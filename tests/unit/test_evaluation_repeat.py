import contextlib
import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from handoff import evaluation_repeat as rep
from handoff.evaluation import EvalCounts, EvalError, EvalResult
from handoff.evaluation_honesty import HonestyError, HonestyMetrics, HonestyResult
from handoff.evaluation_loop import LoopMetrics, LoopResult, Ratio
from handoff.evaluation_repeat import (
    METRIC_TARGETS,
    RepeatReport,
    RunRecord,
    ServerStartError,
    aggregate,
    flatten,
    main,
    render_markdown,
)

FIXTURES = Path(__file__).resolve().parents[2] / "eval" / "fixtures"


def _base(
    answered: int = 7, total: int = 8, cited_ok: int = 6, covered: int = 7, fabricated: int = 0
) -> EvalResult:
    counts = EvalCounts(
        total=total,
        covered=covered,
        answered=answered,
        cited_ok=cited_ok,
        good=0,
        fair=0,
        wrong=0,
        fabricated=fabricated,
        max_session_seconds=3,
    )
    return EvalResult.model_construct(counts=counts)


def _loop(closed: tuple[int, int] = (3, 3), drill: tuple[int, int] = (2, 2)) -> LoopResult:
    metrics = LoopMetrics(
        depth_agree=1.0,
        slot_target=0.9,
        slot_fill=0.8,
        card_recall=0.75,
        card_evidence=1.0,
        gap_recall=0.6,
        loop_closed=Ratio(n=closed[0], m=closed[1]),
        artifact_cited=1.0,
        drill_ok=Ratio(n=drill[0], m=drill[1]),
        pii_leaks=0,
    )
    return LoopResult.model_construct(metrics=metrics)


def _honesty(fence_accuracy: float = 1.0, fabricated: int = 0) -> HonestyResult:
    metrics = HonestyMetrics(
        fence_accuracy=fence_accuracy,
        fence_fabricated=fabricated,
        uncertain_detect=1.0,
        tacit_followup=1.0,
        routing=1.0,
        at_risk=1.0,
        premortem_cited=1.0,
        pii_leaks=0,
        surveillance_keys=0,
    )
    return HonestyResult.model_construct(metrics=metrics)


def _rec(run: int, values: dict[str, float], error: str | None = None) -> RunRecord:
    return RunRecord(run=run, values=values, error=error, elapsed_seconds=1.0)


class FakeLauncher:
    def __init__(self, fail_runs: set[int] | None = None) -> None:
        self.fail_runs = fail_runs or set()
        self.calls = 0
        self.dirs: list[Path] = []
        self.existed: list[bool] = []
        self.stopped = 0

    def __call__(self, data_dir: Path) -> contextlib.AbstractContextManager[str]:
        @contextlib.contextmanager
        def cm() -> Iterator[str]:
            self.calls += 1
            self.dirs.append(data_dir)
            self.existed.append(data_dir.is_dir())
            if self.calls in self.fail_runs:
                raise ServerStartError("no health")
            try:
                yield "http://fake.invalid"
            finally:
                self.stopped += 1

        return cm()


@pytest.fixture
def runners(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"calls": [], "honesty": [], "fail": {}}

    def fake_eval(client: Any, mock: Any, questions: Any, leaver: str = "eval") -> EvalResult:
        state["calls"].append(("base", leaver))
        if "base" in state["fail"] and state["fail"]["base"](len(state["calls"])):
            raise EvalError("query", 500)
        return _base()

    def fake_loop(client: Any, fixture: Any, leaver: str = "eval") -> LoopResult:
        state["calls"].append(("loop", leaver))
        return _loop()

    def fake_honesty(client: Any, fixture: Any, leaver: str = "honesty") -> HonestyResult:
        state["calls"].append(("honesty", leaver))
        n = len([c for c in state["calls"] if c[0] == "honesty"])
        if "honesty" in state["fail"] and state["fail"]["honesty"](n):
            raise HonestyError("fence", 503)
        values = state["honesty"]
        return values[n - 1] if n - 1 < len(values) else _honesty()

    monkeypatch.setattr(rep, "run_eval", fake_eval)
    monkeypatch.setattr(rep, "run_loop_eval", fake_loop)
    monkeypatch.setattr(rep, "run_honesty_eval", fake_honesty)
    return state


def _argv(tmp_path: Path, *extra: str, runs: str = "2") -> list[str]:
    return [
        "--runs",
        runs,
        "--out-dir",
        str(tmp_path / "out"),
        "--name",
        "rep",
        "--fixtures-dir",
        str(FIXTURES),
        *extra,
    ]


def _report(tmp_path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((tmp_path / "out" / "rep.json").read_text(encoding="utf-8"))
    return data


# ---- flatten ------------------------------------------------------------------


def test_flatten_all_suites_has_every_target_metric() -> None:
    values = flatten({"base": _base(), "loop": _loop(), "honesty": _honesty()})
    assert set(values) == set(METRIC_TARGETS)
    assert values["base.answered"] == 0.875
    assert values["base.cited_ok"] == 0.857
    assert values["base.fabricated"] == 0.0
    assert values["base.max_session_seconds"] == 3.0
    assert values["loop.loop_closed"] == 1.0
    assert values["loop.card_recall"] == 0.75
    assert values["honesty.fence_accuracy"] == 1.0


def test_flatten_zero_denominators_are_zero() -> None:
    values = flatten(
        {"base": _base(answered=0, total=0, cited_ok=0, covered=0), "loop": _loop((0, 0), (0, 0))}
    )
    assert values["base.answered"] == 0.0
    assert values["base.cited_ok"] == 0.0
    assert values["loop.loop_closed"] == 0.0
    assert values["loop.drill_ok"] == 0.0
    assert not any(k.startswith("honesty.") for k in values)


# ---- aggregate ----------------------------------------------------------------


def test_aggregate_mean_min_max_met_for_min_target() -> None:
    per_run = [
        _rec(1, {"honesty.routing": 1.0}),
        _rec(2, {"honesty.routing": 0.5}),
        _rec(3, {"honesty.routing": 0.75}),
    ]
    s = aggregate(per_run)["honesty.routing"]
    assert (s.mean, s.min, s.max, s.met, s.runs) == (0.75, 0.5, 1.0, 2, 3)
    assert (s.kind, s.target) == ("min", 0.75)


def test_aggregate_max_target_counts_runs_at_or_below() -> None:
    per_run = [_rec(1, {"base.fabricated": 0.0}), _rec(2, {"base.fabricated": 1.0})]
    s = aggregate(per_run)["base.fabricated"]
    assert s.kind == "max"
    assert s.met == 1
    assert s.mean == 0.5
    assert rep.unmet_mean(aggregate(per_run)) == ["base.fabricated"]


def test_aggregate_skips_error_runs_and_none_targets() -> None:
    per_run = [
        _rec(1, {"base.answered": 0.5}),
        _rec(2, {}, error="server"),
        _rec(3, {"base.answered": 1.0}),
    ]
    s = aggregate(per_run)["base.answered"]
    assert s.runs == 2
    assert s.met is None and s.target is None and s.kind is None
    assert s.mean == 0.75


def test_render_markdown_has_metric_table_and_errors() -> None:
    per_run = [_rec(1, {"base.fabricated": 0.0}), _rec(2, {}, error="honesty:boom")]
    report = RepeatReport(
        name="x",
        runs=2,
        ok=1,
        suites=["base"],
        generated_at=datetime.now(UTC),
        stats=aggregate(per_run),
        per_run=per_run,
    )
    md = render_markdown(report)
    assert "| 지표 | 목표 | 평균 | 최소 | 최대 | 충족 회차 |" in md
    assert "| base.fabricated | ≤ 0 | 0 | 0 | 0 | 1/1 |" in md
    assert "- 2회: honesty:boom" in md


# ---- main ---------------------------------------------------------------------


def test_main_all_ok_writes_reports_and_summary(
    tmp_path: Path, runners: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    launcher = FakeLauncher()
    assert main(_argv(tmp_path), launcher=launcher) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[-1] == "REPEAT runs=2 ok=2 all_met=2/2 unmet_mean=-"
    assert lines[-2].startswith("report: ")
    data = _report(tmp_path)
    assert data["runs"] == 2 and data["ok"] == 2
    assert data["suites"] == ["base", "loop", "honesty"]
    assert all(s["min"] == s["max"] for s in data["stats"].values())
    assert (tmp_path / "out" / "rep.md").is_file()
    assert runners["calls"][:3] == [("base", "eval"), ("loop", "eval"), ("honesty", "honesty")]
    assert launcher.stopped == 2


def test_main_fresh_data_dir_per_run_and_removed(tmp_path: Path, runners: dict[str, Any]) -> None:
    launcher = FakeLauncher()
    assert main(_argv(tmp_path), launcher=launcher) == 0
    assert launcher.existed == [True, True]
    assert len(set(launcher.dirs)) == 2
    assert all("handoff-repeat-" in d.name for d in launcher.dirs)
    assert not any(d.exists() for d in launcher.dirs)


def test_main_keep_data_leaves_dirs(tmp_path: Path, runners: dict[str, Any]) -> None:
    launcher = FakeLauncher()
    assert main(_argv(tmp_path, "--keep-data", runs="1"), launcher=launcher) == 0
    assert launcher.dirs[0].is_dir()
    launcher.dirs[0].rmdir()


def test_main_run_error_recorded_and_continues(
    tmp_path: Path, runners: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    runners["fail"]["honesty"] = lambda n: n == 1
    launcher = FakeLauncher()
    assert main(_argv(tmp_path), launcher=launcher) == 0
    data = _report(tmp_path)
    assert data["ok"] == 1
    assert data["per_run"][0]["error"] == "honesty:HONESTY ERROR fence status=503"
    assert data["per_run"][0]["values"] == {}
    assert data["per_run"][1]["error"] is None
    assert data["stats"]["honesty.routing"]["runs"] == 1
    assert launcher.stopped == 2
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("REPEAT runs=2 ok=1 ")


def test_main_server_error_recorded(tmp_path: Path, runners: dict[str, Any]) -> None:
    launcher = FakeLauncher(fail_runs={2})
    assert main(_argv(tmp_path), launcher=launcher) == 0
    data = _report(tmp_path)
    assert [r["error"] for r in data["per_run"]] == [None, "server"]


def test_main_all_runs_error_exits_2(tmp_path: Path, runners: dict[str, Any]) -> None:
    runners["fail"]["base"] = lambda n: True
    assert main(_argv(tmp_path), launcher=FakeLauncher()) == 2
    data = _report(tmp_path)
    assert data["ok"] == 0
    assert data["stats"] == {}
    assert all(r["error"] == "base:EVAL ERROR query status=500" for r in data["per_run"])


def test_main_timeout_marks_run(tmp_path: Path, runners: dict[str, Any]) -> None:
    def slow(client: Any, fixture: Any, leaver: str = "honesty") -> HonestyResult:
        time.sleep(0.02)
        return _honesty()

    rep_runs = rep.run_honesty_eval
    try:
        rep.run_honesty_eval = slow  # type: ignore[assignment]
        code = main(
            _argv(tmp_path, "--suites", "honesty", "--run-timeout", "0.01", runs="1"),
            launcher=FakeLauncher(),
        )
    finally:
        rep.run_honesty_eval = rep_runs  # type: ignore[assignment]
    assert code == 2
    assert _report(tmp_path)["per_run"][0]["error"] == "timeout"


@pytest.mark.parametrize("runs", ["0", "11", "x"])
def test_main_runs_out_of_range_exits_2(tmp_path: Path, runs: str) -> None:
    with pytest.raises(SystemExit) as exc:
        main(_argv(tmp_path, runs=runs), launcher=FakeLauncher())
    assert exc.value.code == 2


@pytest.mark.parametrize("suites", ["loop", "loop,honesty", "nope"])
def test_main_bad_suites_exit_2(tmp_path: Path, suites: str) -> None:
    launcher = FakeLauncher()
    with pytest.raises(SystemExit) as exc:
        main(_argv(tmp_path, "--suites", suites), launcher=launcher)
    assert exc.value.code == 2
    assert launcher.calls == 0


def test_main_selected_suites_only(tmp_path: Path, runners: dict[str, Any]) -> None:
    assert main(_argv(tmp_path, "--suites", "honesty", runs="1"), launcher=FakeLauncher()) == 0
    assert runners["calls"] == [("honesty", "honesty")]
    assert all(k.startswith("honesty.") for k in _report(tmp_path)["stats"])


def test_main_require_mean_targets_unmet_exits_1(
    tmp_path: Path, runners: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    runners["honesty"] = [_honesty(fence_accuracy=0.6), _honesty(fence_accuracy=0.8)]
    argv = _argv(tmp_path, "--require-mean-targets", "honesty")
    assert main(argv, launcher=FakeLauncher()) == 1
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last == "REPEAT runs=2 ok=2 all_met=1/2 unmet_mean=honesty.fence_accuracy"


def test_main_unmet_without_matching_prefix_exits_0(
    tmp_path: Path, runners: dict[str, Any]
) -> None:
    runners["honesty"] = [_honesty(fabricated=1), _honesty(fabricated=1)]
    assert main(_argv(tmp_path, "--require-mean-targets", "base.fabricated"),
                launcher=FakeLauncher()) == 0
    assert main(_argv(tmp_path), launcher=FakeLauncher()) == 0


def test_main_fixture_error_exits_2(tmp_path: Path) -> None:
    argv = _argv(tmp_path)
    argv[argv.index("--fixtures-dir") + 1] = str(tmp_path / "missing")
    assert main(argv, launcher=FakeLauncher()) == 2
