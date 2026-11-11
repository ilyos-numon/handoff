"""Knowledge loop assembled into a FastAPI app the same way main.py wires it (without main.py)."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from handoff.config import Settings
from handoff.fence import FenceService
from handoff.kb import KnowledgeBase
from handoff.knowledge_app import KnowledgeServices, build_knowledge, include_knowledge_routers
from handoff.premortem import PremortemService
from handoff.providers import FakeEmbedder, FakeLLM
from handoff.question_tree import load_tree
from handoff.routes import Services, router
from handoff.routing import QuestionRouter
from handoff.session import SessionService
from handoff.store import SessionStore
from handoff.verify import ClaimVerifier

ROLE = "backend_developer"
TREE = load_tree(ROLE)
PARTIAL_ANSWER = "배포 전에 변경 목록을 확인합니다. 장애가 잦았기 때문입니다."
FULL_ANSWER = (
    "매주 월요일에 정산 배치를 확인합니다. 누락을 막기 때문입니다. 결제팀 리드에게 공유하고 "
    "스크립트로 순서대로 재실행합니다. 안 되면 수동으로 대체합니다."
)
NO_MORE = "추가로 기억나는 것은 없습니다."
LOOP_QUESTION = "정산 배치가 실패하면 손으로 어떻게 복구하나요?"
LOOP_ANSWER = "정산 배치가 실패하면 실패 구간을 확인하고 정산 배치를 수동으로 재실행합니다."



def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    base = Settings(provider="fake", data_dir=tmp_path, min_score=0.25, collection="handoff_kb")
    return replace(base, **overrides)


@contextmanager
def _app(tmp_path: Path, **overrides: Any) -> Iterator[tuple[TestClient, KnowledgeServices]]:
    settings = _settings(tmp_path, **overrides)
    llm = FakeLLM()
    store = SessionStore(":memory:")
    knowledge = build_knowledge(settings, llm, store)
    kb = KnowledgeBase(
        tmp_path / "qdrant", FakeEmbedder(), llm, settings.min_score, verifier=knowledge.verifier
    )
    sessions = SessionService(kb, llm, store=store, hooks=knowledge.hooks)
    app = FastAPI()
    app.state.services = Services(sessions=sessions, kb=kb)
    app.state.knowledge = knowledge
    app.include_router(router)
    include_knowledge_routers(app)
    try:
        with TestClient(app) as client:
            yield client, knowledge
    finally:
        sessions.close()
        kb.close()


@pytest.fixture
def loop_app(tmp_path: Path) -> Iterator[tuple[TestClient, KnowledgeServices]]:
    with _app(tmp_path) as pair:
        yield pair


def _start(client: TestClient, leaver: str) -> tuple[str, dict[str, Any]]:
    resp = client.post("/sessions", json={"role": ROLE, "leaver": leaver})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["session_id"], body["question"]


def _answer(client: TestClient, session_id: str, text: str) -> dict[str, Any] | None:
    resp = client.post(f"/sessions/{session_id}/answers", json={"text": text})
    assert resp.status_code == 200, resp.text
    nxt: dict[str, Any] | None = resp.json()["next_question"]
    return nxt


def _end(client: TestClient, session_id: str) -> None:
    resp = client.post(f"/sessions/{session_id}/end")
    assert resp.status_code == 200, resp.text


def _ask(client: TestClient, leaver: str, question: str) -> dict[str, Any]:
    resp = client.post("/kb/ask", json={"question": question, "leaver": leaver})
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def _queue(client: TestClient, leaver: str) -> list[dict[str, Any]]:
    resp = client.get(f"/leavers/{leaver}/queue")
    assert resp.status_code == 200, resp.text
    items: list[dict[str, Any]] = resp.json()
    return items


# ---- 조립


def test_build_knowledge_uses_settings(tmp_path: Path) -> None:
    store = SessionStore(":memory:")
    try:
        on = build_knowledge(_settings(tmp_path), FakeLLM(), store)
        off = build_knowledge(_settings(tmp_path, verify_claims=False), FakeLLM(), store)
        assert isinstance(on.verifier, ClaimVerifier)
        assert off.verifier is None
        assert on.hooks is not None and on.store is not None
    finally:
        store.close()


def test_include_knowledge_routers_registers_feature_paths() -> None:
    app = FastAPI()
    include_knowledge_routers(app)
    paths = set(app.openapi()["paths"])
    expected = {
        "/leavers/{leaver}/sources",
        "/leavers/{leaver}/sources/mock",
        "/leavers/{leaver}/entities",
        "/leavers/{leaver}/questions/generate",
        "/depth/judge",
        "/depth/follow-up",
        "/sessions/{session_id}/depth",
        "/leavers/{leaver}/cards",
        "/sessions/{session_id}/cards/extract",
        "/leavers/{leaver}/gaps/refresh",
        "/leavers/{leaver}/gaps",
        "/leavers/{leaver}/queue",
        "/kb/ask",
        "/leavers/{leaver}/successor-questions",
        "/leavers/{leaver}/artifacts/{kind}",
        "/leavers/{leaver}/drills",
        "/drills/{drill_id}",
        "/drills/{drill_id}/answers",
        "/kb/fence",
        "/depth/tacit",
        "/leavers/{leaver}/cards/at-risk",
        "/leavers/{leaver}/premortem/questions",
    }
    assert expected <= paths


def test_build_knowledge_wires_honesty_services(tmp_path: Path) -> None:
    store = SessionStore(":memory:")
    try:
        on = build_knowledge(_settings(tmp_path), FakeLLM(), store)
        off = build_knowledge(_settings(tmp_path, tacit_followups=False), FakeLLM(), store)
        assert isinstance(on.fence, FenceService)
        assert isinstance(on.router, QuestionRouter)
        assert isinstance(on.premortem, PremortemService)
        assert on.successor._router is on.router
        assert on.depth._tacit_followups is True
        assert off.depth._tacit_followups is False
    finally:
        store.close()


def test_fence_and_premortem_routes_respond(
    loop_app: tuple[TestClient, KnowledgeServices],
) -> None:
    client, _ = loop_app
    fence = client.post("/kb/fence", json={"leaver": "demo", "subject": "금요일 배포 금지"})
    assert fence.status_code == 200, fence.text
    premortem = client.post("/leavers/demo/premortem/questions")
    assert premortem.status_code == 200, premortem.text
    assert len(premortem.json()) == 3


def test_mock_sources_route_works(loop_app: tuple[TestClient, KnowledgeServices]) -> None:
    client, _ = loop_app
    resp = client.post("/leavers/demo/sources/mock")
    assert resp.status_code == 201
    assert len(resp.json()) >= 8


# ---- interview-session


def test_slot_follow_up_sys_01_s1(loop_app: tuple[TestClient, KnowledgeServices]) -> None:
    client, _ = loop_app
    sid, first = _start(client, "kim")
    assert first["id"] == "sys-01" and first["kind"] == "main"
    nxt = _answer(client, sid, PARTIAL_ANSWER)
    assert nxt is not None
    assert nxt["kind"] == "follow_up"
    assert nxt["id"] == "sys-01-s1"
    assert nxt["axis"] == first["axis"]
    assert "언제" in nxt["text"]
    depth = client.get(f"/sessions/{sid}/depth")
    assert depth.status_code == 200
    assert len(depth.json()["judgements"]) == 1


def test_follow_up_limit_moves_to_next_main(
    loop_app: tuple[TestClient, KnowledgeServices],
) -> None:
    client, _ = loop_app
    sid, _ = _start(client, "kim")
    follow = _answer(client, sid, PARTIAL_ANSWER)
    assert follow is not None and follow["kind"] == "follow_up"
    nxt = _answer(client, sid, NO_MORE)
    assert nxt is not None
    assert nxt["kind"] == "main"
    assert nxt["id"] == TREE.nodes[1].id


def test_follow_up_limit_zero_skips_follow_ups(tmp_path: Path) -> None:
    with _app(tmp_path, depth_max_followups=0) as (client, _):
        sid, _ = _start(client, "kim")
        nxt = _answer(client, sid, PARTIAL_ANSWER)
        assert nxt is not None
        assert nxt["kind"] == "main" and nxt["id"] == TREE.nodes[1].id


def test_second_session_continues(loop_app: tuple[TestClient, KnowledgeServices]) -> None:
    client, _ = loop_app
    sid, _ = _start(client, "kim")
    _answer(client, sid, PARTIAL_ANSWER)
    _answer(client, sid, NO_MORE)
    _end(client, sid)
    resp = client.post("/sessions", json={"role": ROLE, "leaver": "kim"})
    assert resp.status_code == 201
    assert resp.json()["number"] == 2
    assert resp.json()["question"]["id"] == "rou-01"


# ---- 질문 큐 우선 출제


def test_queue_item_asked_first(loop_app: tuple[TestClient, KnowledgeServices]) -> None:
    client, _ = loop_app
    item = _ask(client, "kim", "사무실 주차 등록은 어떻게 해요?")["queued"]
    sid, first = _start(client, "kim")
    assert first["kind"] == "queued"
    assert first["id"] == item["id"]
    assert first["axis"] == item["axis"]
    asked = next(q for q in _queue(client, "kim") if q["id"] == item["id"])
    assert asked["status"] == "asked"
    assert asked["asked_session_id"] == sid


def test_queue_per_session_limit_two(loop_app: tuple[TestClient, KnowledgeServices]) -> None:
    client, _ = loop_app
    for text in (
        "사무실 주차 등록은 어떻게 해요?",
        "휴가 신청 결재선은 어떻게 되나요?",
        "회의실 예약은 어디서 하나요?",
    ):
        assert _ask(client, "kim", text)["queued"] is not None
    sid, q = _start(client, "kim")
    kinds = [q["kind"]]
    for _ in range(20):
        if q["kind"] == "main":
            break
        nxt = _answer(client, sid, NO_MORE if q["kind"] == "follow_up" else PARTIAL_ANSWER)
        assert nxt is not None
        q = nxt
        kinds.append(q["kind"])
    assert kinds.count("queued") == 2
    assert kinds[-1] == "main"
    assert q["id"] == TREE.nodes[0].id
    pending = [i for i in _queue(client, "kim") if i["status"] == "pending"]
    assert len(pending) == 1


def test_unanswered_queue_item_returns_to_pending(
    loop_app: tuple[TestClient, KnowledgeServices],
) -> None:
    client, _ = loop_app
    item = _ask(client, "kim", "사무실 주차 등록은 어떻게 해요?")["queued"]
    sid, first = _start(client, "kim")
    assert first["id"] == item["id"]
    _end(client, sid)
    back = next(q for q in _queue(client, "kim") if q["id"] == item["id"])
    assert back["status"] == "pending"
    assert back["asked_session_id"] is None


def test_queue_question_keeps_tree_position(
    loop_app: tuple[TestClient, KnowledgeServices],
) -> None:
    client, _ = loop_app
    _ask(client, "kim", "사무실 주차 등록은 어떻게 해요?")
    sid, first = _start(client, "kim")
    assert first["kind"] == "queued"
    _answer(client, sid, FULL_ANSWER)
    _end(client, sid)
    _, nxt = _start(client, "kim")
    assert nxt["kind"] == "main"
    assert nxt["id"] == TREE.nodes[0].id


def test_tree_exhausted_with_empty_queue(tmp_path: Path) -> None:
    with _app(tmp_path) as (client, knowledge):
        resp = client.post("/sessions", json={"role": ROLE, "leaver": "kim"})
        assert resp.status_code == 201
        assert knowledge.store.list_queue("kim") == []


# ---- successor-loop


def test_successor_ask_queues_once(loop_app: tuple[TestClient, KnowledgeServices]) -> None:
    client, _ = loop_app
    first = _ask(client, "loop", "사무실 주차 등록은 어떻게 해요?")
    second = _ask(client, "loop", "사무실 주차 등록은 어떻게 해요?")
    assert first["answer"]["found"] is False
    assert first["queued"]["source"] == "successor"
    assert first["queued"]["status"] == "pending"
    assert first["queued"]["id"] == second["queued"]["id"]
    listed = client.get("/leavers/loop/successor-questions").json()
    assert [q["id"] for q in listed] == [first["queued"]["id"]]


def test_loop_closes(loop_app: tuple[TestClient, KnowledgeServices]) -> None:
    client, _ = loop_app
    item = _ask(client, "loop", LOOP_QUESTION)["queued"]
    assert item is not None
    sid, first = _start(client, "loop")
    assert first["kind"] == "queued" and first["id"] == item["id"]
    _answer(client, sid, LOOP_ANSWER)
    _end(client, sid)
    closed = next(q for q in _queue(client, "loop") if q["id"] == item["id"])
    assert closed["status"] == "answered"
    assert closed["answered_session_id"] == sid
    resp = client.post("/kb/query", json={"question": LOOP_QUESTION})
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["citations"][0]["question_id"] == item["id"]
    again = _ask(client, "loop", LOOP_QUESTION)
    assert again["answer"]["found"] is True
    assert again["queued"] is None


def test_session_end_extracts_cards(loop_app: tuple[TestClient, KnowledgeServices]) -> None:
    client, _ = loop_app
    sid, _ = _start(client, "kim")
    _answer(client, sid, FULL_ANSWER)
    _end(client, sid)
    cards = client.get("/leavers/kim/cards")
    assert cards.status_code == 200
    assert len(cards.json()) >= 1
    assert all(e["session_id"] == sid for c in cards.json() for e in c["evidence"])


# ---- 검증 끔


def test_verify_off_keeps_composed_answer(tmp_path: Path) -> None:
    with _app(tmp_path, verify_claims=False) as (client, knowledge):
        assert knowledge.verifier is None
        _ask(client, "loop", LOOP_QUESTION)
        sid, _ = _start(client, "loop")
        _answer(client, sid, LOOP_ANSWER)
        _end(client, sid)
        body = client.post("/kb/query", json={"question": LOOP_QUESTION}).json()
        assert body["found"] is True
        assert body["claims_removed"] == 0
