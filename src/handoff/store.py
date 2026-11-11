"""SQLite-backed session store."""

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from handoff.kb import KBEntry

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    leaver TEXT NOT NULL,
    role TEXT NOT NULL,
    number INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    deadline TEXT NOT NULL,
    ended_at TEXT,
    node_index INTEGER NOT NULL,
    current_json TEXT,
    summary_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    axis TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at TEXT NOT NULL,
    session_number INTEGER
);
"""

_SESSION_COLUMNS = (
    "session_id, leaver, role, number, started_at, deadline, ended_at, "
    "node_index, current_json, summary_json"
)
_ANSWER_COLUMNS = "session_id, question_id, axis, question, answer, created_at, session_number"


@dataclass
class SessionRow:
    session_id: str
    leaver: str
    role: str
    number: int
    started_at: datetime
    deadline: datetime
    ended_at: datetime | None
    node_index: int
    current_json: str | None
    summary: list[str]


def _row_from(r: tuple[object, ...]) -> SessionRow:
    ended = r[6]
    return SessionRow(
        session_id=str(r[0]),
        leaver=str(r[1]),
        role=str(r[2]),
        number=int(str(r[3])),
        started_at=datetime.fromisoformat(str(r[4])),
        deadline=datetime.fromisoformat(str(r[5])),
        ended_at=datetime.fromisoformat(str(ended)) if ended is not None else None,
        node_index=int(str(r[7])),
        current_json=str(r[8]) if r[8] is not None else None,
        summary=[str(s) for s in json.loads(str(r[9]))],
    )


def _entry_from(r: tuple[object, ...]) -> KBEntry:
    return KBEntry.model_validate(
        {
            "session_id": r[0],
            "question_id": r[1],
            "axis": r[2],
            "question": r[3],
            "answer": r[4],
            "created_at": datetime.fromisoformat(str(r[5])),
            "session_number": r[6],
        }
    )


def _session_params(row: SessionRow) -> tuple[object, ...]:
    return (
        row.session_id,
        row.leaver,
        row.role,
        row.number,
        row.started_at.isoformat(),
        row.deadline.isoformat(),
        row.ended_at.isoformat() if row.ended_at is not None else None,
        row.node_index,
        row.current_json,
        json.dumps(row.summary, ensure_ascii=False),
    )


class SessionStore:
    def __init__(self, db_path: Path | str) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def insert_session(self, row: SessionRow) -> None:
        with self._lock:
            self._conn.execute(
                f"INSERT INTO sessions ({_SESSION_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _session_params(row),
            )
            self._conn.commit()

    def update_session(self, row: SessionRow) -> None:
        p = _session_params(row)
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET leaver = ?, role = ?, number = ?, started_at = ?, "
                "deadline = ?, ended_at = ?, node_index = ?, current_json = ?, summary_json = ? "
                "WHERE session_id = ?",
                (*p[1:], p[0]),
            )
            self._conn.commit()

    def get_session(self, session_id: str) -> SessionRow | None:
        with self._lock:
            r = self._conn.execute(
                f"SELECT {_SESSION_COLUMNS} FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return _row_from(r) if r is not None else None

    def list_sessions(self, leaver: str, role: str) -> list[SessionRow]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_SESSION_COLUMNS} FROM sessions WHERE leaver = ? AND role = ? "
                "ORDER BY number ASC",
                (leaver, role),
            ).fetchall()
        return [_row_from(r) for r in rows]

    def add_answer(self, entry: KBEntry) -> None:
        with self._lock:
            self._conn.execute(
                f"INSERT INTO answers ({_ANSWER_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.session_id,
                    entry.question_id,
                    entry.axis,
                    entry.question,
                    entry.answer,
                    entry.created_at.isoformat(),
                    entry.session_number,
                ),
            )
            self._conn.commit()

    def answers(self, session_id: str) -> list[KBEntry]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_ANSWER_COLUMNS} FROM answers WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        return [_entry_from(r) for r in rows]

    def leaver_answers(self, leaver: str, role: str, ended_only: bool) -> list[KBEntry]:
        cols = ", ".join(f"a.{c.strip()}" for c in _ANSWER_COLUMNS.split(","))
        sql = (
            f"SELECT {cols} FROM answers a JOIN sessions s ON a.session_id = s.session_id "
            "WHERE s.leaver = ? AND s.role = ?"
        )
        if ended_only:
            sql += " AND s.ended_at IS NOT NULL"
        sql += " ORDER BY a.id ASC"
        with self._lock:
            rows = self._conn.execute(sql, (leaver, role)).fetchall()
        return [_entry_from(r) for r in rows]

    def shared(self) -> tuple[sqlite3.Connection, threading.Lock]:
        return self._conn, self._lock

    def close(self) -> None:
        with self._lock:
            self._conn.close()
