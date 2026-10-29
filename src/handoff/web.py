"""Built web frontend serving: screen pages fall back to the old static HTML."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route

SCREEN_PATHS: tuple[str, ...] = ("/", "/intro", "/chat", "/report", "/admin", "/demo")
INTRO_ROLES: tuple[str, ...] = ("leaver", "successor", "manager", "admin")

STATIC_DIR = Path(__file__).parent / "static"
NO_CACHE = {"Cache-Control": "no-cache"}


def valid_dist(dist: Path | None) -> Path | None:
    """Return dist when it holds an index.html, otherwise None."""
    if dist is not None and (dist / "index.html").is_file():
        return dist
    return None


def _index(dist: Path) -> HTMLResponse:
    return HTMLResponse((dist / "index.html").read_text(encoding="utf-8"), headers=NO_CACHE)


def install_web(app: FastAPI, dist: Path | None) -> Path | None:
    """Record the usable dist on app.state and mount its assets, /intro and /favicon.svg."""
    applied = valid_dist(dist)
    app.state.web_dist = applied
    if applied is None:
        return None

    def intro(_request: Request) -> Response:
        return _index(applied)

    def intro_role(request: Request) -> Response:
        if request.path_params["role"] in INTRO_ROLES:
            return _index(applied)
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    def favicon(_request: Request) -> Response:
        icon = applied / "favicon.svg"
        if not icon.is_file():
            return Response(status_code=404)
        return FileResponse(icon, media_type="image/svg+xml")

    app.mount(
        "/assets",
        StaticFiles(directory=applied / "assets", check_dir=False),
        name="web-assets",
    )
    app.router.routes.append(Route("/intro", intro, methods=["GET"]))
    app.router.routes.append(Route("/intro/{role}", intro_role, methods=["GET"]))
    app.router.routes.append(Route("/favicon.svg", favicon, methods=["GET"]))
    return applied


def page(request: Request, static_name: str) -> HTMLResponse:
    """Serve the built index.html when a dist is installed, else the old static page."""
    dist: Path | None = getattr(request.app.state, "web_dist", None)
    if dist is not None:
        return _index(dist)
    return HTMLResponse((STATIC_DIR / static_name).read_text(encoding="utf-8"))
