"""Per-visitor demo sandboxes: registry, scenario seed (steps 1-10) and expiry purge."""

from __future__ import annotations

import secrets
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

from handoff.contracts import MANAGER_REASON, GenerateQuestionsRequest
from handoff.kb import KnowledgeBase
from handoff.knowledge_app import KnowledgeServices
from handoff.session import SessionService
from handoff.verify import guess_axis

SEED_ROLE = "backend_developer"
SEED_PRIORITY_QUESTION = "결제 대행사 장애가 나면 가장 먼저 무엇을 확인하나요?"
SEED_ANSWERS: tuple[str, str, str] = (
    (
        "결제 대행사 장애가 나면 먼저 대행사 상태 페이지를 확인하고, "
        "담당 엔지니어에게 010-1234-5678 로 전화합니다."
    ),
    "정산 배치는 매일 새벽 2시에 돌고, 실패하면 아침 9시 전에 수동으로 다시 돌립니다.",
    "금요일 배포는 피합니다. 작년 3월 금요일 저녁 배포 후 정산 배치가 주말 내내 실패했습니다.",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS demo_sandboxes (
    leaver TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


def utc_now() -> datetime:
    return datetime.now(UTC)


class Sandbox(BaseModel):
    leaver: str
    created_at: datetime
    expires_at: datetime


class SandboxLimitError(RuntimeError):
    """Too many live sandboxes."""


def _sandbox(row: tuple[object, ...]) -> Sandbox:
    return Sandbox(
        leaver=str(row[0]),
        created_at=datetime.fromisoformat(str(row[1])),
        expires_at=datetime.fromisoformat(str(row[2])),
    )


class DemoSandboxes:
    def __init__(
        self, db_path: Path, ttl_s: int, max_live: int, clock: Callable[[], datetime] = utc_now
    ) -> None:
        self._ttl = timedelta(seconds=ttl_s)
        self._max_live = max_live
        self._clock = clock
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def _rows(self) -> list[Sandbox]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT leaver, created_at, expires_at FROM demo_sandboxes"
            ).fetchall()
        return [_sandbox(r) for r in rows]

    def _remove_row(self, leaver: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM demo_sandboxes WHERE leaver = ?", (leaver,))
            self._conn.commit()

    def create(self, seed: Callable[[str], None], purge: Callable[[str], None]) -> Sandbox:
        self.purge_expired(purge)
        now = self._clock()
        box = Sandbox(
            leaver="demo-" + secrets.token_hex(5), created_at=now, expires_at=now + self._ttl
        )
        with self._lock:
            live = self._conn.execute("SELECT COUNT(*) FROM demo_sandboxes").fetchone()[0]
            if int(live) >= self._max_live:
                raise SandboxLimitError(f"{live} demo sandboxes are live")
            self._conn.execute(
                "INSERT INTO demo_sandboxes (leaver, created_at, expires_at) VALUES (?, ?, ?)",
                (box.leaver, box.created_at.isoformat(), box.expires_at.isoformat()),
            )
            self._conn.commit()
        try:
            seed(box.leaver)
        except BaseException:
            try:
                purge(box.leaver)
            finally:
                self._remove_row(box.leaver)
            raise
        return box

    def get(self, leaver: str) -> Sandbox | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT leaver, created_at, expires_at FROM demo_sandboxes WHERE leaver = ?",
                (leaver,),
            ).fetchone()
        if row is None:
            return None
        box = _sandbox(row)
        return box if box.expires_at > self._clock() else None

    def purge_expired(self, purge: Callable[[str], None]) -> int:
        now = self._clock()
        expired = [box.leaver for box in self._rows() if box.expires_at <= now]
        for leaver in expired:
            purge(leaver)
            self._remove_row(leaver)
        return len(expired)

    def delete(self, leaver: str, purge: Callable[[str], None]) -> None:
        purge(leaver)
        self._remove_row(leaver)

    def live_count(self) -> int:
        now = self._clock()
        return sum(1 for box in self._rows() if box.expires_at > now)


def seed_scenario(sessions: SessionService, knowledge: KnowledgeServices, leaver: str) -> None:
    """Replay docs/demo-scenario.md steps 2-10 for ``leaver`` (same calls the screens make)."""
    knowledge.sources.load_mock(leaver)
    knowledge.sources.generate(leaver, GenerateQuestionsRequest.model_validate({"role": SEED_ROLE}))
    knowledge.premortem.enqueue(leaver, SEED_ROLE)
    knowledge.store.enqueue(
        leaver,
        SEED_ROLE,
        "manager",
        guess_axis(SEED_PRIORITY_QUESTION),
        SEED_PRIORITY_QUESTION,
        MANAGER_REASON,
        None,
        utc_now(),
    )
    session_id, _first = sessions.start(SEED_ROLE, leaver)
    for text in SEED_ANSWERS:
        sessions.answer(session_id, text)
    sessions.end(session_id)


def purge_all(kb: KnowledgeBase, knowledge: KnowledgeServices, leaver: str) -> None:
    knowledge.store.purge_leaver(leaver)
    kb.purge_leaver(leaver)
