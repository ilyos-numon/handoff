"""RBAC wired into the full app: policy completeness and end-to-end checks."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient

from handoff.config import Settings
from handoff.main import create_app
from handoff.rbac.deps import FORBIDDEN, LOGIN_REQUIRED
from handoff.rbac.policy import ROUTE_SCOPES, build_enforcer, is_allowed
from handoff.rbac.tokens import Principal, Role, issue_invite

SECRET = "t"
BUILTIN_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}

# Role policy table: (method, template) -> roles allowed among leaver(L), successor(S), manager(M),
# admin(A). Public rows allow every role, admin included (admin inherits anonymous).
ALL = "LSM"
PUB = "LSMA"
POLICY_TABLE: dict[tuple[str, str], str] = {
    ("GET", "/health"): PUB,
    ("GET", "/"): PUB,
    ("GET", "/chat"): PUB,
    ("GET", "/report"): PUB,
    ("GET", "/auth/accept"): PUB,
    ("GET", "/auth/me"): PUB,
    ("POST", "/auth/logout"): PUB,
    ("GET", "/question-trees/{role}"): ALL,
    ("POST", "/sessions"): "L",
    ("GET", "/sessions/{session_id}"): "L",
    ("POST", "/sessions/{session_id}/answers"): "L",
    ("POST", "/sessions/{session_id}/end"): "L",
    ("GET", "/coverage"): "LM",
    ("POST", "/kb/query"): "",
    ("POST", "/leavers/{leaver}/sources"): "M",
    ("POST", "/leavers/{leaver}/sources/mock"): "M",
    ("GET", "/leavers/{leaver}/sources"): "LM",
    ("GET", "/leavers/{leaver}/entities"): "LM",
    ("POST", "/leavers/{leaver}/questions/generate"): "M",
    ("POST", "/depth/judge"): "L",
    ("POST", "/depth/follow-up"): "L",
    ("POST", "/depth/tacit"): "L",
    ("GET", "/sessions/{session_id}/depth"): "L",
    ("GET", "/leavers/{leaver}/cards"): "LS",
    ("GET", "/leavers/{leaver}/cards/at-risk"): "SM",
    ("POST", "/sessions/{session_id}/cards/extract"): "L",
    ("POST", "/leavers/{leaver}/gaps/refresh"): "M",
    ("GET", "/leavers/{leaver}/gaps"): "M",
    ("GET", "/leavers/{leaver}/queue"): "M",
    ("POST", "/leavers/{leaver}/priority-questions"): "M",
    ("POST", "/kb/ask"): "S",
    ("GET", "/leavers/{leaver}/successor-questions"): "SM",
    ("GET", "/leavers/{leaver}/questions/{qid}/thread"): "SM",
    ("POST", "/leavers/{leaver}/artifacts/{kind}"): "M",
    ("GET", "/leavers/{leaver}/artifacts/{kind}"): ALL,
    ("POST", "/leavers/{leaver}/drills"): "S",
    ("GET", "/drills/{drill_id}"): "S",
    ("POST", "/drills/{drill_id}/answers"): "S",
    ("POST", "/kb/fence"): "S",
    ("POST", "/leavers/{leaver}/premortem/questions"): "M",
    ("GET", "/admin"): PUB,
    ("GET", "/admin/leavers"): "A",
    ("POST", "/admin/leavers"): "A",
    ("POST", "/admin/leavers/{leaver_id}/invites"): "A",
    ("GET", "/demo"): PUB,
    ("POST", "/demo/start"): PUB,
    ("POST", "/demo/role"): PUB,
    ("POST", "/demo/reset"): PUB,
    ("GET", "/demo/status"): PUB,
}
PUBLIC = {key for key, scope in ROUTE_SCOPES.items() if scope == "public"}
LETTER: dict[Role, str] = {"leaver": "L", "successor": "S", "manager": "M", "admin": "A"}


def _settings(data_dir: Path, *, auth: bool) -> Settings:
    return Settings(
        provider="fake",
        data_dir=data_dir,
        min_score=0.25,
        collection="handoff_kb",
        auth=auth,
        auth_secret=SECRET if auth else "",
    )


@pytest.fixture()
def auth_client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(_settings(tmp_path, auth=True))) as client:
        yield client


def _login(client: TestClient, role: Role, leaver: str = "kim") -> None:
    token = issue_invite(SECRET, Principal(role=role, leaver=leaver))
    res = client.get("/auth/accept", params={"token": token}, follow_redirects=False)
    assert res.status_code == 303


def _app_routes(tmp_path: Path) -> set[tuple[str, str]]:
    app = create_app(_settings(tmp_path, auth=True))
    found: set[tuple[str, str]] = set()
    for context in iter_route_contexts(app.routes):
        route = context.route
        if not isinstance(route, APIRoute) or route.path in BUILTIN_PATHS:
            continue
        for method in route.methods:
            if method != "HEAD":
                found.add((method, route.path))
    return found


# (a) every APIRoute has a scope, and every scope maps to a real route
def test_every_app_route_has_a_scope_and_back(tmp_path: Path) -> None:
    routes = _app_routes(tmp_path)
    assert routes - set(ROUTE_SCOPES) == set()
    assert set(ROUTE_SCOPES) - routes == set()


def test_policy_table_matches_route_scopes() -> None:
    assert set(POLICY_TABLE) == set(ROUTE_SCOPES)


# (b) casbin decisions match the §4.4 table for every (row, role)
@pytest.mark.parametrize("key", sorted(POLICY_TABLE))
def test_policy_matches_table(key: tuple[str, str]) -> None:
    enforcer = build_enforcer()
    method, template = key
    for role, letter in LETTER.items():
        expected = letter in POLICY_TABLE[key]
        assert is_allowed(enforcer, role, method, template) is expected, (role, key)
    assert is_allowed(enforcer, "anonymous", method, template) is (key in PUBLIC), key


# (c) end-to-end through the real app with auth on
def test_coverage_without_cookie_is_401(auth_client: TestClient) -> None:
    res = auth_client.get("/coverage", params={"leaver": "kim"})
    assert res.status_code == 401
    assert res.json()["detail"] == LOGIN_REQUIRED


def test_public_paths_open_without_cookie(auth_client: TestClient) -> None:
    assert auth_client.get("/health").status_code == 200
    assert auth_client.get("/").status_code == 200
    me = auth_client.get("/auth/me").json()
    assert me == {"auth": True, "role": None, "leaver": None}


def test_manager_cookie_allows_own_coverage_only(auth_client: TestClient) -> None:
    _login(auth_client, "manager")
    assert auth_client.get("/coverage", params={"leaver": "kim"}).status_code == 200
    assert auth_client.get("/coverage", params={"leaver": "lee"}).status_code == 403


@pytest.mark.parametrize(
    "path",
    ["/sessions/s-unknown/depth", "/drills/d-unknown", "/leavers/kim/cards"],
)
def test_manager_cookie_forbidden_paths(auth_client: TestClient, path: str) -> None:
    _login(auth_client, "manager")
    res = auth_client.get(path)
    assert res.status_code == 403
    assert res.json()["detail"] == FORBIDDEN


@pytest.mark.parametrize("role", ["leaver", "successor", "manager"])
def test_kb_query_forbidden_for_every_role(auth_client: TestClient, role: Role) -> None:
    _login(auth_client, role)
    res = auth_client.post("/kb/query", json={"question": "x"})
    assert res.status_code == 403


def test_manager_cannot_start_session(auth_client: TestClient) -> None:
    _login(auth_client, "manager")
    res = auth_client.post("/sessions", json={"role": "backend_developer", "leaver": "kim"})
    assert res.status_code == 403


# (d) default Settings (auth off) keeps the old open behaviour
def test_auth_off_kb_query_open(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, auth=False))) as client:
        res = client.post("/kb/query", json={"question": "x"})
        assert res.status_code == 200
        assert client.get("/auth/me").json() == {"auth": False, "role": None, "leaver": None}
