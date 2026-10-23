"""SQLite-backed knowledge loop store sharing the session store connection."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime
from typing import TYPE_CHECKING

from handoff.contracts import (
    QUEUE_PRIORITY,
    Artifact,
    ArtifactKind,
    ArtifactSection,
    CardType,
    DepthJudgement,
    DrillScenario,
    Entity,
    EntityDraft,
    Evidence,
    Gap,
    KnowledgeCard,
    NewArtifact,
    NewCard,
    NewDrill,
    NewGap,
    QueueItem,
    QueueSource,
    QueueStatus,
    SourceDoc,
    SourceKind,
)
from handoff.question_tree import Axis

if TYPE_CHECKING:
    from handoff.store import SessionStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS k_sources (id INTEGER PRIMARY KEY AUTOINCREMENT, leaver TEXT NOT NULL, kind TEXT NOT NULL,
  title TEXT NOT NULL, text TEXT NOT NULL, replaced INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS k_entities (id INTEGER PRIMARY KEY AUTOINCREMENT, leaver TEXT NOT NULL, name TEXT NOT NULL,
  type TEXT NOT NULL, source_ids_json TEXT NOT NULL, mentions INTEGER NOT NULL, UNIQUE(leaver, name));
CREATE TABLE IF NOT EXISTS k_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, leaver TEXT NOT NULL, role TEXT NOT NULL,
  source TEXT NOT NULL, axis TEXT NOT NULL, text TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL,
  ref_id TEXT, asked_session_id TEXT, answered_session_id TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS k_depth (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
  question_id TEXT NOT NULL, slots_json TEXT NOT NULL, missing_json TEXT NOT NULL, follow_up_id TEXT,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS k_cards (id INTEGER PRIMARY KEY AUTOINCREMENT, leaver TEXT NOT NULL, role TEXT NOT NULL,
  session_id TEXT NOT NULL, type TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, entities_json TEXT NOT NULL,
  confidence REAL NOT NULL, evidence_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS k_gaps (id INTEGER PRIMARY KEY AUTOINCREMENT, leaver TEXT NOT NULL, kind TEXT NOT NULL,
  topic TEXT NOT NULL, detail TEXT NOT NULL, risk TEXT NOT NULL, card_ids_json TEXT NOT NULL,
  entity_ids_json TEXT NOT NULL, queue_item_id TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS k_artifacts (id INTEGER PRIMARY KEY AUTOINCREMENT, leaver TEXT NOT NULL, kind TEXT NOT NULL,
  title TEXT NOT NULL, sections_json TEXT NOT NULL, markdown TEXT NOT NULL, card_ids_json TEXT NOT NULL,
  generated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS k_drills (id INTEGER PRIMARY KEY AUTOINCREMENT, leaver TEXT NOT NULL, title TEXT NOT NULL,
  situation TEXT NOT NULL, ask TEXT NOT NULL, expected_json TEXT NOT NULL, card_ids_json TEXT NOT NULL,
  created_at TEXT NOT NULL);
"""

_SOURCE_COLS = "id, leaver, kind, title, text, replaced, created_at"
_ENTITY_COLS = "id, leaver, name, type, source_ids_json, mentions"
_QUEUE_COLS = (
    "id, leaver, role, source, axis, text, reason, status, ref_id, "
    "asked_session_id, answered_session_id, created_at"
)
_DEPTH_COLS = "session_id, question_id, slots_json, missing_json, follow_up_id, created_at"
_CARD_COLS = (
    "id, leaver, role, session_id, type, title, body, entities_json, confidence, "
    "evidence_json, created_at"
)
_GAP_COLS = (
    "id, leaver, kind, topic, detail, risk, card_ids_json, entity_ids_json, queue_item_id, created_at"
)
_ARTIFACT_COLS = "id, leaver, kind, title, sections_json, markdown, card_ids_json, generated_at"
_DRILL_COLS = "id, leaver, title, situation, ask, expected_json, card_ids_json, created_at"

Row = tuple[object, ...]


def _parse_id(prefix: str, external_id: str) -> int | None:
    m = re.fullmatch(rf"{prefix}(\d+)", external_id)
    return int(m.group(1)) if m else None


def _dt(value: object) -> datetime:
    return datetime.fromisoformat(str(value))


def _opt(value: object) -> str | None:
    return str(value) if value is not None else None


def _source_from(r: Row) -> SourceDoc:
    return SourceDoc.model_validate(
        {"id": f"src{r[0]}", "leaver": r[1], "kind": r[2], "title": r[3], "text": r[4],
         "replaced": r[5], "created_at": _dt(r[6])}
    )


def _entity_from(r: Row) -> Entity:
    return Entity.model_validate(
        {"id": f"e{r[0]}", "leaver": r[1], "name": r[2], "type": r[3],
         "source_ids": json.loads(str(r[4])), "mentions": r[5]}
    )


def _queue_from(r: Row) -> QueueItem:
    return QueueItem.model_validate(
        {"id": f"q{r[0]}", "leaver": r[1], "role": r[2], "source": r[3], "axis": r[4],
         "text": r[5], "reason": r[6], "status": r[7], "ref_id": r[8],
         "asked_session_id": r[9], "answered_session_id": r[10], "created_at": _dt(r[11])}
    )


def _depth_from(r: Row) -> DepthJudgement:
    return DepthJudgement.model_validate(
        {"session_id": r[0], "question_id": r[1], "slots": json.loads(str(r[2])),
         "missing": json.loads(str(r[3])), "follow_up_id": r[4], "created_at": _dt(r[5])}
    )


def _card_from(r: Row) -> KnowledgeCard:
    return KnowledgeCard.model_validate(
        {"id": f"c{r[0]}", "leaver": r[1], "role": r[2], "session_id": r[3], "type": r[4],
         "title": r[5], "body": r[6], "entities": json.loads(str(r[7])), "confidence": r[8],
         "evidence": json.loads(str(r[9])), "created_at": _dt(r[10])}
    )


def _gap_from(r: Row) -> Gap:
    return Gap.model_validate(
        {"id": f"g{r[0]}", "leaver": r[1], "kind": r[2], "topic": r[3], "detail": r[4],
         "risk": r[5], "card_ids": json.loads(str(r[6])), "entity_ids": json.loads(str(r[7])),
         "queue_item_id": r[8], "created_at": _dt(r[9])}
    )


def _artifact_from(r: Row) -> Artifact:
    return Artifact.model_validate(
        {"id": f"a{r[0]}", "leaver": r[1], "kind": r[2], "title": r[3],
         "sections": json.loads(str(r[4])), "markdown": r[5],
         "card_ids": json.loads(str(r[6])), "generated_at": _dt(r[7])}
    )


def _drill_from(r: Row) -> DrillScenario:
    return DrillScenario.model_validate(
        {"id": f"d{r[0]}", "leaver": r[1], "title": r[2], "situation": r[3], "ask": r[4],
         "expected_points": json.loads(str(r[5])), "card_ids": json.loads(str(r[6])),
         "created_at": _dt(r[7])}
    )


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _evidence_json(evidence: list[Evidence]) -> str:
    return _dumps([e.model_dump() for e in evidence])


def _sections_json(sections: list[ArtifactSection]) -> str:
    return _dumps([s.model_dump() for s in sections])


class KnowledgeStore:
    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    @classmethod
    def from_session_store(cls, store: SessionStore) -> KnowledgeStore:
        conn, lock = store.shared()
        return cls(conn, lock)

    # ---- 자료·엔티티
    def add_source(self, leaver: str, kind: SourceKind, title: str, text: str, replaced: int,
                   now: datetime) -> SourceDoc:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO k_sources (leaver, kind, title, text, replaced, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (leaver, kind, title, text, replaced, now.isoformat()),
            )
            self._conn.commit()
            row = self._conn.execute(
                f"SELECT {_SOURCE_COLS} FROM k_sources WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return _source_from(row)

    def list_sources(self, leaver: str) -> list[SourceDoc]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_SOURCE_COLS} FROM k_sources WHERE leaver = ? ORDER BY id ASC", (leaver,)
            ).fetchall()
        return [_source_from(r) for r in rows]

    def upsert_entity(self, leaver: str, draft: EntityDraft) -> Entity:
        with self._lock:
            existing = self._conn.execute(
                f"SELECT {_ENTITY_COLS} FROM k_entities WHERE leaver = ? AND name = ?",
                (leaver, draft.name),
            ).fetchone()
            if existing is None:
                cur = self._conn.execute(
                    "INSERT INTO k_entities (leaver, name, type, source_ids_json, mentions) "
                    "VALUES (?, ?, ?, ?, 1)",
                    (leaver, draft.name, draft.type, _dumps([draft.source_id])),
                )
                row_id = cur.lastrowid
            else:
                row_id = int(str(existing[0]))
                source_ids = [str(s) for s in json.loads(str(existing[4]))]
                if draft.source_id not in source_ids:
                    source_ids.append(draft.source_id)
                self._conn.execute(
                    "UPDATE k_entities SET source_ids_json = ?, mentions = mentions + 1 WHERE id = ?",
                    (_dumps(source_ids), row_id),
                )
            self._conn.commit()
            row = self._conn.execute(
                f"SELECT {_ENTITY_COLS} FROM k_entities WHERE id = ?", (row_id,)
            ).fetchone()
        return _entity_from(row)

    def list_entities(self, leaver: str) -> list[Entity]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_ENTITY_COLS} FROM k_entities WHERE leaver = ? ORDER BY id ASC", (leaver,)
            ).fetchall()
        return [_entity_from(r) for r in rows]

    # ---- 큐
    def enqueue(self, leaver: str, role: str, source: QueueSource, axis: Axis, text: str,
                reason: str, ref_id: str | None, now: datetime) -> tuple[QueueItem, bool]:
        with self._lock:
            existing = self._conn.execute(
                f"SELECT {_QUEUE_COLS} FROM k_queue WHERE leaver = ? AND text = ? "
                "AND status IN ('pending', 'asked') ORDER BY id ASC LIMIT 1",
                (leaver, text),
            ).fetchone()
            if existing is not None:
                return _queue_from(existing), False
            cur = self._conn.execute(
                "INSERT INTO k_queue (leaver, role, source, axis, text, reason, status, ref_id, "
                "asked_session_id, answered_session_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, NULL, NULL, ?)",
                (leaver, role, source, axis, text, reason, ref_id, now.isoformat()),
            )
            self._conn.commit()
            row = self._conn.execute(
                f"SELECT {_QUEUE_COLS} FROM k_queue WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return _queue_from(row), True

    def get_queue_item(self, item_id: str) -> QueueItem | None:
        n = _parse_id("q", item_id)
        if n is None:
            return None
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_QUEUE_COLS} FROM k_queue WHERE id = ?", (n,)
            ).fetchone()
        return _queue_from(row) if row is not None else None

    def list_queue(self, leaver: str, status: QueueStatus | None = None,
                   source: QueueSource | None = None) -> list[QueueItem]:
        sql = f"SELECT {_QUEUE_COLS} FROM k_queue WHERE leaver = ?"
        params: list[object] = [leaver]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        if source is not None:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY id ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_queue_from(r) for r in rows]

    def next_pending(self, leaver: str, role: str) -> QueueItem | None:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_QUEUE_COLS} FROM k_queue WHERE leaver = ? AND role = ? "
                "AND status = 'pending' ORDER BY id ASC",
                (leaver, role),
            ).fetchall()
        items = [_queue_from(r) for r in rows]
        if not items:
            return None
        return min(items, key=lambda q: (QUEUE_PRIORITY[q.source], int(q.id[1:])))

    def mark_asked(self, item_id: str, session_id: str) -> None:
        self._set_status(item_id, "UPDATE k_queue SET status = 'asked', asked_session_id = ? "
                         "WHERE id = ?", session_id)

    def mark_answered(self, item_id: str, session_id: str) -> None:
        self._set_status(item_id, "UPDATE k_queue SET status = 'answered', answered_session_id = ? "
                         "WHERE id = ?", session_id)

    def _set_status(self, item_id: str, sql: str, session_id: str) -> None:
        n = _parse_id("q", item_id)
        if n is None:
            return
        with self._lock:
            self._conn.execute(sql, (session_id, n))
            self._conn.commit()

    def reset_asked(self, session_id: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE k_queue SET status = 'pending', asked_session_id = NULL "
                "WHERE status = 'asked' AND asked_session_id = ?",
                (session_id,),
            )
            self._conn.commit()
        return cur.rowcount

    def count_asked_in_session(self, session_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM k_queue WHERE asked_session_id = ?", (session_id,)
            ).fetchone()
        return int(row[0])

    # ---- 깊이
    def add_depth(self, j: DepthJudgement) -> None:
        with self._lock:
            self._conn.execute(
                f"INSERT INTO k_depth ({_DEPTH_COLS}) VALUES (?, ?, ?, ?, ?, ?)",
                (j.session_id, j.question_id, _dumps(j.slots), _dumps(j.missing),
                 j.follow_up_id, j.created_at.isoformat()),
            )
            self._conn.commit()

    def list_depth(self, session_id: str) -> list[DepthJudgement]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_DEPTH_COLS} FROM k_depth WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        return [_depth_from(r) for r in rows]

    # ---- 카드
    def replace_session_cards(self, session_id: str, cards: list[NewCard],
                              now: datetime) -> list[KnowledgeCard]:
        with self._lock:
            self._conn.execute("DELETE FROM k_cards WHERE session_id = ?", (session_id,))
            ids: list[int | None] = []
            for c in cards:
                cur = self._conn.execute(
                    "INSERT INTO k_cards (leaver, role, session_id, type, title, body, entities_json, "
                    "confidence, evidence_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (c.leaver, c.role, session_id, c.type, c.title, c.body, _dumps(c.entities),
                     c.confidence, _evidence_json(c.evidence), now.isoformat()),
                )
                ids.append(cur.lastrowid)
            self._conn.commit()
            rows = [
                self._conn.execute(f"SELECT {_CARD_COLS} FROM k_cards WHERE id = ?", (i,)).fetchone()
                for i in ids
            ]
        return [_card_from(r) for r in rows]

    def list_cards(self, leaver: str, type: CardType | None = None,
                   min_confidence: float = 0.0) -> list[KnowledgeCard]:
        sql = f"SELECT {_CARD_COLS} FROM k_cards WHERE leaver = ? AND confidence >= ?"
        params: list[object] = [leaver, min_confidence]
        if type is not None:
            sql += " AND type = ?"
            params.append(type)
        sql += " ORDER BY id ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_card_from(r) for r in rows]

    def get_card(self, card_id: str) -> KnowledgeCard | None:
        n = _parse_id("c", card_id)
        if n is None:
            return None
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_CARD_COLS} FROM k_cards WHERE id = ?", (n,)
            ).fetchone()
        return _card_from(row) if row is not None else None

    # ---- 빈 구멍
    def replace_gaps(self, leaver: str, gaps: list[NewGap], now: datetime) -> list[Gap]:
        with self._lock:
            self._conn.execute("DELETE FROM k_gaps WHERE leaver = ?", (leaver,))
            for g in gaps:
                self._conn.execute(
                    "INSERT INTO k_gaps (leaver, kind, topic, detail, risk, card_ids_json, "
                    "entity_ids_json, queue_item_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (leaver, g.kind, g.topic, g.detail, g.risk, _dumps(g.card_ids),
                     _dumps(g.entity_ids), g.queue_item_id, now.isoformat()),
                )
            self._conn.commit()
            rows = self._conn.execute(
                f"SELECT {_GAP_COLS} FROM k_gaps WHERE leaver = ? ORDER BY id ASC", (leaver,)
            ).fetchall()
        return [_gap_from(r) for r in rows]

    def list_gaps(self, leaver: str) -> list[Gap]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_GAP_COLS} FROM k_gaps WHERE leaver = ? ORDER BY id ASC", (leaver,)
            ).fetchall()
        return [_gap_from(r) for r in rows]

    # ---- 산출물·훈련
    def save_artifact(self, artifact: NewArtifact, now: datetime) -> Artifact:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO k_artifacts (leaver, kind, title, sections_json, markdown, card_ids_json, "
                "generated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (artifact.leaver, artifact.kind, artifact.title, _sections_json(artifact.sections),
                 artifact.markdown, _dumps(artifact.card_ids), now.isoformat()),
            )
            self._conn.commit()
            row = self._conn.execute(
                f"SELECT {_ARTIFACT_COLS} FROM k_artifacts WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return _artifact_from(row)

    def latest_artifact(self, leaver: str, kind: ArtifactKind) -> Artifact | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_ARTIFACT_COLS} FROM k_artifacts WHERE leaver = ? AND kind = ? "
                "ORDER BY id DESC LIMIT 1",
                (leaver, kind),
            ).fetchone()
        return _artifact_from(row) if row is not None else None

    def add_drill(self, drill: NewDrill, now: datetime) -> DrillScenario:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO k_drills (leaver, title, situation, ask, expected_json, card_ids_json, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (drill.leaver, drill.title, drill.situation, drill.ask,
                 _dumps(drill.expected_points), _dumps(drill.card_ids), now.isoformat()),
            )
            self._conn.commit()
            row = self._conn.execute(
                f"SELECT {_DRILL_COLS} FROM k_drills WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return _drill_from(row)

    def get_drill(self, drill_id: str) -> DrillScenario | None:
        n = _parse_id("d", drill_id)
        if n is None:
            return None
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_DRILL_COLS} FROM k_drills WHERE id = ?", (n,)
            ).fetchone()
        return _drill_from(row) if row is not None else None
