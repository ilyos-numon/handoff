"""RBAC dependency, auth routes and invite CLI on a small FastAPI app."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from handoff.rbac import routes_auth
from handoff.rbac.deps import FORBIDDEN, LOGIN_REQUIRED, authorize
from handoff.rbac.policy import SESSION_COOKIE, AuthConfig, build_enforcer
from handoff.rbac.tokens import Principal, issue_invite, read_invite
from handoff.session import SessionNotFoundError

SECRET = "unit-test-secret"


class FakeSessions:
    def __init__(self, owners: dict[str, str]) -> None:
        self._owners = owners

    def view(self, session_id: str) -> SimpleNamespace:
        if session_id not in self._owners:
            raise SessionNotFoundError(session_id)
        return SimpleNamespace(leaver=self._owners[session_id])


class FakeStore:
    def __init__(self, drills: dict[str, str]) -> None:
        self._drills = drills

    def get_drill(self, drill_id: str) -> SimpleNamespace | None:
        leaver = self._drills.get(drill_id)
        return None if leaver is None else SimpleNamespace(leaver=leaver)


def _dummy_router() -> APIRouter:
    router = APIRouter(dependencies=[Depends(authorize)])

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/coverage")
    def coverage(leaver: str = "default") -> dict[str, str]:
        return {"leaver": leaver}

    @router.post("/kb/ask")
    def ask(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        return payload

    @router.post("/kb/query")
    def query() -> dict[str, str]:
        return {"ok": "yes"}

    @router.get("/question-trees/{role}")
    def tree(role: str) -> dict[str, str]:
        return {"role": role}

    @router.api_route("/leavers/{leaver}/gaps", methods=["GET", "HEAD"])
    def gaps(leaver: str) -> dict[str, str]:
        return {"leaver": leaver}

    @router.get("/sessions/{session_id}")
    def session(session_id: str, request: Request) -> dict[str, str]:
        try:
            view = request.app.state.services.sessions.view(session_id)
        except SessionNotFoundError:
            raise HTTPException(404, detail="session not found") from None
        return {"leaver": view.leaver}

    @router.get("/drills/{drill_id}")
    def drill(drill_id: str, request: Request) -> dict[str, str]:
        found = request.app.state.knowledge.store.get_drill(drill_id)
        if found is None:
            raise HTTPException(404, detail="drill not found")
        return {"leaver": found.leaver}

    @router.get("/unlisted")
    def unlisted() -> dict[str, str]:
        return {"ok": "yes"}

    return router


def _app(auth: AuthConfig | None) -> FastAPI:
    app = FastAPI()
    app.state.knowledge = SimpleNamespace(auth=auth, store=FakeStore({"d1": "kim"}))
    app.state.services = SimpleNamespace(sessions=FakeSessions({"s1": "kim"}))
    if auth is not None:
        app.add_middleware(SessionMiddleware, secret_key=auth.secret, session_cookie=SESSION_COOKIE)
    app.include_router(routes_auth.router)
    app.include_router(_dummy_router())
    return app


@pytest.fixture(scope="module")
def auth() -> AuthConfig:
    return AuthConfig(secret=SECRET, invite_ttl_s=3600, enforcer=build_enforcer())


@pytest.fixture
def client(auth: AuthConfig) -> TestClient:
    return TestClient(_app(auth), follow_redirects=False)


def _login(client: TestClient, role: str, leaver: str = "kim") -> None:
    token = issue_invite(SECRET, Principal.model_validate({"role": role, "leaver": leaver}))
    response = client.get("/auth/accept", params={"token": token})
    assert response.status_code == 303


def test_public_route_open_without_login(client: TestClient) -> None:
    assert client.get("/health").status_code == 200


def test_protected_route_without_login_is_401(client: TestClient) -> None:
    response = client.get("/coverage", params={"leaver": "kim"})
    assert response.status_code == 401
    assert response.json() == {"detail": LOGIN_REQUIRED}


def test_wrong_role_is_403(client: TestClient) -> None:
    _login(client, "successor")
    response = client.get("/leavers/kim/gaps")
    assert response.status_code == 403
    assert response.json() == {"detail": FORBIDDEN}


def test_unlisted_route_denied_by_default(client: TestClient) -> None:
    assert client.get("/unlisted").status_code == 401
    _login(client, "manager")
    assert client.get("/unlisted").status_code == 403


def test_kb_query_denied_for_every_role(client: TestClient) -> None:
    for role in ("leaver", "successor", "manager"):
        _login(client, role)
        assert client.post("/kb/query", json={}).status_code == 403


def test_none_scope_skips_leaver_check(client: TestClient) -> None:
    _login(client, "successor", "lee")
    assert client.get("/question-trees/ops").status_code == 200


def test_path_scope_matches_leaver(client: TestClient) -> None:
    _login(client, "manager")
    assert client.get("/leavers/kim/gaps").status_code == 200
    assert client.get("/leavers/lee/gaps").status_code == 403


def test_query_scope_and_default(client: TestClient) -> None:
    _login(client, "leaver")
    assert client.get("/coverage", params={"leaver": "kim"}).status_code == 200
    assert client.get("/coverage", params={"leaver": "lee"}).status_code == 403
    assert client.get("/coverage").status_code == 403  # missing → "default"
    _login(client, "leaver", "default")
    assert client.get("/coverage").status_code == 200


def test_body_scope_and_default(client: TestClient) -> None:
    _login(client, "successor")
    ok = client.post("/kb/ask", json={"leaver": "kim", "question": "q"})
    assert ok.status_code == 200
    assert ok.json() == {"leaver": "kim", "question": "q"}
    assert client.post("/kb/ask", json={"leaver": "lee", "question": "q"}).status_code == 403
    assert client.post("/kb/ask", json={"question": "q"}).status_code == 403


def test_body_scope_unparsable_body_is_denied(client: TestClient) -> None:
    _login(client, "successor")
    # Malformed JSON declared as JSON is rejected by FastAPI before any dependency
    # or handler runs, so it never reaches the route.
    declared = client.post(
        "/kb/ask", content=b"not json", headers={"content-type": "application/json"}
    )
    assert declared.status_code == 422
    # Any other unparsable body reaches authorize, which fails closed.
    response = client.post("/kb/ask", content=b"not json", headers={"content-type": "text/plain"})
    assert response.status_code == 403
    assert response.json() == {"detail": FORBIDDEN}


def test_body_scope_non_string_or_non_object_is_denied(client: TestClient) -> None:
    _login(client, "successor")
    assert client.post("/kb/ask", json={"leaver": 7, "question": "q"}).status_code == 403
    assert client.post("/kb/ask", json={"leaver": None, "question": "q"}).status_code == 403
    assert client.post("/kb/ask", json=["kim"]).status_code == 403


def test_body_scope_missing_leaver_is_denied_for_other_leaver(client: TestClient) -> None:
    _login(client, "successor", "kim")
    response = client.post("/kb/ask", json={"question": "q"})
    assert response.status_code == 403
    assert response.json() == {"detail": FORBIDDEN}


def test_body_scope_failures_pass_through_when_auth_off() -> None:
    client = TestClient(_app(None))
    response = client.post(
        "/kb/ask", content=b"not json", headers={"content-type": "application/json"}
    )
    assert response.status_code == 422
    assert client.post("/kb/ask", json={"leaver": 7}).status_code == 200


def test_session_scope_and_missing_session_reaches_handler(client: TestClient) -> None:
    _login(client, "leaver")
    assert client.get("/sessions/s1").status_code == 200
    assert client.get("/sessions/nope").status_code == 404
    _login(client, "leaver", "lee")
    assert client.get("/sessions/s1").status_code == 403


def test_drill_scope_and_missing_drill_reaches_handler(client: TestClient) -> None:
    _login(client, "successor")
    assert client.get("/drills/d1").status_code == 200
    assert client.get("/drills/d9").status_code == 404
    _login(client, "successor", "lee")
    assert client.get("/drills/d1").status_code == 403


def test_head_treated_as_get(client: TestClient) -> None:
    _login(client, "manager")
    assert client.head("/leavers/kim/gaps").status_code == 200
    assert client.head("/leavers/lee/gaps").status_code == 403


def test_auth_off_app_lets_everything_through() -> None:
    client = TestClient(_app(None))
    assert client.post("/kb/query", json={}).status_code == 200
    assert client.get("/leavers/lee/gaps").status_code == 200
    assert client.get("/unlisted").status_code == 200


def test_accept_redirects_and_sets_httponly_cookie(client: TestClient) -> None:
    for role, home in (("leaver", "/"), ("successor", "/chat"), ("manager", "/report")):
        token = issue_invite(SECRET, Principal.model_validate({"role": role, "leaver": "kim"}))
        response = client.get("/auth/accept", params={"token": token})
        assert response.status_code == 303
        assert response.headers["location"] == home
        cookie = response.headers["set-cookie"].lower()
        assert cookie.startswith(f"{SESSION_COOKIE}=")
        assert "httponly" in cookie


def test_accept_forged_or_expired_token_is_401(auth: AuthConfig) -> None:
    client = TestClient(_app(auth), follow_redirects=False)
    forged = issue_invite("other-secret", Principal(role="manager", leaver="kim"))
    response = client.get("/auth/accept", params={"token": forged})
    assert response.status_code == 401
    assert response.json() == {"detail": "invalid invite"}
    expired_app = _app(AuthConfig(secret=SECRET, invite_ttl_s=-1, enforcer=auth.enforcer))
    expired = TestClient(expired_app, follow_redirects=False)
    token = issue_invite(SECRET, Principal(role="manager", leaver="kim"))
    assert expired.get("/auth/accept", params={"token": token}).status_code == 401
    assert "set-cookie" not in response.headers


def test_accept_when_auth_off_is_404() -> None:
    client = TestClient(_app(None), follow_redirects=False)
    response = client.get("/auth/accept", params={"token": "x"})
    assert response.status_code == 404
    assert response.json() == {"detail": "auth disabled"}


def test_me_and_logout(client: TestClient) -> None:
    assert client.get("/auth/me").json() == {"auth": True, "role": None, "leaver": None}
    _login(client, "manager")
    assert client.get("/auth/me").json() == {"auth": True, "role": "manager", "leaver": "kim"}
    assert client.post("/auth/logout").status_code == 204
    assert client.get("/auth/me").json() == {"auth": True, "role": None, "leaver": None}
    assert client.get("/leavers/kim/gaps").status_code == 401


def test_me_and_logout_when_auth_off() -> None:
    client = TestClient(_app(None))
    assert client.get("/auth/me").json() == {"auth": False, "role": None, "leaver": None}
    assert client.post("/auth/logout").status_code == 204


def _cli(args: list[str], tmp_path: Path, auth: str = "on") -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "HANDOFF_DATA_DIR": str(tmp_path),
        "HANDOFF_AUTH_SECRET": SECRET,
        "HANDOFF_AUTH": auth,
        "HANDOFF_PROVIDER": "fake",
    }
    return subprocess.run(
        [sys.executable, "-m", "handoff.rbac.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_cli_prints_accept_link(tmp_path: Path) -> None:
    result = _cli(
        ["invite", "--role", "successor", "--leaver", "kim", "--base-url", "http://h:1/"],
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 1
    url = urlparse(lines[0])
    assert f"{url.scheme}://{url.netloc}{url.path}" == "http://h:1/auth/accept"
    token = parse_qs(url.query)["token"][0]
    assert read_invite(SECRET, token, 60) == Principal(role="successor", leaver="kim")


def test_cli_default_base_url_and_auth_off_warning(tmp_path: Path) -> None:
    result = _cli(["invite", "--role", "leaver", "--leaver", "kim"], tmp_path, auth="off")
    assert result.returncode == 0
    assert result.stdout.startswith("http://localhost:8780/auth/accept?token=")
    assert "warning" in result.stderr.lower()


def test_cli_rejects_bad_role_and_leaver(tmp_path: Path) -> None:
    bad_role = _cli(["invite", "--role", "admin", "--leaver", "kim"], tmp_path)
    assert bad_role.returncode == 2
    assert bad_role.stdout == ""
    bad_leaver = _cli(["invite", "--role", "manager", "--leaver", "Kim Lee"], tmp_path)
    assert bad_leaver.returncode == 2
    assert bad_leaver.stdout == ""
