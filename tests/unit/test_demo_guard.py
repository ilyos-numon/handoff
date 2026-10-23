"""Demo role header (X-Handoff-As) and public demo abuse guards on the full app."""

import json
import time
from base64 import b64encode
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from starlette.middleware.sessions import SessionMiddleware

from handoff.config import Settings
from handoff.demo.guard import (
    DEMO_ROLE_HEADER,
    UPLOAD_BLOCKED,
    DemoBodyGuard,
)
from handoff.main import create_app
from handoff.rbac.policy import SESSION_COOKIE
from handoff.rbac.tokens import Principal, issue_invite

SECRET = "t"
QUESTION = "정산 배치는 언제 도나요?"


def _settings(data_dir: Path, *, demo: bool = True, **extra: Any) -> Settings:
    return Settings(
        provider="fake",
        data_dir=data_dir,
        min_score=0.25,
        collection="handoff_kb",
        auth=True,
        auth_secret=SECRET,
        demo=demo,
        **extra,
    )


@pytest.fixture()
def app(tmp_path: Path) -> FastAPI:
    return create_app(_settings(tmp_path))


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _start(client: TestClient) -> str:
    res = client.post("/demo/start")
    assert res.status_code == 200, res.text
    leaver: str = res.json()["leaver"]
    return leaver


def _as(role: str) -> dict[str, str]:
    return {DEMO_ROLE_HEADER: role}


def _cookie(session: dict[str, Any]) -> str:
    """Sign a session cookie the way Starlette's SessionMiddleware does."""
    data = b64encode(json.dumps(session).encode("utf-8"))
    return TimestampSigner(SECRET).sign(data).decode("utf-8")


def _ask(client: TestClient, leaver: str, role: str) -> int:
    res = client.post("/kb/ask", json={"question": QUESTION, "leaver": leaver}, headers=_as(role))
    return res.status_code


# --- X-Handoff-As -----------------------------------------------------------


def test_header_picks_role_inside_demo_sandbox(client: TestClient) -> None:
    leaver = _start(client)  # cookie role is manager
    assert client.post("/kb/ask", json={"question": QUESTION, "leaver": leaver}).status_code == 403
    assert _ask(client, leaver, "successor") == 200
    me = client.get("/auth/me", headers=_as("leaver")).json()
    assert me == {"auth": True, "role": "leaver", "leaver": leaver}
    # The cookie itself is unchanged: without the header it is still the manager.
    assert client.get("/auth/me").json()["role"] == "manager"


def test_header_ignored_for_regular_invite_cookie(client: TestClient) -> None:
    token = issue_invite(SECRET, Principal.model_validate({"role": "manager", "leaver": "kim"}))
    client.get("/auth/accept", params={"token": token}, follow_redirects=False)
    assert client.get("/auth/me", headers=_as("successor")).json()["role"] == "manager"
    assert _ask(client, "kim", "successor") == 403


def test_header_ignored_when_demo_cookie_expired(client: TestClient) -> None:
    leaver = _start(client)
    live = {"role": "manager", "leaver": leaver, "demo": True, "demo_exp": int(time.time()) + 600}
    client.cookies.set(SESSION_COOKIE, _cookie(live))
    assert _ask(client, leaver, "successor") == 200  # positive control for the forged cookie
    expired = dict(live, demo_exp=int(time.time()) - 1)
    client.cookies.set(SESSION_COOKIE, _cookie(expired))
    assert _ask(client, leaver, "successor") == 403
    assert client.get("/auth/me", headers=_as("successor")).json()["role"] == "manager"


@pytest.mark.parametrize("role", ["admin", "intruder", ""])
def test_header_ignores_non_invitable_roles(client: TestClient, role: str) -> None:
    _start(client)
    assert client.get("/auth/me", headers=_as(role)).json()["role"] == "manager"
    assert client.get("/admin/leavers", headers=_as(role)).status_code == 403


def test_header_ignored_when_demo_off(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, demo=False))) as c:
        exp = int(time.time()) + 600
        c.cookies.set(
            SESSION_COOKIE,
            _cookie({"role": "manager", "leaver": "kim", "demo": True, "demo_exp": exp}),
        )
        assert c.get("/auth/me", headers=_as("successor")).json()["role"] == "manager"
        assert _ask(c, "kim", "successor") == 403


def test_header_cannot_reach_other_sandbox(app: FastAPI) -> None:
    with TestClient(app) as a, TestClient(app) as b:
        leaver_a = _start(a)
        _start(b)
        assert _ask(b, leaver_a, "successor") == 403
        assert b.get(f"/leavers/{leaver_a}/queue", headers=_as("manager")).status_code == 403
        assert _ask(a, leaver_a, "successor") == 200


# --- upload block and body size --------------------------------------------


def test_real_upload_blocked_but_mock_allowed(client: TestClient) -> None:
    leaver = _start(client)
    res = client.post(
        f"/leavers/{leaver}/sources",
        json={"title": "t", "text": "real document", "kind": "doc"},
        headers=_as("manager"),
    )
    assert res.status_code == 403
    assert res.json() == {"detail": UPLOAD_BLOCKED}
    assert client.post(f"/leavers/{leaver}/sources/mock", headers=_as("manager")).status_code == 201
    assert client.get(f"/leavers/{leaver}/sources", headers=_as("manager")).status_code == 200


def test_upload_not_blocked_when_demo_off(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, demo=False))) as c:
        token = issue_invite(SECRET, Principal.model_validate({"role": "manager", "leaver": "kim"}))
        c.get("/auth/accept", params={"token": token}, follow_redirects=False)
        res = c.post("/leavers/kim/sources", json={"title": "t", "text": "x" * 20000})
        assert res.status_code != 403
        assert res.status_code != 413


def test_declared_body_too_large_is_413(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, demo_max_body_bytes=1024))) as c:
        leaver = _start(c)
        big = {"question": "x" * 2000, "leaver": leaver}
        res = c.post("/kb/ask", json=big, headers=_as("successor"))
        assert res.status_code == 413
        assert res.json() == {"detail": "request too large"}
        assert _ask(c, leaver, "successor") == 200


def test_streamed_body_too_large_is_413(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, demo_max_body_bytes=1024))) as c:
        _start(c)

        def chunks() -> Iterator[bytes]:
            for _ in range(4):
                yield b"x" * 512

        res = c.post(
            "/kb/ask",
            content=chunks(),
            headers={**_as("successor"), "content-type": "application/json"},
        )
        assert res.status_code == 413


# --- rate limits -------------------------------------------------------------


def test_default_rate_limit_is_429(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, demo_rate="3/minute"))) as c:
        codes = [c.get("/demo/status").status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]


def test_start_rate_limit_is_429(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, demo_start_rate="2/minute"))) as c:
        codes = [c.post("/demo/start").status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        assert c.get("/demo/status").status_code == 200  # other paths keep the default limit


def test_no_rate_limit_when_demo_off(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, demo=False, demo_rate="1/minute"))
    assert not any(m.cls is DemoBodyGuard for m in app.user_middleware)
    with TestClient(app) as c:
        assert [c.get("/demo/status").status_code for _ in range(3)] == [200, 200, 200]


# --- middleware order ----------------------------------------------------------


def test_session_middleware_is_outermost(app: FastAPI, client: TestClient) -> None:
    classes = [m.cls for m in app.user_middleware]
    assert classes[0] is SessionMiddleware
    assert DemoBodyGuard in classes
    leaver = _start(client)
    # The cookie set by /demo/start is read again behind the guards.
    assert client.get("/demo/status").json()["leaver"] == leaver
    assert _ask(client, leaver, "successor") == 200
