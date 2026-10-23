"""Leaver registry and the admin invite API (PLAN §4.5)."""

import re
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from handoff.admin import LeaverRegistry
from handoff.config import Settings
from handoff.main import create_app
from handoff.rbac.deps import FORBIDDEN, LOGIN_REQUIRED
from handoff.rbac.tokens import ADMIN_SCOPE, HOME_PATH, Principal, Role, issue_invite

SECRET = "t"
LEAVER_ID = re.compile(r"^lv-[0-9a-f]{8}$")


def _settings(data_dir: Path, **overrides: Any) -> Settings:
    base = Settings(
        provider="fake",
        data_dir=data_dir,
        min_score=0.25,
        collection="handoff_kb",
        auth=True,
        auth_secret=SECRET,
    )
    return replace(base, **overrides)


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(_settings(tmp_path))) as c:
        yield c


def _login(client: TestClient, role: Role, leaver: str) -> None:
    token = issue_invite(SECRET, Principal(role=role, leaver=leaver))
    res = client.get("/auth/accept", params={"token": token}, follow_redirects=False)
    assert res.status_code == 303


def _admin(client: TestClient) -> None:
    _login(client, "admin", ADMIN_SCOPE)


# --- registry ---------------------------------------------------------------


def test_registry_create_get_list(tmp_path: Path) -> None:
    times = iter(datetime(2026, 10, 23, 9, i, tzinfo=UTC) for i in range(10))
    registry = LeaverRegistry(tmp_path / "admin.db", clock=lambda: next(times))
    first = registry.create("결제 담당")
    second = registry.create(None)
    assert LEAVER_ID.match(first.leaver)
    assert LEAVER_ID.match(second.leaver)
    assert first.leaver != second.leaver
    assert registry.get(first.leaver) == first
    assert registry.get("lv-00000000") is None
    assert [r.leaver for r in registry.list()] == [second.leaver, first.leaver]
    assert second.alias is None


def test_registry_persists_across_instances(tmp_path: Path) -> None:
    created = LeaverRegistry(tmp_path / "admin.db").create("정산")
    again = LeaverRegistry(tmp_path / "admin.db")
    assert again.get(created.leaver) == created


# --- API --------------------------------------------------------------------


def test_create_leaver_201(client: TestClient) -> None:
    _admin(client)
    res = client.post("/admin/leavers", json={"alias": "  결제 담당  "})
    assert res.status_code == 201
    body = res.json()
    assert LEAVER_ID.match(body["leaver"])
    assert body["alias"] == "결제 담당"
    assert body["created_at"]


def test_create_leaver_without_alias(client: TestClient) -> None:
    _admin(client)
    res = client.post("/admin/leavers", json={})
    assert res.status_code == 201
    assert res.json()["alias"] is None


@pytest.mark.parametrize(
    "alias",
    ["kim@example.com", "010-1234-5678", "연락처 01012345678", "가" * 31, "   "],
)
def test_create_leaver_bad_alias_422(client: TestClient, alias: str) -> None:
    _admin(client)
    res = client.post("/admin/leavers", json={"alias": alias})
    assert res.status_code == 422


def test_alias_30_chars_ok(client: TestClient) -> None:
    _admin(client)
    assert client.post("/admin/leavers", json={"alias": "가" * 30}).status_code == 201


def test_list_newest_first(client: TestClient) -> None:
    _admin(client)
    ids = [
        client.post("/admin/leavers", json={"alias": f"a{i}"}).json()["leaver"] for i in range(3)
    ]
    res = client.get("/admin/leavers")
    assert res.status_code == 200
    assert [r["leaver"] for r in res.json()] == list(reversed(ids))


@pytest.mark.parametrize("role", ["leaver", "successor", "manager"])
def test_issue_invite_and_accept(client: TestClient, role: Role) -> None:
    _admin(client)
    leaver = client.post("/admin/leavers", json={"alias": "정산"}).json()["leaver"]
    before = datetime.now(UTC)
    res = client.post(f"/admin/leavers/{leaver}/invites", json={"role": role})
    assert res.status_code == 201
    body = res.json()
    assert body["role"] == role
    assert body["leaver"] == leaver
    assert body["url"].startswith("http://testserver/auth/accept?token=")
    expires = datetime.fromisoformat(body["expires_at"])
    ttl = timedelta(hours=72)
    assert before + ttl - timedelta(seconds=5) <= expires <= datetime.now(UTC) + ttl

    parts = urlsplit(body["url"])
    accepted = client.get(f"{parts.path}?{parts.query}", follow_redirects=False)
    assert accepted.status_code == 303
    assert accepted.headers["location"] == HOME_PATH[role]
    assert client.get("/auth/me").json() == {"auth": True, "role": role, "leaver": leaver}


def test_public_url_wins(tmp_path: Path) -> None:
    settings = _settings(tmp_path, public_url="https://handoff-demo.example")
    with TestClient(create_app(settings)) as c:
        _admin(c)
        leaver = c.post("/admin/leavers", json={}).json()["leaver"]
        res = c.post(f"/admin/leavers/{leaver}/invites", json={"role": "manager"})
        assert res.status_code == 201
        assert res.json()["url"].startswith("https://handoff-demo.example/auth/accept?token=")


def test_invite_unknown_leaver_404(client: TestClient) -> None:
    _admin(client)
    res = client.post("/admin/leavers/lv-00000000/invites", json={"role": "manager"})
    assert res.status_code == 404
    assert res.json() == {"detail": "leaver not found"}


def test_invite_admin_role_rejected(client: TestClient) -> None:
    _admin(client)
    leaver = client.post("/admin/leavers", json={}).json()["leaver"]
    res = client.post(f"/admin/leavers/{leaver}/invites", json={"role": "admin"})
    assert res.status_code == 422


@pytest.mark.parametrize("role", ["leaver", "successor", "manager"])
def test_other_roles_forbidden_on_admin_api(client: TestClient, role: Role) -> None:
    _login(client, role, "kim")
    for res in (
        client.get("/admin/leavers"),
        client.post("/admin/leavers", json={}),
        client.post("/admin/leavers/lv-00000000/invites", json={"role": "manager"}),
    ):
        assert res.status_code == 403
        assert res.json()["detail"] == FORBIDDEN


def test_anonymous_admin_api_401(client: TestClient) -> None:
    res = client.get("/admin/leavers")
    assert res.status_code == 401
    assert res.json()["detail"] == LOGIN_REQUIRED


def test_admin_cannot_read_leaver_data(client: TestClient) -> None:
    _admin(client)
    leaver = client.post("/admin/leavers", json={}).json()["leaver"]
    for path in (
        f"/leavers/{leaver}/queue",
        f"/leavers/{leaver}/cards",
        f"/leavers/{leaver}/gaps",
        f"/coverage?leaver={leaver}",
    ):
        res = client.get(path)
        assert res.status_code == 403, path


def test_auth_off_admin_api_404(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, auth=False, auth_secret=""))) as c:
        for res in (
            c.get("/admin/leavers"),
            c.post("/admin/leavers", json={}),
            c.post("/admin/leavers/lv-00000000/invites", json={"role": "manager"}),
        ):
            assert res.status_code == 404
            assert res.json() == {"detail": "auth disabled"}
