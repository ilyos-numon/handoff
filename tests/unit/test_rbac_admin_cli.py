import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import APIRouter, Body, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.middleware.sessions import SessionMiddleware

from handoff.rbac import routes_auth
from handoff.rbac.deps import FORBIDDEN, authorize
from handoff.rbac.policy import SESSION_COOKIE, AuthConfig, build_enforcer, is_allowed
from handoff.rbac.tokens import (
    ADMIN_ROLE,
    ADMIN_SCOPE,
    HOME_PATH,
    ROLES,
    Principal,
    issue_invite,
    read_invite,
)

SECRET = "unit-test-secret"


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


def _token_from(stdout: str) -> tuple[str, str]:
    lines = stdout.strip().splitlines()
    assert len(lines) == 1
    url = urlparse(lines[0])
    return f"{url.scheme}://{url.netloc}{url.path}", parse_qs(url.query)["token"][0]


def _app(auth: AuthConfig) -> FastAPI:
    router = APIRouter(dependencies=[Depends(authorize)])

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.post("/sessions")
    def start(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        return payload

    app = FastAPI()
    app.state.knowledge = SimpleNamespace(auth=auth)
    app.add_middleware(SessionMiddleware, secret_key=auth.secret, session_cookie=SESSION_COOKIE)
    app.include_router(routes_auth.router)
    app.include_router(router)
    return app


@pytest.fixture
def client() -> TestClient:
    auth = AuthConfig(secret=SECRET, invite_ttl_s=3600, enforcer=build_enforcer())
    return TestClient(_app(auth), follow_redirects=False)


def _admin_login(client: TestClient) -> None:
    token = issue_invite(SECRET, Principal(role=ADMIN_ROLE, leaver=ADMIN_SCOPE))
    response = client.get("/auth/accept", params={"token": token})
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"


def test_admin_constants() -> None:
    assert ADMIN_ROLE == "admin"
    assert ADMIN_SCOPE == "_admin"
    assert ADMIN_ROLE not in ROLES
    assert HOME_PATH[ADMIN_ROLE] == "/admin"


def test_cli_admin_prints_admin_link(tmp_path: Path) -> None:
    result = _cli(["admin", "--base-url", "http://h:1/"], tmp_path)
    assert result.returncode == 0, result.stderr
    base, token = _token_from(result.stdout)
    assert base == "http://h:1/auth/accept"
    assert read_invite(SECRET, token, 60) == Principal(role="admin", leaver="_admin")


def test_cli_admin_default_base_url(tmp_path: Path) -> None:
    result = _cli(["admin"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("http://localhost:8780/auth/accept?token=")


def test_cli_admin_rejects_leaver_option(tmp_path: Path) -> None:
    result = _cli(["admin", "--leaver", "kim"], tmp_path)
    assert result.returncode == 2
    assert result.stdout == ""


def test_cli_invite_role_admin_still_exit_2(tmp_path: Path) -> None:
    result = _cli(["invite", "--role", "admin", "--leaver", "kim"], tmp_path)
    assert result.returncode == 2
    assert result.stdout == ""


def test_admin_link_accept_redirects_and_me(client: TestClient, tmp_path: Path) -> None:
    result = _cli(["admin"], tmp_path)
    _base, token = _token_from(result.stdout)
    response = client.get("/auth/accept", params={"token": token})
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    me = client.get("/auth/me").json()
    assert me == {"auth": True, "role": "admin", "leaver": "_admin"}


def test_admin_get_health_allowed(client: TestClient) -> None:
    _admin_login(client)
    assert client.get("/health").status_code == 200
    assert is_allowed(build_enforcer(), "admin", "GET", "/health") is True


def test_admin_post_sessions_denied(client: TestClient) -> None:
    _admin_login(client)
    response = client.post("/sessions", json={"leaver": "_admin"})
    assert response.status_code == 403
    assert response.json() == {"detail": FORBIDDEN}
    assert is_allowed(build_enforcer(), "admin", "POST", "/sessions") is False


@pytest.mark.parametrize(
    "path",
    ["/leavers/{leaver}/cards", "/leavers/{leaver}/queue", "/coverage", "/kb/ask"],
)
def test_admin_cannot_read_leaver_data(path: str) -> None:
    enforcer = build_enforcer()
    for method in ("GET", "POST"):
        assert is_allowed(enforcer, "admin", method, path) is False


@pytest.mark.parametrize(
    "payload",
    [
        {"role": "admin", "leaver": "kim"},
        {"role": "leaver", "leaver": "_admin"},
        {"role": "manager", "leaver": "_admin"},
    ],
)
def test_admin_scope_bound_to_admin_role(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        Principal.model_validate(payload)
