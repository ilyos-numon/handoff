from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from handoff.kb import NOT_FOUND_MESSAGE, KnowledgeBase
from handoff.providers import FakeEmbedder, FakeLLM
from handoff.question_tree import QuestionTree, load_tree
from handoff.routes import Services, router
from handoff.session import SessionService

ANSWER_MAIN = "금요일 배포는 피합니다. 작년 3월 금요일 저녁 배포 후 정산 배치가 주말 내내 실패했습니다."
ANSWER_FOLLOW = "복구는 큐를 비우고 정산 배치를 수동으로 재실행했습니다."
QUERY_HIT = "금요일에 배포하면 왜 안 되나요?"
QUERY_MISS = "PG사 장애 나면 누구한테 연락해요?"

MIN_SCORE = 0.25
ROLE = "backend_developer"


@pytest.fixture
def kb(tmp_path: Path) -> Iterator[KnowledgeBase]:
    instance = KnowledgeBase(tmp_path / "qdrant", FakeEmbedder(), FakeLLM(), MIN_SCORE)
    yield instance
    instance.close()


@pytest.fixture
def client(kb: KnowledgeBase) -> Iterator[TestClient]:
    app = FastAPI()
    app.state.services = Services(sessions=SessionService(kb, FakeLLM()), kb=kb)
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client


def _start(client: TestClient) -> str:
    resp = client.post("/sessions", json={"role": ROLE})
    assert resp.status_code == 201
    session_id: str = resp.json()["session_id"]
    return session_id


def test_services_trees_defaults_to_load_tree(kb: KnowledgeBase) -> None:
    services = Services(sessions=SessionService(kb, FakeLLM()), kb=kb)
    assert services.trees is load_tree


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_get_question_tree(client: TestClient) -> None:
    resp = client.get(f"/question-trees/{ROLE}")
    assert resp.status_code == 200
    tree = QuestionTree.model_validate(resp.json())
    assert tree == load_tree(ROLE)
    assert tree.role == ROLE
    for axis in ("systems", "routines", "incidents", "people"):
        assert sum(1 for n in tree.nodes if n.axis == axis) >= 2
    assert all(len(n.follow_ups) >= 1 for n in tree.nodes)


def test_get_question_tree_unknown_role(client: TestClient) -> None:
    resp = client.get("/question-trees/unknown_role")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "unknown role"}


def test_start_session(client: TestClient) -> None:
    resp = client.post("/sessions", json={"role": ROLE})
    assert resp.status_code == 201
    body = resp.json()
    first = load_tree(ROLE).nodes[0]
    assert len(body["session_id"]) == 32
    assert body["question"] == {
        "id": first.id,
        "axis": first.axis,
        "text": first.text,
        "kind": "main",
    }


def test_start_session_unknown_role(client: TestClient) -> None:
    resp = client.post("/sessions", json={"role": "unknown_role"})
    assert resp.status_code == 404
    assert resp.json() == {"detail": "unknown role"}


@pytest.mark.parametrize("payload", [{}, {"role": ""}, {"role": "   "}])
def test_start_session_invalid_body(client: TestClient, payload: dict[str, str]) -> None:
    assert client.post("/sessions", json=payload).status_code == 422


def test_answer_returns_follow_up_then_main(client: TestClient) -> None:
    session_id = _start(client)
    resp = client.post(f"/sessions/{session_id}/answers", json={"text": ANSWER_MAIN})
    assert resp.status_code == 200
    follow = resp.json()["next_question"]
    assert follow["kind"] == "follow_up"
    assert follow["id"] == "sys-01-f1"
    assert follow["axis"] == "systems"

    resp = client.post(f"/sessions/{session_id}/answers", json={"text": ANSWER_FOLLOW})
    assert resp.status_code == 200
    nxt = resp.json()["next_question"]
    assert nxt["kind"] == "main"
    assert nxt["id"] == load_tree(ROLE).nodes[1].id


def test_answer_returns_null_when_tree_exhausted(client: TestClient) -> None:
    session_id = _start(client)
    total = len(load_tree(ROLE).nodes) * 2
    for i in range(total):
        resp = client.post(f"/sessions/{session_id}/answers", json={"text": f"답변 {i}"})
        assert resp.status_code == 200
    assert resp.json() == {"next_question": None}


def test_answer_unknown_session(client: TestClient) -> None:
    resp = client.post("/sessions/missing/answers", json={"text": ANSWER_MAIN})
    assert resp.status_code == 404
    assert resp.json() == {"detail": "session not found"}


def test_answer_closed_session(client: TestClient) -> None:
    session_id = _start(client)
    assert client.post(f"/sessions/{session_id}/end").status_code == 200
    resp = client.post(f"/sessions/{session_id}/answers", json={"text": ANSWER_MAIN})
    assert resp.status_code == 409
    assert resp.json() == {"detail": "session closed"}


@pytest.mark.parametrize("payload", [{}, {"text": ""}, {"text": " \n\t "}])
def test_answer_invalid_body(client: TestClient, payload: dict[str, str]) -> None:
    session_id = _start(client)
    resp = client.post(f"/sessions/{session_id}/answers", json=payload)
    assert resp.status_code == 422
    resp = client.post(f"/sessions/{session_id}/end")
    assert resp.json()["ingested"] == 0


def test_end_session(client: TestClient) -> None:
    session_id = _start(client)
    client.post(f"/sessions/{session_id}/answers", json={"text": ANSWER_MAIN})
    client.post(f"/sessions/{session_id}/answers", json={"text": ANSWER_FOLLOW})
    resp = client.post(f"/sessions/{session_id}/end")
    assert resp.status_code == 200
    assert resp.json() == {"session_id": session_id, "ingested": 2}


def test_end_unknown_session(client: TestClient) -> None:
    resp = client.post("/sessions/missing/end")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "session not found"}


def test_end_closed_session(client: TestClient) -> None:
    session_id = _start(client)
    assert client.post(f"/sessions/{session_id}/end").status_code == 200
    resp = client.post(f"/sessions/{session_id}/end")
    assert resp.status_code == 409
    assert resp.json() == {"detail": "session closed"}


@pytest.mark.parametrize("payload", [{}, {"question": ""}, {"question": "   "}])
def test_kb_query_invalid_body(client: TestClient, payload: dict[str, str]) -> None:
    assert client.post("/kb/query", json=payload).status_code == 422


def test_kb_query_empty_kb_not_found(client: TestClient) -> None:
    resp = client.post("/kb/query", json={"question": QUERY_HIT})
    assert resp.status_code == 200
    assert resp.json() == {"found": False, "answer": NOT_FOUND_MESSAGE, "citations": []}


def test_full_flow_then_kb_hit_and_miss(client: TestClient) -> None:
    session_id = _start(client)
    resp = client.post(f"/sessions/{session_id}/answers", json={"text": ANSWER_MAIN})
    assert resp.json()["next_question"]["kind"] == "follow_up"
    resp = client.post(f"/sessions/{session_id}/answers", json={"text": ANSWER_FOLLOW})
    assert resp.json()["next_question"]["kind"] == "main"
    resp = client.post(f"/sessions/{session_id}/end")
    assert resp.json() == {"session_id": session_id, "ingested": 2}

    hit = client.post("/kb/query", json={"question": QUERY_HIT})
    assert hit.status_code == 200
    body = hit.json()
    assert body["found"] is True
    assert body["citations"][0]["session_id"] == session_id
    assert body["citations"][0]["question_id"] == "sys-01"
    assert "금요일" in body["citations"][0]["excerpt"]
    assert body["answer"].startswith("인수인계 기록에 따르면: ")

    miss = client.post("/kb/query", json={"question": QUERY_MISS})
    assert miss.status_code == 200
    assert miss.json() == {"found": False, "answer": NOT_FOUND_MESSAGE, "citations": []}
