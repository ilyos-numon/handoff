"""FastAPI dependency that enforces the role-to-route policy."""

import time
from typing import Any

from fastapi import HTTPException, Request, status
from pydantic import ValidationError

from handoff.rbac.policy import ROUTE_SCOPES, AuthConfig, is_allowed
from handoff.rbac.tokens import ROLES, Principal
from handoff.session import SessionNotFoundError

LOGIN_REQUIRED = "login required"
FORBIDDEN = "forbidden"
DEFAULT_LEAVER = "default"
DEMO_ROLE_HEADER = "X-Handoff-As"


def get_auth(request: Request) -> AuthConfig | None:
    """Return the app's auth configuration, or ``None`` when auth is off."""
    knowledge = getattr(request.app.state, "knowledge", None)
    auth: AuthConfig | None = getattr(knowledge, "auth", None)
    return auth


def _demo_header_role(request: Request, data: Any) -> str | None:
    """Return the ``X-Handoff-As`` role only for a live demo sandbox cookie."""
    role = request.headers.get(DEMO_ROLE_HEADER)
    if role not in ROLES or get_auth(request) is None:
        return None
    knowledge = getattr(request.app.state, "knowledge", None)
    if getattr(knowledge, "demo", None) is None:
        return None
    exp = data.get("demo_exp")
    if data.get("demo") is not True or not isinstance(exp, int) or exp <= time.time():
        return None
    return role


def current_principal(request: Request) -> Principal | None:
    """Return the signed-in principal from the session cookie, if any.

    Inside a live demo sandbox the ``X-Handoff-As`` header picks the role, so the
    three side-by-side panes can share one cookie. Everywhere else it is ignored.
    """
    if "session" not in request.scope:
        return None
    data = request.session
    header_role = _demo_header_role(request, data)
    if header_role is not None:
        try:
            return Principal.model_validate({"role": header_role, "leaver": data.get("leaver")})
        except ValidationError:
            return None
    try:
        return Principal.model_validate({"role": data.get("role"), "leaver": data.get("leaver")})
    except ValidationError:
        return None


def _deny(principal: Principal | None) -> HTTPException:
    if principal is None:
        return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=LOGIN_REQUIRED)
    return HTTPException(status.HTTP_403_FORBIDDEN, detail=FORBIDDEN)


async def _body_leaver(request: Request) -> str | None:
    try:
        payload: Any = await request.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("leaver", DEFAULT_LEAVER)
    return value if isinstance(value, str) else None


def _session_leaver(request: Request) -> str | None:
    session_id = request.path_params.get("session_id")
    if session_id is None:
        return None
    try:
        leaver: str = request.app.state.services.sessions.view(session_id).leaver
    except SessionNotFoundError:
        return None
    return leaver


def _drill_leaver(request: Request) -> str | None:
    drill_id = request.path_params.get("drill_id")
    if drill_id is None:
        return None
    drill = request.app.state.knowledge.store.get_drill(drill_id)
    if drill is None:
        return None
    leaver: str = drill.leaver
    return leaver


async def _target_leaver(request: Request, scope: str) -> str | None:
    if scope == "path":
        value = request.path_params.get("leaver")
        return value if isinstance(value, str) else None
    if scope == "query":
        return request.query_params.get("leaver", DEFAULT_LEAVER)
    if scope == "body":
        return await _body_leaver(request)
    if scope == "session":
        return _session_leaver(request)
    if scope == "drill":
        return _drill_leaver(request)
    return None


async def authorize(request: Request) -> None:
    """Allow the request only if the caller's role and leaver match the route policy."""
    auth = get_auth(request)
    if auth is None:
        return
    principal = current_principal(request)
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if not isinstance(template, str):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=FORBIDDEN)
    method = "GET" if request.method.upper() == "HEAD" else request.method.upper()
    scope = ROUTE_SCOPES.get((method, template))
    if scope is None:
        raise _deny(principal)
    role = principal.role if principal is not None else "anonymous"
    if not is_allowed(auth.enforcer, role, method, template):
        raise _deny(principal)
    if scope in ("public", "none"):
        return
    target = await _target_leaver(request, scope)
    if scope == "body" and target is None:
        # Fail closed: an unreadable body or a non-string leaver cannot be scoped.
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=FORBIDDEN)
    if target is not None and (principal is None or target != principal.leaver):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=FORBIDDEN)
