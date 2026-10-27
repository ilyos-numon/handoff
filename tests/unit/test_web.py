"""Built web frontend serving (handoff.web)."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import Route

from handoff.web import STATIC_DIR, install_web, page, valid_dist

INDEX = "<!doctype html><html><body><div id=root></div></body></html>"


def _dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX, encoding="utf-8")
    (dist / "assets" / "a.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return dist


def _app(dist: Path | None) -> tuple[FastAPI, Path | None]:
    app = FastAPI()
    applied = install_web(app, dist)

    @app.get("/chat", response_class=HTMLResponse, include_in_schema=False)
    def chat_page(request: Request) -> HTMLResponse:
        return page(request, "chat.html")

    return app, applied


def test_install_web_serves_intro_assets_and_favicon(tmp_path: Path) -> None:
    dist = _dist(tmp_path)
    app, applied = _app(dist)
    assert applied == dist
    assert app.state.web_dist == dist
    client = TestClient(app)

    intro = client.get("/intro")
    assert intro.status_code == 200
    assert intro.text == INDEX
    assert intro.headers["cache-control"] == "no-cache"

    asset = client.get("/assets/a.js")
    assert asset.status_code == 200
    assert asset.text == "console.log(1)"

    icon = client.get("/favicon.svg")
    assert icon.status_code == 200
    assert icon.text == "<svg/>"


def test_favicon_missing_is_404(tmp_path: Path) -> None:
    dist = _dist(tmp_path)
    (dist / "favicon.svg").unlink()
    app, _ = _app(dist)
    assert TestClient(app).get("/favicon.svg").status_code == 404


def test_dist_without_index_is_ignored(tmp_path: Path) -> None:
    dist = tmp_path / "empty"
    dist.mkdir()
    assert valid_dist(dist) is None
    assert valid_dist(None) is None
    app, applied = _app(dist)
    assert applied is None
    assert app.state.web_dist is None
    client = TestClient(app)
    assert client.get("/intro").status_code == 404
    assert client.get("/assets/a.js").status_code == 404


def test_page_uses_index_when_dist_installed(tmp_path: Path) -> None:
    app, _ = _app(_dist(tmp_path))
    res = TestClient(app).get("/chat")
    assert res.status_code == 200
    assert res.text == INDEX
    assert res.headers["cache-control"] == "no-cache"


def test_page_falls_back_to_static_without_dist() -> None:
    app, _ = _app(None)
    res = TestClient(app).get("/chat")
    assert res.status_code == 200
    assert res.text == (STATIC_DIR / "chat.html").read_text(encoding="utf-8")


def test_page_without_web_dist_state_uses_static() -> None:
    app = FastAPI()

    @app.get("/chat", response_class=HTMLResponse)
    def chat_page(request: Request) -> HTMLResponse:
        return page(request, "chat.html")

    res = TestClient(app).get("/chat")
    assert res.text == (STATIC_DIR / "chat.html").read_text(encoding="utf-8")


def test_intro_is_plain_route_not_api_route(tmp_path: Path) -> None:
    app, _ = _app(_dist(tmp_path))
    routes = [r for r in app.routes if getattr(r, "path", None) in ("/intro", "/favicon.svg")]
    assert len(routes) == 2
    for route in routes:
        assert isinstance(route, Route)
        assert not isinstance(route, APIRoute)
