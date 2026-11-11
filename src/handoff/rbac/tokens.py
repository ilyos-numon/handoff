"""Signed invite tokens (itsdangerous) and the server secret."""

import os
import secrets
from pathlib import Path
from typing import Annotated, Literal

from itsdangerous import BadData, URLSafeTimedSerializer
from pydantic import BaseModel, StringConstraints, ValidationError, model_validator

Role = Literal["leaver", "successor", "manager", "admin"]
ROLES: tuple[Role, ...] = ("leaver", "successor", "manager")  # invitable roles
ADMIN_ROLE: Role = "admin"
ADMIN_SCOPE = "_admin"  # fixed leaver value of an admin principal; never a real leaver
INVITE_SALT = "handoff-invite"
SECRET_FILE = "auth_secret"
HOME_PATH: dict[Role, str] = {
    "leaver": "/",
    "successor": "/chat",
    "manager": "/report",
    "admin": "/admin",
}


class Principal(BaseModel):
    role: Role
    leaver: Annotated[str, StringConstraints(pattern=r"^[a-z0-9_-]{1,40}$")]

    @model_validator(mode="after")
    def _admin_scope_only_for_admin(self) -> "Principal":
        if (self.role == ADMIN_ROLE) != (self.leaver == ADMIN_SCOPE):
            raise ValueError("the admin role and the admin scope go together")
        return self


class InvalidInvite(ValueError):
    """The invite token is forged, expired, or malformed."""


def load_secret(data_dir: Path, env_secret: str = "") -> str:
    """Return ``env_secret`` if set, else the secret stored in ``data_dir`` (created once)."""
    if env_secret.strip():
        return env_secret.strip()
    path = data_dir / SECRET_FILE
    if path.exists():
        stored = path.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    data_dir.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(secret)
    os.chmod(path, 0o600)
    return secret


def _serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt=INVITE_SALT)


def issue_invite(secret: str, principal: Principal) -> str:
    """Sign ``principal`` into a URL-safe, timestamped token."""
    return _serializer(secret).dumps(principal.model_dump())


def read_invite(secret: str, token: str, max_age_s: int) -> Principal:
    """Verify ``token`` and return its principal, or raise ``InvalidInvite``."""
    try:
        payload = _serializer(secret).loads(token, max_age=max_age_s)
        return Principal.model_validate(payload)
    except (BadData, ValidationError) as exc:
        raise InvalidInvite("invalid or expired invite") from exc
