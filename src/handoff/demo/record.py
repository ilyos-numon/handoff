"""Record the demo cassette, or verify that it replays, by driving the demo flow in-process.

python -m handoff.demo.record [--out PATH] [--verify]
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from handoff.config import DEFAULT_CASSETTE, load_settings
from handoff.demo.sandbox import SEED_ROLE
from handoff.main import create_app
from handoff.providers_replay import Cassette

KEY_ENV = "HANDOFF_LLM_API_KEY"
ROLE_HEADER = "X-Handoff-As"
ASK_KNOWN = "결제 대행사 장애가 나면 무엇을 먼저 확인하나요?"
ASK_UNKNOWN = "캐시 서버를 재시작해야 할 때는 어떻게 하나요?"
FENCE_SUBJECT = "금요일 배포 금지"
CACHE_ANSWER = "캐시 서버는 트래픽이 적은 새벽에 한 대씩 재시작하고, 재시작 후 적중률을 확인합니다."


class FlowError(RuntimeError):
    """A demo request did not succeed."""


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _call(
    client: TestClient, role: str | None, method: str, path: str, body: Any = None
) -> Any:
    headers = {ROLE_HEADER: role} if role else {}
    res = client.request(method, path, headers=headers, json=body)
    _log(f"{method} {path.split('?')[0]} as {role or '-'} -> {res.status_code}")
    if res.status_code >= 400:
        raise FlowError(f"{method} {path.split('?')[0]} returned {res.status_code}")
    return res.json() if res.content else None


def run_flow(client: TestClient) -> str:
    """POST /demo/start (seed, steps 1-10) then steps 11-18 in docs/demo-scenario.md order."""
    leaver = str(_call(client, None, "POST", "/demo/start")["leaver"])
    lv = f"/leavers/{leaver}"
    _call(client, "successor", "POST", "/kb/ask", {"question": ASK_KNOWN, "leaver": leaver})
    _call(client, "successor", "POST", "/kb/ask", {"question": ASK_UNKNOWN, "leaver": leaver})
    _call(client, "successor", "POST", "/kb/fence", {"leaver": leaver, "subject": FENCE_SUBJECT})
    _call(client, "manager", "GET", f"/coverage?leaver={leaver}&role={SEED_ROLE}")
    _call(client, "manager", "GET", f"{lv}/queue")
    _call(client, "manager", "POST", f"{lv}/gaps/refresh?role={SEED_ROLE}")
    _call(client, "manager", "POST", f"{lv}/artifacts/runbook")
    started = _call(client, "leaver", "POST", "/sessions", {"role": SEED_ROLE, "leaver": leaver})
    session = f"/sessions/{started['session_id']}"
    _call(client, "leaver", "POST", f"{session}/answers", {"text": CACHE_ANSWER})
    _call(client, "leaver", "POST", f"{session}/end")
    _call(client, "successor", "POST", "/kb/ask", {"question": ASK_UNKNOWN, "leaver": leaver})
    return leaver


def _env(
    base: Mapping[str, str], data_dir: Path, out: Path, provider: str
) -> dict[str, str]:
    env = dict(base)
    env.update(
        {
            "HANDOFF_DATA_DIR": str(data_dir),
            "HANDOFF_PROVIDER": provider,
            "HANDOFF_DEMO": "on",
            "HANDOFF_AUTH": "on",
            "HANDOFF_DEMO_CASSETTE": str(out),
        }
    )
    return env


def _record(base: Mapping[str, str], out: Path) -> int:
    if not base.get(KEY_ENV, "").strip():
        _log(f"{KEY_ENV} is required to record")
        return 2
    if out.exists():
        out.unlink()
    with tempfile.TemporaryDirectory(prefix="handoff-record-") as tmp:
        settings = load_settings(_env(base, Path(tmp), out, "record"))
        with TestClient(create_app(settings)) as client:
            run_flow(client)
    print(f"RECORD entries={len(Cassette(out))} path={out}", flush=True)
    return 0


def _verify(base: Mapping[str, str], out: Path) -> int:
    clean = {k: v for k, v in base.items() if k != KEY_ENV}
    with tempfile.TemporaryDirectory(prefix="handoff-verify-") as tmp:
        settings = load_settings(_env(clean, Path(tmp), out, "replay"))
        with TestClient(create_app(settings)) as client:
            run_flow(client)
            llm = _call(client, None, "GET", "/demo/status").get("llm") or {}
    hits = int(llm.get("hits", 0))
    misses = int(llm.get("misses", 0))
    fallbacks = int(llm.get("fake_fallbacks", 0))
    print(f"REPLAY hits={hits} misses={misses} fake_fallbacks={fallbacks}", flush=True)
    return 1 if misses > 0 else 0


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m handoff.demo.record")
    parser.add_argument("--out", type=Path, default=DEFAULT_CASSETTE)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    base: Mapping[str, str] = os.environ if env is None else env
    out: Path = args.out.resolve()
    try:
        return _verify(base, out) if args.verify else _record(base, out)
    except FlowError as exc:
        _log(f"demo flow failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
