"""GET /leavers/{leaver}/questions/{qid}/thread: route, 404/422 and role x source checks."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from handoff.config import Settings
from handoff.contracts import QUESTION_NOT_FOUND, KBEntry, QueueSource
from handoff.main import create_app
from handoff.rbac.deps import FORBIDDEN, LOGIN_REQUIRED
from handoff.rbac.tokens import ADMIN_SCOPE, Principal, Role, issue_invite
from handoff.store import SessionRow

SECRET = "t"
LEAVER = "kim"
ROLE = "backend_developer"
NOW = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)
ANSWER = "배포 전에 스테이징 대시보드를 먼저 확인합니다."


def _settings(data_dir: Path, *, auth: bool) -> Settings:
    return Settings(
        provider="fake",
        data_dir=data_dir,
        min_score=0.25,
        collection="handoff_kb",
        auth=auth,
        auth_secret=SECRET if auth else "",
    )


def _client(tmp_path: Path, *, auth: bool) -> Iterator[TestClient]:
    with TestClient(create_app(_settings(tmp_path, auth=auth))) as client:
        yield client


@pytest.fixture()
def auth_client(tmp_path: Path) -> Iterator[TestClient]:
    yield from _client(tmp_path, auth=True)


@pytest.fixture()
def open_client(tmp_path: Path) -> Iterator[TestClient]:
    yield from _client(tmp_path, auth=False)


def _login(client: TestClient, role: Role, leaver: str = LEAVER) -> None:
    scope = ADMIN_SCOPE if role == "admin" else leaver
    token = issue_invite(SECRET, Principal(role=role, leaver=scope))
    res = client.get("/auth/accept", params={"token": token}, follow_redirects=False)
    assert res.status_code == 303


def _enqueue(client: TestClient, source: QueueSource, text: str) -> str:
    app: FastAPI = client.app  # type: ignore[assignment]
    item, _ = app.state.knowledge.store.enqueue(
        LEAVER, ROLE, source, "systems", text, "이유", None, NOW
    )
    qid: str = item.id
    return qid


def _answer_and_end(client: TestClient, qid: str, question: str) -> None:
    app: FastAPI = client.app  # type: ignore[assignment]
    sessions: Any = app.state.knowledge.successor._sessions
    row = SessionRow(
        session_id="sess-1",
        leaver=LEAVER,
        role=ROLE,
        number=1,
        started_at=NOW,
        deadline=NOW + timedelta(minutes=30),
        ended_at=None,
        node_index=0,
        current_json=None,
        summary=[],
    )
    sessions.insert_session(row)
    sessions.add_answer(
        KBEntry(
            session_id=row.session_id,
            question_id=qid,
            axis="systems",
            question=question,
            answer=ANSWER,
            created_at=NOW + timedelta(minutes=1),
            session_number=1,
        )
    )
    row.ended_at = NOW + timedelta(minutes=10)
    sessions.update_session(row)


def _url(qid: str, leaver: str = LEAVER) -> str:
    return f"/leavers/{leaver}/questions/{qid}/thread"


# --- auth off: open, no source check ---


@pytest.mark.parametrize("source", ["successor", "manager", "gap"])
def test_auth_off_any_source_200(open_client: TestClient, source: QueueSource) -> None:
    qid = _enqueue(open_client, source, f"{source} 질문")
    res = open_client.get(_url(qid))
    assert res.status_code == 200
    body = res.json()
    assert body["item"]["id"] == qid
    assert body["item"]["source"] == source
    assert body["state"] == "waiting"
    assert body["turns"] == []


def test_auth_off_missing_qid_404(open_client: TestClient) -> None:
    res = open_client.get(_url("q999"))
    assert res.status_code == 404
    assert res.json()["detail"] == QUESTION_NOT_FOUND


def test_auth_off_other_leaver_404(open_client: TestClient) -> None:
    qid = _enqueue(open_client, "successor", "후임 질문")
    res = open_client.get(_url(qid, leaver="lee"))
    assert res.status_code == 404
    assert res.json()["detail"] == QUESTION_NOT_FOUND


def test_bad_qid_422(open_client: TestClient) -> None:
    assert open_client.get(_url("abc")).status_code == 422


# --- auth on ---


def test_successor_own_source_200(auth_client: TestClient) -> None:
    qid = _enqueue(auth_client, "successor", "후임 질문")
    _login(auth_client, "successor")
    res = auth_client.get(_url(qid))
    assert res.status_code == 200
    assert res.json()["item"]["source"] == "successor"


def test_successor_manager_item_403(auth_client: TestClient) -> None:
    qid = _enqueue(auth_client, "manager", "팀장 질문")
    _login(auth_client, "successor")
    res = auth_client.get(_url(qid))
    assert res.status_code == 403
    assert res.json()["detail"] == FORBIDDEN


def test_manager_own_source_200_with_answer(auth_client: TestClient) -> None:
    question = "배포 전에 무엇을 확인하나요?"
    qid = _enqueue(auth_client, "manager", question)
    _answer_and_end(auth_client, qid, question)
    _login(auth_client, "manager")
    res = auth_client.get(_url(qid))
    assert res.status_code == 200
    body = res.json()
    assert body["state"] == "answered"
    assert len(body["turns"]) == 1
    assert body["turns"][0]["answer"] == ANSWER
    assert body["turns"][0]["session_number"] == 1


@pytest.mark.parametrize("source", ["successor", "gap"])
def test_manager_other_source_403(auth_client: TestClient, source: QueueSource) -> None:
    qid = _enqueue(auth_client, source, f"{source} 질문")
    _answer_and_end(auth_client, qid, f"{source} 질문")
    _login(auth_client, "manager")
    res = auth_client.get(_url(qid))
    assert res.status_code == 403
    assert res.json()["detail"] == FORBIDDEN
    assert ANSWER not in res.text


@pytest.mark.parametrize("role", ["leaver", "admin"])
def test_leaver_and_admin_403(auth_client: TestClient, role: Role) -> None:
    qid = _enqueue(auth_client, "successor", "후임 질문")
    _login(auth_client, role)
    res = auth_client.get(_url(qid))
    assert res.status_code == 403
    assert res.json()["detail"] == FORBIDDEN


def test_no_cookie_401(auth_client: TestClient) -> None:
    qid = _enqueue(auth_client, "successor", "후임 질문")
    res = auth_client.get(_url(qid))
    assert res.status_code == 401
    assert res.json()["detail"] == LOGIN_REQUIRED


def test_other_leaver_path_403(auth_client: TestClient) -> None:
    qid = _enqueue(auth_client, "successor", "후임 질문")
    _login(auth_client, "successor")
    res = auth_client.get(_url(qid, leaver="lee"))
    assert res.status_code == 403
    assert res.json()["detail"] == FORBIDDEN


def test_missing_qid_404_auth_on(auth_client: TestClient) -> None:
    _login(auth_client, "successor")
    res = auth_client.get(_url("q999"))
    assert res.status_code == 404
    assert res.json()["detail"] == QUESTION_NOT_FOUND


def test_bad_qid_422_auth_on(auth_client: TestClient) -> None:
    _login(auth_client, "successor")
    assert auth_client.get(_url("abc")).status_code == 422
