"""Screen/API routing, static fallback, demo 404 and API docs switches (create_app level)."""

from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from handoff.config import Settings, load_settings
from handoff.main import create_app
from handoff.rbac.tokens import ADMIN_SCOPE, Principal, issue_invite
from handoff.web import STATIC_DIR

SECRET = "t"
INDEX_HTML = "<!doctype html><title>built-index</title><div id=root></div>"

MakeClient = Callable[..., TestClient]


@pytest.fixture()
def dist(tmp_path: Path) -> Path:
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (d / "assets" / "a.js").write_text("console.log(1);", encoding="utf-8")
    return d


@pytest.fixture()
def make_client(tmp_path: Path) -> Iterator[MakeClient]:
    opened: list[TestClient] = []

    def make(**overrides: Any) -> TestClient:
        data = tmp_path / f"data{len(opened)}"
        base = Settings(
            provider="fake",
            data_dir=data,
            min_score=0.25,
            collection="handoff_kb",
            auth=False,
        )
        client = TestClient(create_app(replace(base, **overrides)))
        client.__enter__()
        opened.append(client)
        return client

    yield make
    for c in opened:
        c.__exit__(None, None, None)


def _demo_settings(dist: Path | None) -> dict[str, Any]:
    return {"auth": True, "auth_secret": SECRET, "demo": True, "web_dist": dist}


@pytest.mark.parametrize("path", ["/", "/intro", "/chat", "/report", "/admin"])
def test_screen_paths_serve_index(make_client: MakeClient, dist: Path, path: str) -> None:
    res = make_client(web_dist=dist).get(path)
    assert res.status_code == 200
    assert res.text == INDEX_HTML


def test_demo_screen_serves_index_when_demo_on(make_client: MakeClient, dist: Path) -> None:
    res = make_client(**_demo_settings(dist)).get("/demo")
    assert res.status_code == 200
    assert res.text == INDEX_HTML


def test_demo_off_is_404(make_client: MakeClient, dist: Path) -> None:
    res = make_client(web_dist=dist).get("/demo")
    assert res.status_code == 404
    assert res.json() == {"detail": "demo off"}


def test_admin_api_returns_json_not_index(make_client: MakeClient, dist: Path) -> None:
    client = make_client(auth=True, auth_secret=SECRET, web_dist=dist)
    token = issue_invite(SECRET, Principal(role="admin", leaver=ADMIN_SCOPE))
    res = client.get("/auth/accept", params={"token": token}, follow_redirects=False)
    assert res.status_code == 303
    res = client.get("/admin/leavers")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/json")
    assert res.json() == []


def test_demo_status_is_json(make_client: MakeClient, dist: Path) -> None:
    res = make_client(**_demo_settings(dist)).get("/demo/status")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/json")
    assert isinstance(res.json(), dict)


def test_no_dist_falls_back_to_static_chat(make_client: MakeClient) -> None:
    client = make_client(web_dist=None)
    res = client.get("/chat")
    assert res.status_code == 200
    assert res.text == (STATIC_DIR / "chat.html").read_text(encoding="utf-8")
    assert client.get("/intro").status_code == 404


def test_dist_without_index_falls_back(make_client: MakeClient, tmp_path: Path) -> None:
    empty = tmp_path / "empty-dist"
    empty.mkdir()
    client = make_client(web_dist=empty)
    res = client.get("/chat")
    assert res.text == (STATIC_DIR / "chat.html").read_text(encoding="utf-8")
    assert client.get("/intro").status_code == 404


def test_api_docs_off_returns_404(make_client: MakeClient) -> None:
    client = make_client(api_docs=False)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path


def test_api_docs_on_serves_openapi(make_client: MakeClient) -> None:
    res = make_client(api_docs=True).get("/openapi.json")
    assert res.status_code == 200
    assert "paths" in res.json()


def test_auth_on_defaults_hide_openapi(tmp_path: Path) -> None:
    settings = load_settings(
        {"HANDOFF_AUTH": "on", "HANDOFF_AUTH_SECRET": "t", "HANDOFF_DATA_DIR": str(tmp_path)}
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/openapi.json").status_code == 404


def test_assets_are_served(make_client: MakeClient, dist: Path) -> None:
    res = make_client(web_dist=dist).get("/assets/a.js")
    assert res.status_code == 200
    assert res.text == "console.log(1);"
