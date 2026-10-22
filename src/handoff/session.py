"""Interview session service (SQLite-backed state, KB ingest on end)."""

import math
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from handoff.coverage import CoverageReport, build_coverage
from handoff.kb import KBEntry, KnowledgeBase
from handoff.providers import LLM
from handoff.question_tree import Axis, QuestionTree, load_tree
from handoff.store import SessionRow, SessionStore

QuestionKind = Literal["main", "follow_up"]

DEFAULT_LEAVER = "default"
LEAVER_PATTERN = r"^[a-z0-9_-]{1,40}$"


class Question(BaseModel):
    id: str
    axis: Axis
    text: str
    kind: QuestionKind


class SessionNotFoundError(LookupError):
    """Raised when a session id is unknown."""


class SessionClosedError(RuntimeError):
    """Raised when a closed session receives an answer or end request."""


class SessionLimitError(RuntimeError):
    """Raised when the leaver already has the planned number of sessions."""


class TreeExhaustedError(RuntimeError):
    """Raised when every node of the tree has already been covered."""


class SessionTimeOverError(RuntimeError):
    """Raised when an answer arrives after deadline + grace."""


@dataclass(frozen=True)
class SessionView:
    session_id: str
    leaver: str
    role: str
    number: int
    planned: int
    status: Literal["open", "ended"]
    question: Question | None
    remaining_seconds: int
    items_today: int
    items_total: int
    summary: list[str]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _main_question(tree: QuestionTree, index: int) -> Question:
    node = tree.nodes[index]
    return Question(id=node.id, axis=node.axis, text=node.text, kind="main")


def _current(row: SessionRow) -> Question | None:
    if row.current_json is None:
        return None
    return Question.model_validate_json(row.current_json)


class SessionService:
    def __init__(
        self,
        kb: KnowledgeBase,
        llm: LLM,
        clock: Callable[[], datetime] = _utc_now,
        store: SessionStore | None = None,
        session_seconds: int = 900,
        grace_seconds: int = 30,
        planned: int = 10,
    ) -> None:
        self._kb = kb
        self._llm = llm
        self._clock = clock
        self._store = store if store is not None else SessionStore(":memory:")
        self._session_seconds = session_seconds
        self._grace_seconds = grace_seconds
        self._planned = planned

    def _start_index(self, tree: QuestionTree, last: SessionRow) -> int:
        current = _current(last)
        if current is None:
            return len(tree.nodes)
        if current.kind == "main" and not any(
            a.question_id == current.id for a in self._store.answers(last.session_id)
        ):
            return last.node_index
        return last.node_index + 1

    def start(self, role: str, leaver: str = DEFAULT_LEAVER) -> tuple[str, Question]:
        if not re.fullmatch(LEAVER_PATTERN, leaver):
            raise ValueError(f"invalid leaver: must match {LEAVER_PATTERN}")
        tree = load_tree(role)
        prev = self._store.list_sessions(leaver, role)
        if len(prev) >= self._planned:
            raise SessionLimitError(f"{leaver} already has {len(prev)} sessions")
        for row in prev:
            if row.ended_at is None:
                self.end(row.session_id)
        index = self._start_index(tree, prev[-1]) if prev else 0
        if index >= len(tree.nodes):
            raise TreeExhaustedError(f"question tree {role} is exhausted for {leaver}")
        now = self._clock()
        session_id = uuid.uuid4().hex
        first = _main_question(tree, index)
        self._store.insert_session(
            SessionRow(
                session_id=session_id,
                leaver=leaver,
                role=role,
                number=len(prev) + 1,
                started_at=now,
                deadline=now + timedelta(seconds=self._session_seconds),
                ended_at=None,
                node_index=index,
                current_json=first.model_dump_json(),
                summary=[],
            )
        )
        return session_id, first

    def _get(self, session_id: str) -> SessionRow:
        row = self._store.get_session(session_id)
        if row is None:
            raise SessionNotFoundError(session_id)
        return row

    def _get_open(self, session_id: str) -> SessionRow:
        row = self._get(session_id)
        if row.ended_at is not None:
            raise SessionClosedError(session_id)
        return row

    def answer(self, session_id: str, text: str) -> Question | None:
        if text.strip() == "":
            raise ValueError("answer text must not be blank")
        row = self._get_open(session_id)
        now = self._clock()
        if now > row.deadline + timedelta(seconds=self._grace_seconds):
            raise SessionTimeOverError(session_id)
        tree = load_tree(row.role)
        current = _current(row)
        if current is None or row.node_index >= len(tree.nodes):
            return None
        self._store.add_answer(
            KBEntry(
                session_id=session_id,
                question_id=current.id,
                axis=current.axis,
                question=current.text,
                answer=text,
                created_at=now,
                session_number=row.number,
            )
        )
        nxt: Question | None = None
        if current.kind == "main":
            node = tree.nodes[row.node_index]
            follow = self._llm.follow_up(node.text, text, node.follow_ups)
            if follow is not None:
                nxt = Question(id=follow.id, axis=node.axis, text=follow.text, kind="follow_up")
        if nxt is None:
            row.node_index += 1
            if row.node_index < len(tree.nodes):
                nxt = _main_question(tree, row.node_index)
        row.current_json = nxt.model_dump_json() if nxt is not None else None
        self._store.update_session(row)
        if now >= row.deadline:
            return None
        return nxt

    def end(self, session_id: str) -> int:
        row = self._get_open(session_id)
        answers = self._store.answers(session_id)
        ingested = self._kb.add(answers)
        summary = self._llm.summarize(answers)
        row.ended_at = self._clock()
        row.summary = list(summary)
        self._store.update_session(row)
        return ingested

    def view(self, session_id: str) -> SessionView:
        row = self._get(session_id)
        ended = row.ended_at is not None
        if ended:
            remaining = 0
        else:
            remaining = max(0, math.ceil((row.deadline - self._clock()).total_seconds()))
        today = len(self._store.answers(session_id))
        total = len(self._store.leaver_answers(row.leaver, row.role, ended_only=True))
        if not ended:
            total += today
        return SessionView(
            session_id=row.session_id,
            leaver=row.leaver,
            role=row.role,
            number=row.number,
            planned=self._planned,
            status="ended" if ended else "open",
            question=_current(row),
            remaining_seconds=remaining,
            items_today=today,
            items_total=total,
            summary=list(row.summary),
        )

    def coverage(self, leaver: str, role: str) -> CoverageReport:
        tree = load_tree(role)
        sessions = self._store.list_sessions(leaver, role)
        answers = self._store.leaver_answers(leaver, role, ended_only=True)
        return build_coverage(tree, leaver, sessions, answers, self._planned)

    def close(self) -> None:
        self._store.close()
