"""Stack smoke: interview session -> knowledge base query over real HTTP."""

import httpx

from handoff.kb import NOT_FOUND_MESSAGE

ROLE = "backend_developer"
ANSWER_MAIN = "금요일 배포는 피합니다. 작년 3월 금요일 저녁 배포 후 정산 배치가 주말 내내 실패했습니다."
ANSWER_FOLLOW = "복구는 큐를 비우고 정산 배치를 수동으로 재실행했습니다."
QUERY_HIT = "금요일에 배포하면 왜 안 되나요?"
QUERY_MISS = "PG사 장애 나면 누구한테 연락해요?"


def _start(client: httpx.Client) -> tuple[str, dict]:
    resp = client.post("/sessions", json={"role": ROLE})
    assert resp.status_code == 201
    body = resp.json()
    return body["session_id"], body["question"]


def _answer(client: httpx.Client, session_id: str, text: str) -> dict | None:
    resp = client.post(f"/sessions/{session_id}/answers", json={"text": text})
    assert resp.status_code == 200
    return resp.json()["next_question"]


def _run_session(client: httpx.Client) -> str:
    session_id, first = _start(client)
    assert first["id"] == "sys-01"
    follow = _answer(client, session_id, ANSWER_MAIN)
    assert follow is not None and follow["kind"] == "follow_up"
    nxt = _answer(client, session_id, ANSWER_FOLLOW)
    assert nxt is not None and nxt["kind"] == "main"
    end = client.post(f"/sessions/{session_id}/end")
    assert end.status_code == 200
    assert end.json()["ingested"] == 2
    return session_id


def test_session_then_kb_query_returns_citation(client: httpx.Client) -> None:
    tree = client.get(f"/question-trees/{ROLE}")
    assert tree.status_code == 200
    assert tree.json()["role"] == ROLE

    session_id = _run_session(client)

    resp = client.post("/kb/query", json={"question": QUERY_HIT})
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["citations"][0]["session_id"] == session_id
    assert "금요일" in body["citations"][0]["excerpt"]
    assert "금요일" in body["answer"]


def test_unknown_topic_returns_not_found(client: httpx.Client) -> None:
    _run_session(client)

    resp = client.post("/kb/query", json={"question": QUERY_MISS})
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is False
    assert body["answer"] == NOT_FOUND_MESSAGE
    assert body["citations"] == []


def test_invalid_inputs_are_rejected(client: httpx.Client) -> None:
    assert client.post("/sessions", json={"role": "no_such_role"}).status_code == 404

    session_id, _ = _start(client)
    blank = client.post(f"/sessions/{session_id}/answers", json={"text": "   "})
    assert blank.status_code == 422

    missing = client.post("/sessions/no-such-session/answers", json={"text": ANSWER_MAIN})
    assert missing.status_code == 404

    assert client.post(f"/sessions/{session_id}/end").status_code == 200
    closed = client.post(f"/sessions/{session_id}/answers", json={"text": ANSWER_MAIN})
    assert closed.status_code == 409

    assert client.post("/kb/query", json={"question": "  "}).status_code == 422


def test_query_before_end_is_not_found(client: httpx.Client) -> None:
    session_id, _ = _start(client)
    _answer(client, session_id, ANSWER_MAIN)

    resp = client.post("/kb/query", json={"question": QUERY_HIT})
    assert resp.status_code == 200
    assert resp.json()["found"] is False
