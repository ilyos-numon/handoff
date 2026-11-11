"""Repeat the evaluations N times (``python -m handoff.evaluation_repeat``).

LLM answers are not deterministic, so one evaluation run is one sample. This
tool starts a fresh server on an empty data directory per run, runs the base,
loop and honesty evaluations against it, stops the server and reports each
metric as mean / min / max / runs meeting its target.
"""

import argparse
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
import yaml
from pydantic import BaseModel, ValidationError

from handoff.evaluation import (
    EvalError,
    EvalResult,
    MockLeaver,
    SuccessorQuestion,
    load_mock,
    load_questions,
    run_eval,
)
from handoff.evaluation_honesty import (
    HONESTY_TARGETS,
    HonestyError,
    HonestyFixture,
    HonestyMetrics,
    HonestyResult,
    run_honesty_eval,
)
from handoff.evaluation_honesty import load_fixture as load_honesty_fixture
from handoff.evaluation_loop import (
    FLOAT_TARGETS,
    LoopError,
    LoopFixture,
    LoopResult,
    run_loop_eval,
)
from handoff.evaluation_loop import load_fixture as load_loop_fixture

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUITES: tuple[str, ...] = ("base", "loop", "honesty")
MIN_RUNS = 1
MAX_RUNS = 10
READY_TIMEOUT_S = 60.0
POLL_INTERVAL_S = 0.2
STOP_TIMEOUT_S = 10.0
HTTP_TIMEOUT_S = 120.0
BASE_LEAVER = "eval"
HONESTY_LEAVER = "honesty"

Kind = Literal["min", "max"]
Target = tuple[Kind, float]
# A launcher gets the run's empty data directory and yields the server base URL.
Launcher = Callable[[Path], AbstractContextManager[str]]

METRIC_TARGETS: dict[str, Target | None] = {
    "base.answered": None,
    "base.cited_ok": ("min", 0.857),
    "base.fabricated": ("max", 0),
    "base.max_session_seconds": None,
    **{f"loop.{k}": ("min", v) for k, v in FLOAT_TARGETS.items()},
    "loop.loop_closed": ("min", 1.0),
    "loop.drill_ok": ("min", 1.0),
    "loop.pii_leaks": ("max", 0),
    **{f"honesty.{k}": v for k, v in HONESTY_TARGETS.items()},
}


class ServerStartError(RuntimeError):
    """The server process exited or never answered ``/health`` with 200."""


class MetricStats(BaseModel):
    mean: float
    min: float
    max: float
    met: int | None
    runs: int
    target: float | None
    kind: Kind | None


class RunRecord(BaseModel):
    run: int
    values: dict[str, float]
    error: str | None
    elapsed_seconds: float


class RepeatReport(BaseModel):
    name: str
    runs: int
    ok: int
    suites: list[str]
    generated_at: datetime
    stats: dict[str, MetricStats]
    per_run: list[RunRecord]


# ---- metrics ------------------------------------------------------------------


def _rate(n: int, m: int) -> float:
    return round(n / m, 3) if m else 0.0


def flatten(suite_results: Mapping[str, BaseModel]) -> dict[str, float]:
    """Turn suite results (keys ``base``/``loop``/``honesty``) into flat metric values."""
    values: dict[str, float] = {}
    base = suite_results.get("base")
    if isinstance(base, EvalResult):
        c = base.counts
        values["base.answered"] = _rate(c.answered, c.total)
        values["base.cited_ok"] = _rate(c.cited_ok, c.covered)
        values["base.fabricated"] = float(c.fabricated)
        values["base.max_session_seconds"] = float(c.max_session_seconds)
    loop = suite_results.get("loop")
    if isinstance(loop, LoopResult):
        lm = loop.metrics
        for key in FLOAT_TARGETS:
            values[f"loop.{key}"] = float(getattr(lm, key))
        values["loop.loop_closed"] = _rate(lm.loop_closed.n, lm.loop_closed.m)
        values["loop.drill_ok"] = _rate(lm.drill_ok.n, lm.drill_ok.m)
        values["loop.pii_leaks"] = float(lm.pii_leaks)
    honesty = suite_results.get("honesty")
    if isinstance(honesty, HonestyResult):
        for key in HonestyMetrics.model_fields:
            values[f"honesty.{key}"] = float(getattr(honesty.metrics, key))
    return values


def meets(value: float, target: Target) -> bool:
    kind, bound = target
    return value >= bound if kind == "min" else value <= bound


def _metric_order(names: set[str]) -> list[str]:
    known = [k for k in METRIC_TARGETS if k in names]
    return known + sorted(names - set(METRIC_TARGETS))


def aggregate(per_run: list[RunRecord]) -> dict[str, MetricStats]:
    """Per-metric statistics over runs without an error."""
    ok_runs = [r for r in per_run if r.error is None]
    names: set[str] = set()
    for r in ok_runs:
        names.update(r.values)
    stats: dict[str, MetricStats] = {}
    for name in _metric_order(names):
        vals = [r.values[name] for r in ok_runs if name in r.values]
        target = METRIC_TARGETS.get(name)
        stats[name] = MetricStats(
            mean=round(sum(vals) / len(vals), 3),
            min=min(vals),
            max=max(vals),
            met=None if target is None else sum(1 for v in vals if meets(v, target)),
            runs=len(vals),
            target=None if target is None else float(target[1]),
            kind=None if target is None else target[0],
        )
    return stats


def unmet_mean(stats: dict[str, MetricStats]) -> list[str]:
    """Metrics whose mean misses the target (min: mean < target, max: mean > target)."""
    return [
        name
        for name, s in stats.items()
        if s.kind is not None and s.target is not None and not meets(s.mean, (s.kind, s.target))
    ]


def all_met_runs(per_run: list[RunRecord]) -> int:
    count = 0
    for r in per_run:
        if r.error is not None:
            continue
        targets = [(v, METRIC_TARGETS.get(k)) for k, v in r.values.items()]
        if all(meets(v, t) for v, t in targets if t is not None):
            count += 1
    return count


# ---- report -------------------------------------------------------------------


def _num(value: float) -> str:
    return f"{round(value, 3):g}"


def _target_text(s: MetricStats) -> str:
    if s.kind is None or s.target is None:
        return "-"
    return f"{'≥' if s.kind == 'min' else '≤'} {_num(s.target)}"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def summary_line(report: RepeatReport) -> str:
    unmet = unmet_mean(report.stats)
    return (
        f"REPEAT runs={report.runs} ok={report.ok} "
        f"all_met={all_met_runs(report.per_run)}/{report.ok} "
        f"unmet_mean={','.join(unmet) if unmet else '-'}"
    )


def render_markdown(report: RepeatReport) -> str:
    lines = [
        f"# 평가 {report.runs}회 집계 — {report.name}",
        "",
        f"- 생성: {report.generated_at.isoformat(timespec='seconds')}",
        f"- 묶음: {', '.join(report.suites)}",
        f"- 회차: {report.runs} (오류 없이 끝난 회차 {report.ok})",
        f"- 모든 목표 충족 회차: {all_met_runs(report.per_run)}/{report.ok}",
        "",
        "## 지표",
        "",
        "| 지표 | 목표 | 평균 | 최소 | 최대 | 충족 회차 |",
        "|---|---|---|---|---|---|",
    ]
    for name, s in report.stats.items():
        met = "-" if s.met is None else f"{s.met}/{s.runs}"
        lines.append(
            f"| {name} | {_target_text(s)} | {_num(s.mean)} | {_num(s.min)} | {_num(s.max)} | {met} |"
        )
    if not report.stats:
        lines.append("| - | - | - | - | - | - |")
    lines += ["", "## 회차별", ""]
    header = ["지표"] + [f"{r.run}회" for r in report.per_run]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    lines.append(
        "| 경과(초) | " + " | ".join(_num(r.elapsed_seconds) for r in report.per_run) + " |"
    )
    for name in report.stats:
        cells = [
            "-" if r.error is not None or name not in r.values else _num(r.values[name])
            for r in report.per_run
        ]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += ["", "## 오류", ""]
    errors = [r for r in report.per_run if r.error is not None]
    if errors:
        lines += [f"- {r.run}회: {_cell(r.error or '')}" for r in errors]
    else:
        lines.append("- 없음")
    return "\n".join(lines) + "\n"


def write_reports(report: RepeatReport, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = json.loads(report.model_dump_json())
    (out_dir / f"{report.name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md_path = out_dir / f"{report.name}.md"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return md_path


# ---- server -------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("localhost", 0))
        return int(sock.getsockname()[1])


def _stop(proc: "subprocess.Popen[bytes]") -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@contextlib.contextmanager
def launch_server(data_dir: Path) -> Iterator[str]:
    """Start uvicorn on a free port with ``HANDOFF_DATA_DIR=data_dir``; always stop it."""
    port = _free_port()
    env = {**os.environ, "HANDOFF_DATA_DIR": str(data_dir), "HANDOFF_AUTH": "off"}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "handoff.main:create_app",
            "--factory",
            "--host",
            "localhost",
            "--port",
            str(port),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://localhost:{port}"
    try:
        deadline = time.monotonic() + READY_TIMEOUT_S
        ready = False
        while time.monotonic() < deadline and proc.poll() is None:
            try:
                if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                    ready = True
                    break
            except httpx.TransportError:
                pass
            time.sleep(POLL_INTERVAL_S)
        if not ready:
            raise ServerStartError(f"server did not become ready on {url}")
        yield url
    finally:
        _stop(proc)


# ---- runs ---------------------------------------------------------------------


@dataclass
class Fixtures:
    mock: MockLeaver | None = None
    questions: list[SuccessorQuestion] = field(default_factory=list)
    loop: LoopFixture | None = None
    honesty: HonestyFixture | None = None


def load_fixtures(fixtures_dir: Path, suites: list[str]) -> Fixtures:
    fx = Fixtures()
    if "base" in suites:
        fx.mock = load_mock(fixtures_dir / "mock_leaver.yaml")
        fx.questions = load_questions(fixtures_dir / "successor_questions.yaml")
    if "loop" in suites:
        fx.loop = load_loop_fixture(fixtures_dir / "loop_eval.yaml")
    if "honesty" in suites:
        fx.honesty = load_honesty_fixture(fixtures_dir / "honesty_eval.yaml")
    return fx


def _run_suites(
    client: httpx.Client, suites: list[str], fx: Fixtures, started: float, run_timeout: float
) -> tuple[dict[str, BaseModel], str | None]:
    results: dict[str, BaseModel] = {}
    for suite in suites:
        try:
            if suite == "base" and fx.mock is not None:
                results[suite] = run_eval(client, fx.mock, fx.questions, leaver=BASE_LEAVER)
            elif suite == "loop" and fx.loop is not None:
                results[suite] = run_loop_eval(client, fx.loop, leaver=BASE_LEAVER)
            elif suite == "honesty" and fx.honesty is not None:
                results[suite] = run_honesty_eval(client, fx.honesty, leaver=HONESTY_LEAVER)
        except (EvalError, LoopError, HonestyError) as exc:
            return results, f"{suite}:{exc}"
        except httpx.HTTPError as exc:
            return results, f"{suite}:{type(exc).__name__}"
        if time.monotonic() - started > run_timeout:
            return results, "timeout"
    return results, None


def run_once(
    index: int,
    suites: list[str],
    fx: Fixtures,
    launcher: Launcher,
    run_timeout: float,
    keep_data: bool,
) -> RunRecord:
    data_dir = Path(tempfile.mkdtemp(prefix=f"handoff-repeat-{index}-"))
    started = time.monotonic()
    results: dict[str, BaseModel] = {}
    error: str | None
    try:
        try:
            with launcher(data_dir) as url, httpx.Client(
                base_url=url, timeout=HTTP_TIMEOUT_S
            ) as client:
                results, error = _run_suites(client, suites, fx, started, run_timeout)
        except ServerStartError:
            error = "server"
    finally:
        if not keep_data:
            shutil.rmtree(data_dir, ignore_errors=True)
    elapsed = time.monotonic() - started
    if error is None and elapsed > run_timeout:
        error = "timeout"
    if keep_data:
        print(f"data: run {index} {data_dir}")
    return RunRecord(
        run=index,
        values=flatten(results) if error is None else {},
        error=error,
        elapsed_seconds=round(elapsed, 1),
    )


# ---- cli ----------------------------------------------------------------------


def _runs(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid int: {text}") from None
    if not MIN_RUNS <= value <= MAX_RUNS:
        raise argparse.ArgumentTypeError(f"runs must be {MIN_RUNS}..{MAX_RUNS}")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m handoff.evaluation_repeat")
    parser.add_argument("--runs", required=True, type=_runs)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--suites", default=",".join(SUITES))
    parser.add_argument("--fixtures-dir", default=Path("eval/fixtures"), type=Path)
    parser.add_argument("--run-timeout", default=2400.0, type=float)
    parser.add_argument("--keep-data", action="store_true")
    parser.add_argument("--require-mean-targets", default="")
    return parser


def _parse_suites(parser: argparse.ArgumentParser, text: str) -> list[str]:
    chosen = [s.strip() for s in text.split(",") if s.strip()]
    unknown = [s for s in chosen if s not in SUITES]
    if not chosen or unknown:
        parser.error(f"--suites must be a subset of {','.join(SUITES)}")
    if "loop" in chosen and "base" not in chosen:
        parser.error("--suites loop requires base")
    return [s for s in SUITES if s in chosen]


def main(argv: list[str] | None = None, launcher: Launcher | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    suites = _parse_suites(parser, args.suites)
    prefixes = [p.strip() for p in args.require_mean_targets.split(",") if p.strip()]
    try:
        fx = load_fixtures(args.fixtures_dir, suites)
    except (OSError, KeyError, TypeError, yaml.YAMLError, ValidationError):
        print("REPEAT ERROR fixtures")
        return 2
    run_launcher: Launcher = launcher or launch_server
    per_run: list[RunRecord] = []
    for index in range(1, args.runs + 1):
        record = run_once(index, suites, fx, run_launcher, args.run_timeout, args.keep_data)
        per_run.append(record)
        status = record.error or "ok"
        print(f"run {index}/{args.runs}: {status} ({record.elapsed_seconds}s)")
    ok = sum(1 for r in per_run if r.error is None)
    report = RepeatReport(
        name=args.name,
        runs=args.runs,
        ok=ok,
        suites=suites,
        generated_at=datetime.now(UTC),
        stats=aggregate(per_run),
        per_run=per_run,
    )
    md_path = write_reports(report, args.out_dir)
    print(f"report: {md_path}")
    print(summary_line(report))
    if ok == 0:
        return 2
    unmet = unmet_mean(report.stats)
    if any(name.startswith(p) for name in unmet for p in prefixes):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
