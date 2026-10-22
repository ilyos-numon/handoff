"""MVP API E2E: eval CLI, restart persistence, time-over, invalid requests."""

import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROLE = "backend_developer"
ANSWER_MAIN = "금요일 배포는 피합니다. 작년 3월 금요일 저녁 배포 후 정산 배치가 주말 내내 실패했습니다."
ANSWER_FOLLOW = "복구는 큐를 비우고 정산 배치를 수동으로 재실행했습니다."
QUERY_HIT = "금요일에 배포하면 왜 안 되나요?"

StartServer = Callable[..., Any]


def test_eval_cli_runs_ten_sessions_fake(start_server: StartServer, tmp_path: Path) -> None:
    server = start_server(tmp_path / "data", {})
    out_dir = tmp_path / "out"
    proc = subprocess.run(
        [
            sys.executable, "-m", "handoff.evaluation",
            "--base-url", server.url,
            "--out-dir", str(out_dir),
            "--name", "fake",
        ],
        check=False,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    result = json.loads((out_dir / "fake.json").read_text(encoding="utf-8"))
    assert len(result["sessions"]) == 10
    assert len(result["rows"]) == 20
    c = result["counts"]
    assert c["good"] + c["fair"] + c["wrong"] == 20
    assert c["total"] == 20
    assert result["coverage"]["sessions_done"] == 10

    md = (out_dir / "fake.md").read_text(encoding="utf-8")
    assert len([ln for ln in md.splitlines() if ln.startswith("| q")]) == 20

    with httpx.Client(base_url=server.url, timeout=10) as client:
        resp = client.post("/sessions", json={"role": ROLE, "leaver": "eval"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "session limit reached"


def test_restart_keeps_number_coverage_and_citation_session(
    start_server: StartServer, tmp_path: Path
) -> None:
    data_dir = tmp_path / "data"
    first = start_server(data_dir, {})
    with httpx.Client(base_url=first.url, timeout=10) as client:
        start = client.post("/sessions", json={"role": ROLE})
        assert start.status_code == 201
        sid = start.json()["session_id"]
        for text in (ANSWER_MAIN, ANSWER_FOLLOW):
            assert client.post(f"/sessions/{sid}/answers", json={"text": text}).status_code == 200
        end = client.post(f"/sessions/{sid}/end")
        assert end.status_code == 200
        assert len(end.json()["summary"]) == 3
    first.stop()

    second = start_server(data_dir, {})
    with httpx.Client(base_url=second.url, timeout=10) as client:
        cov = client.get("/coverage", params={"leaver": "default", "role": ROLE})
        assert cov.status_code == 200
        systems = next(a for a in cov.json()["axes"] if a["axis"] == "systems")
        assert systems["covered"] == 1

        q = client.post("/kb/query", json={"question": QUERY_HIT})
        assert q.status_code == 200
        body = q.json()
        assert body["found"] is True
        assert body["citations"][0]["session_number"] == 1

        nxt = client.post("/sessions", json={"role": ROLE})
        assert nxt.status_code == 201
        assert nxt.json()["number"] == 2
        assert nxt.json()["question"]["id"] == "rou-01"


def test_time_over_rejects_late_answer(start_server: StartServer, tmp_path: Path) -> None:
    server = start_server(
        tmp_path / "data",
        {"HANDOFF_SESSION_SECONDS": "2", "HANDOFF_SESSION_GRACE_SECONDS": "0"},
    )
    with httpx.Client(base_url=server.url, timeout=10) as client:
        start = client.post("/sessions", json={"role": ROLE})
        assert start.status_code == 201
        sid = start.json()["session_id"]

        time.sleep(3)  # explicit: let the 2s session deadline pass

        late = client.post(f"/sessions/{sid}/answers", json={"text": ANSWER_MAIN})
        assert late.status_code == 409
        assert late.json()["detail"] == "session time over"

        end = client.post(f"/sessions/{sid}/end")
        assert end.status_code == 200
        assert end.json()["ingested"] == 0


def test_invalid_requests_and_missing_key(start_server: StartServer, tmp_path: Path) -> None:
    server = start_server(tmp_path / "data", {})
    with httpx.Client(base_url=server.url, timeout=10) as client:
        bad = client.post("/sessions", json={"role": ROLE, "leaver": "Bad Name!"})
        assert bad.status_code == 422
        assert client.get("/sessions/nope").status_code == 404
        unknown = client.get("/coverage", params={"role": "unknown_role"})
        assert unknown.status_code == 404

    with pytest.raises(RuntimeError) as info:
        start_server(tmp_path / "data-qwen", {"HANDOFF_PROVIDER": "qwen"})
    assert "HANDOFF_LLM_API_KEY" in getattr(info.value, "stderr", "")
