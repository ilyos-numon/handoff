"""Role-to-route policy backed by pycasbin (model.conf + policy.csv)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import casbin

from handoff.config import Settings
from handoff.rbac.tokens import load_secret

Scope = Literal["public", "none", "path", "query", "body", "session", "drill"]

POLICY_DIR: Path = Path(__file__).parent
SESSION_COOKIE = "handoff_session"
SESSION_MAX_AGE_S = 7 * 24 * 3600

ROUTE_SCOPES: dict[tuple[str, str], Scope] = {
    ("GET", "/health"): "public",
    ("GET", "/"): "public",
    ("GET", "/chat"): "public",
    ("GET", "/report"): "public",
    ("GET", "/auth/accept"): "public",
    ("GET", "/auth/me"): "public",
    ("POST", "/auth/logout"): "public",
    ("GET", "/question-trees/{role}"): "none",
    ("POST", "/sessions"): "body",
    ("GET", "/sessions/{session_id}"): "session",
    ("POST", "/sessions/{session_id}/answers"): "session",
    ("POST", "/sessions/{session_id}/end"): "session",
    ("GET", "/coverage"): "query",
    ("POST", "/kb/query"): "none",
    ("POST", "/leavers/{leaver}/sources"): "path",
    ("POST", "/leavers/{leaver}/sources/mock"): "path",
    ("GET", "/leavers/{leaver}/sources"): "path",
    ("GET", "/leavers/{leaver}/entities"): "path",
    ("POST", "/leavers/{leaver}/questions/generate"): "path",
    ("POST", "/depth/judge"): "none",
    ("POST", "/depth/follow-up"): "none",
    ("POST", "/depth/tacit"): "none",
    ("GET", "/sessions/{session_id}/depth"): "session",
    ("GET", "/leavers/{leaver}/cards"): "path",
    ("GET", "/leavers/{leaver}/cards/at-risk"): "path",
    ("POST", "/sessions/{session_id}/cards/extract"): "session",
    ("POST", "/leavers/{leaver}/gaps/refresh"): "path",
    ("GET", "/leavers/{leaver}/gaps"): "path",
    ("GET", "/leavers/{leaver}/queue"): "path",
    ("POST", "/leavers/{leaver}/priority-questions"): "path",
    ("POST", "/kb/ask"): "body",
    ("GET", "/leavers/{leaver}/successor-questions"): "path",
    ("POST", "/leavers/{leaver}/artifacts/{kind}"): "path",
    ("GET", "/leavers/{leaver}/artifacts/{kind}"): "path",
    ("POST", "/leavers/{leaver}/drills"): "path",
    ("GET", "/drills/{drill_id}"): "drill",
    ("POST", "/drills/{drill_id}/answers"): "drill",
    ("POST", "/kb/fence"): "body",
    ("POST", "/leavers/{leaver}/premortem/questions"): "path",
}


@dataclass(frozen=True)
class AuthConfig:
    secret: str
    invite_ttl_s: int
    enforcer: casbin.Enforcer


def build_enforcer() -> casbin.Enforcer:
    """Load the packaged casbin model and policy."""
    return casbin.Enforcer(str(POLICY_DIR / "model.conf"), str(POLICY_DIR / "policy.csv"))


def build_auth(settings: Settings) -> AuthConfig | None:
    """Return the auth configuration, or ``None`` when auth is off."""
    if not settings.auth:
        return None
    return AuthConfig(
        secret=load_secret(settings.data_dir, settings.auth_secret),
        invite_ttl_s=settings.invite_ttl_hours * 3600,
        enforcer=build_enforcer(),
    )


def is_allowed(enforcer: casbin.Enforcer, role: str, method: str, path: str) -> bool:
    """Return whether ``role`` may call ``method`` on route template ``path`` (HEAD = GET)."""
    act = "GET" if method.upper() == "HEAD" else method.upper()
    return bool(enforcer.enforce(role, path, act))
