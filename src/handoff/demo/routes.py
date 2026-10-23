"""Public demo API: start a seeded sandbox, switch roles, reset and report status."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from handoff.demo.sandbox import (
    DemoSandboxes,
    Sandbox,
    SandboxLimitError,
    purge_all,
    seed_scenario,
)
from handoff.rbac.deps import get_auth
from handoff.rbac.tokens import HOME_PATH
from handoff.routes import STATIC_DIR

DEMO_OFF = "demo off"
DEMO_BUSY = "demo busy"
DEMO_SEED_FAILED = "demo seed failed"
DEMO_SESSION_REQUIRED = "demo session required"
START_ROLE: Literal["manager"] = "manager"


@dataclass
class DemoRuntime:
    sandboxes: DemoSandboxes
    llm_stats: Callable[[], dict[str, int]] | None


class RoleRequest(BaseModel):
    role: Literal["leaver", "successor", "manager"]


class StartResponse(BaseModel):
    leaver: str
    role: str
    expires_at: str


class RoleResponse(BaseModel):
    role: str
    home: str


class StatusResponse(BaseModel):
    demo: bool
    leaver: str | None
    role: str | None
    expires_at: str | None
    llm: dict[str, int] | None


router = APIRouter()


def _maybe_runtime(request: Request) -> DemoRuntime | None:
    runtime: DemoRuntime | None = getattr(request.app.state.knowledge, "demo", None)
    if runtime is None or get_auth(request) is None or "session" not in request.scope:
        return None
    return runtime


def get_runtime(request: Request) -> DemoRuntime:
    runtime = _maybe_runtime(request)
    if runtime is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=DEMO_OFF)
    return runtime


RuntimeDep = Annotated[DemoRuntime, Depends(get_runtime)]


def _cookie_alive(request: Request) -> bool:
    data = request.session
    exp = data.get("demo_exp")
    return (
        data.get("demo") is True
        and isinstance(data.get("leaver"), str)
        and isinstance(exp, int)
        and exp > time.time()
    )


def _current_box(request: Request, runtime: DemoRuntime) -> Sandbox | None:
    if not _cookie_alive(request):
        return None
    return runtime.sandboxes.get(str(request.session["leaver"]))


def _purge(request: Request) -> Callable[[str], None]:
    knowledge: Any = request.app.state.knowledge
    kb = request.app.state.services.kb
    return lambda leaver: purge_all(kb, knowledge, leaver)


def _seed(request: Request) -> Callable[[str], None]:
    knowledge: Any = request.app.state.knowledge
    sessions = request.app.state.services.sessions
    return lambda leaver: seed_scenario(sessions, knowledge, leaver)


def _start_response(box: Sandbox, role: str) -> StartResponse:
    return StartResponse(leaver=box.leaver, role=role, expires_at=box.expires_at.isoformat())


def _new_sandbox(request: Request, runtime: DemoRuntime) -> StartResponse:
    try:
        box = runtime.sandboxes.create(seed=_seed(request), purge=_purge(request))
    except SandboxLimitError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=DEMO_BUSY) from None
    except Exception:  # noqa: BLE001 - seed already rolled back; visitor sees a fixed message
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=DEMO_SEED_FAILED) from None
    request.session.clear()
    request.session.update(
        {
            "role": START_ROLE,
            "leaver": box.leaver,
            "demo": True,
            "demo_exp": int(box.expires_at.timestamp()),
        }
    )
    return _start_response(box, START_ROLE)


@router.get("/demo", response_class=HTMLResponse, include_in_schema=False)
def demo_page(_runtime: RuntimeDep) -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "demo.html").read_text(encoding="utf-8"))


@router.post("/demo/start", response_model=StartResponse)
def start(request: Request, runtime: RuntimeDep) -> StartResponse:
    box = _current_box(request, runtime)
    if box is not None:
        return _start_response(box, str(request.session.get("role", START_ROLE)))
    return _new_sandbox(request, runtime)


@router.post("/demo/role", response_model=RoleResponse)
def switch_role(body: RoleRequest, request: Request, runtime: RuntimeDep) -> RoleResponse:
    if _current_box(request, runtime) is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=DEMO_SESSION_REQUIRED)
    request.session["role"] = body.role
    return RoleResponse(role=body.role, home=HOME_PATH[body.role])


@router.post("/demo/reset", response_model=StartResponse)
def reset(request: Request, runtime: RuntimeDep) -> StartResponse:
    if not _cookie_alive(request):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=DEMO_SESSION_REQUIRED)
    runtime.sandboxes.delete(str(request.session["leaver"]), _purge(request))
    return _new_sandbox(request, runtime)


@router.get("/demo/status", response_model=StatusResponse)
def demo_status(request: Request) -> StatusResponse:
    runtime = _maybe_runtime(request)
    if runtime is None:
        return StatusResponse(demo=False, leaver=None, role=None, expires_at=None, llm=None)
    llm = runtime.llm_stats() if runtime.llm_stats is not None else None
    box = _current_box(request, runtime)
    if box is None:
        return StatusResponse(demo=True, leaver=None, role=None, expires_at=None, llm=llm)
    role = request.session.get("role")
    return StatusResponse(
        demo=True,
        leaver=box.leaver,
        role=role if isinstance(role, str) else None,
        expires_at=box.expires_at.isoformat(),
        llm=llm,
    )
