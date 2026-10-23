"""Knowledge loop API E2E (fake provider, real uvicorn subprocess, httpx)."""

import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROLE = "backend_developer"
QUERY_HIT = "금요일에 배포하면 왜 안 되나요?"
ANSWER_MAIN = "금요일 배포는 피합니다. 작년 3월 금요일 저녁 배포 후 정산 배치가 주말 내내 실패했습니다."
LOOP_QUESTION = "금요일 저녁에 배포를 하면 왜 안 되나요?"
LOOP_ANSWER = "금요일 저녁에 배포를 하면 주말 장애를 늦게 알아서 안 됩니다. 월요일 오전에 배포합니다."
DECISION_ANSWER = "수동 승인 대신 자동 롤백을 선택했습니다. 배포 후 승인 서버 상태를 먼저 확인합니다."
NO_MORE = "추가로 기억나는 것은 없습니다."
FORBIDDEN_KEYS = {"score", "person", "owner", "rank", "grade"}

StartServer = Callable[..., Any]


def _keys(obj: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.add(k)
            found |= _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= _keys(v)
    return found


def _ok(resp: httpx.Response, code: int = 200) -> Any:
    assert resp.status_code == code, resp.text
    return resp.json()


def _seed_cards(client: httpx.Client, leaver: str) -> tuple[str, list[dict[str, Any]]]:
    """Ingest mock sources, run one finished session with substantive answers, extract cards."""
    _ok(client.post(f"/leavers/{leaver}/sources/mock"), 201)
    _ok(client.post(f"/leavers/{leaver}/questions/generate", json={"role": ROLE}))
    start = _ok(client.post("/sessions", json={"role": ROLE, "leaver": leaver}), 201)
    sid = start["session_id"]
    question = start["question"]
    texts = [ANSWER_MAIN, DECISION_ANSWER, NO_MORE, NO_MORE, NO_MORE, NO_MORE]
    for text in texts:
        if question is None:
            break
        body = _ok(client.post(f"/sessions/{sid}/answers", json={"text": text}))
        question = body["next_question"]
    _ok(client.post(f"/sessions/{sid}/end"))
    cards = _ok(client.get(f"/leavers/{leaver}/cards"))
    return sid, cards


def test_generated_question_flow_to_answered_queue_cards_depth(
    start_server: StartServer, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    with httpx.Client(base_url=server.url, timeout=15) as client:
        leaver = "leaver1"
        docs = _ok(client.post(f"/leavers/{leaver}/sources/mock"), 201)
        assert len(docs) >= 8
        gen = _ok(client.post(f"/leavers/{leaver}/questions/generate", json={"role": ROLE}))
        assert gen["entities"] and gen["queued"]
        queued_ids = {q["id"] for q in gen["queued"]}

        start = _ok(client.post("/sessions", json={"role": ROLE, "leaver": leaver}), 201)
        sid = start["session_id"]
        first = start["question"]
        assert first["kind"] == "queued"
        assert first["id"] in queued_ids

        body = _ok(client.post(f"/sessions/{sid}/answers", json={"text": ANSWER_MAIN}))
        follow = body["next_question"]
        assert follow is not None
        assert follow["kind"] == "follow_up"
        assert follow["id"] == f"{first['id']}-s1"

        _ok(client.post(f"/sessions/{sid}/end"))

        answered = _ok(client.get(f"/leavers/{leaver}/queue", params={"status": "answered"}))
        assert first["id"] in {q["id"] for q in answered}
        cards = _ok(client.get(f"/leavers/{leaver}/cards"))
        assert len(cards) >= 1
        assert all(c["evidence"] for c in cards)
        depth = _ok(client.get(f"/sessions/{sid}/depth"))
        assert len(depth["judgements"]) >= 1
        assert 0.0 <= depth["slot_fill_rate"] <= 1.0


def test_kb_ask_unanswered_then_answered_in_session_is_found_with_item_citation(
    start_server: StartServer, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    with httpx.Client(base_url=server.url, timeout=15) as client:
        leaver = "leaver2"
        asked = _ok(client.post("/kb/ask", json={"question": LOOP_QUESTION, "leaver": leaver}))
        assert asked["answer"]["found"] is False
        item = asked["queued"]
        assert item is not None and re.fullmatch(r"q\d+", item["id"])
        listed = _ok(client.get(f"/leavers/{leaver}/successor-questions"))
        assert item["id"] in {q["id"] for q in listed}
        pending = _ok(client.get(f"/leavers/{leaver}/queue", params={"source": "successor"}))
        assert item["id"] in {q["id"] for q in pending}

        start = _ok(client.post("/sessions", json={"role": ROLE, "leaver": leaver}), 201)
        sid = start["session_id"]
        question = start["question"]
        assert question["kind"] == "queued"
        assert question["id"] == item["id"]
        for _ in range(10):
            if question is None or question["kind"] not in ("queued", "follow_up"):
                break
            text = LOOP_ANSWER if question["kind"] == "queued" else NO_MORE
            question = _ok(client.post(f"/sessions/{sid}/answers", json={"text": text}))[
                "next_question"
            ]
        _ok(client.post(f"/sessions/{sid}/end"))

        found = _ok(client.post("/kb/query", json={"question": LOOP_QUESTION}))
        assert found["found"] is True
        assert any(
            re.match(r"^(q\d+)", c["question_id"]).group(1) == item["id"]  # type: ignore[union-attr]
            for c in found["citations"]
        )
        done = _ok(client.get(f"/leavers/{leaver}/queue", params={"status": "answered"}))
        assert item["id"] in {q["id"] for q in done}


def test_gap_refresh_reports_mentioned_only_without_person_fields(
    start_server: StartServer, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    with httpx.Client(base_url=server.url, timeout=15) as client:
        leaver = "leaver3"
        _seed_cards(client, leaver)
        report = _ok(client.post(f"/leavers/{leaver}/gaps/refresh", params={"role": ROLE}))
        kinds = [g["kind"] for g in report["gaps"]]
        assert kinds.count("mentioned_only") >= 1
        assert not (_keys(report) & FORBIDDEN_KEYS)
        again = _ok(client.get(f"/leavers/{leaver}/gaps"))
        assert not (_keys(again) & FORBIDDEN_KEYS)
        assert len(again["gaps"]) == len(report["gaps"])


def test_artifacts_four_kinds_cite_valid_cards_and_no_cards_conflict(
    start_server: StartServer, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    with httpx.Client(base_url=server.url, timeout=15) as client:
        leaver = "leaver4"
        _, cards = _seed_cards(client, leaver)
        valid = {c["id"] for c in cards}
        assert valid
        for kind in ("runbook", "onboarding_30d", "pitfalls_top10", "adr"):
            art = _ok(client.post(f"/leavers/{leaver}/artifacts/{kind}"))
            assert art["kind"] == kind
            assert art["card_ids"], kind
            assert set(art["card_ids"]) <= valid, kind
            assert art["markdown"].strip()
            latest = _ok(client.get(f"/leavers/{leaver}/artifacts/{kind}"))
            assert latest["id"] == art["id"]

        empty = client.post("/leavers/nobody/artifacts/runbook")
        assert empty.status_code == 409
        assert empty.json()["detail"] == "no cards"
        missing = client.get("/leavers/nobody/artifacts/runbook")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "artifact not found"
        assert client.post(f"/leavers/{leaver}/artifacts/bogus").status_code == 422


def test_drill_weak_answer_missed_then_first_point_covered(
    start_server: StartServer, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    with httpx.Client(base_url=server.url, timeout=15) as client:
        leaver = "leaver5"
        _, cards = _seed_cards(client, leaver)
        assert cards
        drill = _ok(client.post(f"/leavers/{leaver}/drills", json={}), 201)
        assert drill["expected_points"]
        assert not (_keys(drill) & {"score", "rank", "grade"})
        fetched = _ok(client.get(f"/drills/{drill['id']}"))
        assert fetched["id"] == drill["id"]

        weak = _ok(
            client.post(f"/drills/{drill['id']}/answers", json={"text": "잘 모르겠습니다"})
        )
        assert len(weak["missed_points"]) >= 1
        assert not (_keys(weak) & {"score", "rank", "grade"})

        good = _ok(
            client.post(
                f"/drills/{drill['id']}/answers", json={"text": drill["expected_points"][0]}
            )
        )
        assert drill["expected_points"][0] in good["covered_points"]

        assert client.get("/drills/d999999").status_code == 404
        bad = client.post(f"/drills/{drill['id']}/answers", json={"text": "   "})
        assert bad.status_code == 422
        none = client.post("/leavers/nobody/drills", json={})
        assert none.status_code == 409


def test_query_hit_has_claims_removed_and_verify_off_gives_zero(
    start_server: StartServer, tmp_path: Path
) -> None:
    on = start_server(tmp_path / "on", {})
    off = start_server(tmp_path / "off", {"HANDOFF_VERIFY_CLAIMS": "off"})
    for server, expect_zero in ((on, False), (off, True)):
        with httpx.Client(base_url=server.url, timeout=15) as client:
            start = _ok(client.post("/sessions", json={"role": ROLE}), 201)
            sid = start["session_id"]
            _ok(client.post(f"/sessions/{sid}/answers", json={"text": ANSWER_MAIN}))
            _ok(client.post(f"/sessions/{sid}/end"))
            ans = _ok(client.post("/kb/query", json={"question": QUERY_HIT}))
            assert ans["found"] is True
            assert "claims_removed" in ans
            assert isinstance(ans["claims_removed"], int)
            if expect_zero:
                assert ans["claims_removed"] == 0


def test_evaluation_loop_cli_after_base_evaluation(
    start_server: StartServer, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    out_dir = tmp_path / "out"
    base = subprocess.run(
        [
            sys.executable, "-m", "handoff.evaluation",
            "--base-url", server.url, "--out-dir", str(out_dir), "--name", "fake",
        ],
        check=False, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=240,
    )
    assert base.returncode == 0, base.stdout + base.stderr
    loop = subprocess.run(
        [
            sys.executable, "-m", "handoff.evaluation_loop",
            "--base-url", server.url, "--out-dir", str(out_dir), "--name", "fakeloop",
        ],
        check=False, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=240,
    )
    assert loop.returncode == 0, loop.stdout + loop.stderr
    lines = [ln for ln in loop.stdout.splitlines() if ln.startswith("LOOP ")]
    assert lines, loop.stdout
    assert "pii_leaks=0" in lines[-1]
    result = json.loads((out_dir / "fakeloop.json").read_text(encoding="utf-8"))
    assert result["metrics"]["pii_leaks"] == 0
    assert (out_dir / "fakeloop.md").exists()


def test_depth_judge_spec_example_and_blank_answer(
    start_server: StartServer, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    answer = "배포 전에 변경 목록을 확인합니다. 장애가 잦았기 때문입니다."
    with httpx.Client(base_url=server.url, timeout=15) as client:
        judged = _ok(
            client.post("/depth/judge", json={"question": "배포는 어떻게 하나요?", "answer": answer})
        )
        assert judged["slots"]["how"] == "filled"
        assert judged["slots"]["why"] == "filled"
        assert judged["missing"][0] == "when"
        assert set(judged["slots"]) == {"why", "when", "who", "how", "fallback"}

        follow = _ok(
            client.post(
                "/depth/follow-up", json={"question": "배포는 어떻게 하나요?", "answer": answer}
            )
        )
        assert follow["slot"] == "when"
        assert "언제" in follow["text"]

        blank = client.post("/depth/judge", json={"question": "배포는?", "answer": "   "})
        assert blank.status_code == 422
        nosession = client.get("/sessions/does-not-exist/depth")
        assert nosession.status_code == 404
        assert nosession.json()["detail"] == "session not found"


def test_invalid_leaver_and_no_sources_rejected(
    start_server: StartServer, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    with httpx.Client(base_url=server.url, timeout=15) as client:
        assert client.get("/leavers/BAD_NAME/gaps").status_code == 422
        resp = client.post("/leavers/nobody/questions/generate", json={"role": ROLE})
        assert resp.status_code == 409
        assert resp.json()["detail"] == "no sources"
        bad_src = client.post(
            "/leavers/leaver9/sources", json={"kind": "wiki", "title": "t", "text": ""}
        )
        assert bad_src.status_code == 422
