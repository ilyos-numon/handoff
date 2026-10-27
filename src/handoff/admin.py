"""Leaver registry and the admin API that issues invite links (links are only returned)."""

import secrets
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, field_validator

from handoff.privacy import scrub_contacts
from handoff.rbac.deps import get_auth
from handoff.rbac.policy import AuthConfig
from handoff.rbac.tokens import Principal, issue_invite
from handoff.web import page

ALIAS_MAX = 30
AUTH_DISABLED = "auth disabled"
LEAVER_NOT_FOUND = "leaver not found"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leavers (
    leaver TEXT PRIMARY KEY,
    alias TEXT,
    created_at TEXT NOT NULL
);
"""


def utc_now() -> datetime:
    return datetime.now(UTC)


class LeaverRecord(BaseModel):
    leaver: str
    alias: str | None
    created_at: datetime


class CreateLeaverRequest(BaseModel):
    alias: str | None = None

    @field_validator("alias")
    @classmethod
    def _alias_ok(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not 1 <= len(value) <= ALIAS_MAX:
            raise ValueError(f"alias must be 1-{ALIAS_MAX} characters")
        if scrub_contacts(value) != value:
            raise ValueError("alias must not contain an email address or phone number")
        return value


class InviteRequest(BaseModel):
    role: Literal["leaver", "successor", "manager"]


class InviteResponse(BaseModel):
    role: str
    leaver: str
    url: str
    expires_at: datetime


class LeaverRegistry:
    def __init__(self, db_path: Path, clock: Callable[[], datetime] = utc_now) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def create(self, alias: str | None) -> LeaverRecord:
        record = LeaverRecord(
            leaver="lv-" + secrets.token_hex(4), alias=alias, created_at=self._clock()
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO leavers (leaver, alias, created_at) VALUES (?, ?, ?)",
                (record.leaver, record.alias, record.created_at.isoformat()),
            )
            self._conn.commit()
        return record

    def list(self) -> list[LeaverRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT leaver, alias, created_at FROM leavers ORDER BY created_at DESC, rowid DESC"
            ).fetchall()
        return [_record(r) for r in rows]

    def get(self, leaver: str) -> LeaverRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT leaver, alias, created_at FROM leavers WHERE leaver = ?", (leaver,)
            ).fetchone()
        return _record(row) if row is not None else None


def _record(row: tuple[object, ...]) -> LeaverRecord:
    return LeaverRecord(
        leaver=str(row[0]),
        alias=str(row[1]) if row[1] is not None else None,
        created_at=datetime.fromisoformat(str(row[2])),
    )


router = APIRouter()


def get_registry(request: Request) -> LeaverRegistry:
    registry: LeaverRegistry | None = getattr(request.app.state.knowledge, "admin", None)
    if get_auth(request) is None or registry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=AUTH_DISABLED)
    return registry


RegistryDep = Annotated[LeaverRegistry, Depends(get_registry)]


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_page(request: Request) -> HTMLResponse:
    return page(request, "admin.html")


@router.get("/admin/leavers", response_model=list[LeaverRecord])
def list_leavers(registry: RegistryDep) -> list[LeaverRecord]:
    return registry.list()


@router.post("/admin/leavers", response_model=LeaverRecord, status_code=status.HTTP_201_CREATED)
def create_leaver(body: CreateLeaverRequest, registry: RegistryDep) -> LeaverRecord:
    return registry.create(body.alias)


@router.post(
    "/admin/leavers/{leaver_id}/invites",
    response_model=InviteResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_invite(
    leaver_id: str, body: InviteRequest, request: Request, registry: RegistryDep
) -> InviteResponse:
    if registry.get(leaver_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=LEAVER_NOT_FOUND)
    auth: AuthConfig | None = get_auth(request)
    if auth is None:  # pragma: no cover - get_registry already refused
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=AUTH_DISABLED)
    token = issue_invite(auth.secret, Principal(role=body.role, leaver=leaver_id))
    public_url: str = getattr(request.app.state.knowledge, "public_url", "")
    base = public_url or str(request.base_url).rstrip("/")
    return InviteResponse(
        role=body.role,
        leaver=leaver_id,
        url=base + "/auth/accept?token=" + token,
        expires_at=utc_now() + timedelta(seconds=auth.invite_ttl_s),
    )
