"""S12: 팀장 역할의 at-risk 응답에서 evidence·body·uncertain_cues 를 뺀다."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from handoff.config import Settings
from handoff.contracts import Evidence, InvalidationCondition, NewCard
from handoff.main import create_app
from handoff.rbac.tokens import Principal, Role, issue_invite

SECRET = "t"
LEAVER = "kim"
EVENT = "system_replaced"
HIDDEN = ("evidence", "body", "uncertain_cues")


def _settings(data_dir: Path, *, auth: bool, demo: bool = False) -> Settings:
    return Settings(
        provider="fake",
        data_dir=data_dir,
        min_score=0.25,
        collection="handoff_kb",
        auth=auth,
        auth_secret=SECRET if auth else "",
        demo=demo,
    )


def _seed(app: FastAPI, leaver: str) -> None:
    card = NewCard(
        leaver=leaver,
        role="결제 운영",
        session_id="s-risk",
        type="procedure",
        title="정산 배치 재실행",
        body="실패하면 로그를 보고 재실행 스크립트를 돌립니다.",
        entities=["정산 배치"],
        confidence=0.6,
        evidence=[Evidence(session_id="s-risk", question_id="q1", session_number=1,
                           excerpt="실패하면 로그를 보고 재실행합니다.")],
        certainty="uncertain",
        uncertain_cues=["아마"],
        invalidated_when=[InvalidationCondition(event=EVENT, condition="시스템이 바뀌면 틀립니다.")],
    )
    app.state.knowledge.store.replace_session_cards("s-risk", [card], datetime.now(UTC))


def _login(client: TestClient, role: Role) -> None:
    token = issue_invite(SECRET, Principal(role=role, leaver=LEAVER))
    res = client.get("/auth/accept", params={"token": token}, follow_redirects=False)
    assert res.status_code == 303


def _at_risk(client: TestClient, leaver: str, **headers: str) -> dict[str, Any]:
    res = client.get(f"/leavers/{leaver}/cards/at-risk", params={"event": EVENT}, headers=headers)
    assert res.status_code == 200, res.text
    body: dict[str, Any] = res.json()
    assert len(body["cards"]) >= 1
    return body


def _assert_manager_safe(body: dict[str, Any]) -> None:
    for item in body["cards"]:
        for key in HIDDEN:
            assert key not in item["card"]
        assert item["card"]["title"]
        assert item["conditions"]


@pytest.fixture()
def auth_app(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient]]:
    app = create_app(_settings(tmp_path, auth=True))
    with TestClient(app) as client:
        _seed(app, LEAVER)
        yield app, client


def test_manager_cookie_gets_safe_cards(auth_app: tuple[FastAPI, TestClient]) -> None:
    _, client = auth_app
    _login(client, "manager")
    body = _at_risk(client, LEAVER)
    _assert_manager_safe(body)
    card = body["cards"][0]
    assert card["card"]["title"] == "정산 배치 재실행"
    assert card["conditions"] == [{"event": EVENT, "condition": "시스템이 바뀌면 틀립니다."}]
    assert card["card"]["invalidated_when"] == card["conditions"]
    assert body["label"] and body["event"] == EVENT


def test_successor_cookie_keeps_evidence(auth_app: tuple[FastAPI, TestClient]) -> None:
    _, client = auth_app
    _login(client, "successor")
    card = _at_risk(client, LEAVER)["cards"][0]["card"]
    assert len(card["evidence"]) >= 1
    assert card["body"] and card["uncertain_cues"] == ["아마"]


def test_auth_off_keeps_evidence(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, auth=False))
    with TestClient(app) as client:
        _seed(app, LEAVER)
        card = _at_risk(client, LEAVER)["cards"][0]["card"]
    assert len(card["evidence"]) >= 1
    assert card["body"]


def test_demo_manager_header_gets_safe_cards(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, auth=True, demo=True))
    with TestClient(app) as client:
        res = client.post("/demo/start")
        assert res.status_code == 200, res.text
        leaver = res.json()["leaver"]
        _seed(app, leaver)
        client.post("/demo/role", json={"role": "successor"})
        succ = _at_risk(client, leaver, **{"X-Handoff-As": "successor"})
        assert all(len(item["card"]["evidence"]) >= 1 for item in succ["cards"])
        _assert_manager_safe(_at_risk(client, leaver, **{"X-Handoff-As": "manager"}))
