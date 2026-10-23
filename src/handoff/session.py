"""Interview session service (SQLite-backed state, KB ingest on end)."""

import math
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from handoff.contracts import SessionHooks, is_queue_id, topic_id
from handoff.coverage import CoverageReport, build_coverage
from handoff.kb import KBEntry, KnowledgeBase
from handoff.providers import LLM, ProviderError
from handoff.question_tree import Axis, QuestionTree, load_tree
from handoff.store import SessionRow, SessionStore

QuestionKind = Literal["main", "follow_up", "queued"]

DEFAULT_LEAVER = "default"
LEAVER_PATTERN = r"^[a-z0-9_-]{1,40}$"


class Question(BaseModel):
    id: str
    axis: Axis
    text: str
    kind: QuestionKind
    source: str | None = None
    reason: str | None = None


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
        hooks: SessionHooks | None = None,
    ) -> None:
        self._kb = kb
        self._llm = llm
        self._clock = clock
        self._store = store if store is not None else SessionStore(":memory:")
        self._session_seconds = session_seconds
        self._grace_seconds = grace_seconds
        self._planned = planned
        self._hooks = hooks

    def _start_index(self, tree: QuestionTree, last: SessionRow) -> int:
        current = _current(last)
        if current is None:
            return len(tree.nodes)
        if is_queue_id(current.id):
            return last.node_index
        if current.kind == "main" and not any(
            a.question_id == current.id for a in self._store.answers(last.session_id)
        ):
            return last.node_index
        return last.node_index + 1

    def start(self, role: str, leaver: str = DEFAULT_LEAVER) -> tuple[str, Question]:
        if not re.fullmatch(LEAVER_PATTERN, leaver):
            raise ValueError(f"invalid leaver: must match {LEAVER_PATTERN}")
        tree = load_tree(role)
        session_id = uuid.uuid4().hex
        prev = self._store.list_sessions(leaver, role)
        if len(prev) >= self._planned:
            raise SessionLimitError(f"{leaver} already has {len(prev)} sessions")
        for row in prev:
            if row.ended_at is None:
                self.end(row.session_id)
        index = self._start_index(tree, prev[-1]) if prev else 0
        first: Question | None = None
        if self._hooks is not None:
            item = self._hooks.next_queued(session_id, leaver, role)
            if item is not None:
                first = Question(
                    id=item.id,
                    axis=item.axis,
                    text=item.text,
                    kind="queued",
                    source=item.source,
                    reason=item.reason,
                )
        if first is None:
            if index >= len(tree.nodes):
                raise TreeExhaustedError(f"question tree {role} is exhausted for {leaver}")
            first = _main_question(tree, index)
        now = self._clock()
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
        if current is None or (self._hooks is None and row.node_index >= len(tree.nodes)):
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
        if self._hooks is not None:
            nxt = self._next_with_hooks(self._hooks, row, tree, current, text)
        elif current.kind == "main":
            node = tree.nodes[row.node_index]
            follow = self._llm.follow_up(node.text, text, node.follow_ups)
            if follow is not None:
                nxt = Question(id=follow.id, axis=node.axis, text=follow.text, kind="follow_up")
        if nxt is None and self._hooks is None:
            row.node_index += 1
            if row.node_index < len(tree.nodes):
                nxt = _main_question(tree, row.node_index)
        row.current_json = nxt.model_dump_json() if nxt is not None else None
        self._store.update_session(row)
        if now >= row.deadline:
            return None
        return nxt

    def _next_with_hooks(
        self,
        hooks: SessionHooks,
        row: SessionRow,
        tree: QuestionTree,
        current: Question,
        text: str,
    ) -> Question | None:
        topic = topic_id(current.id)
        tree_topic = row.node_index < len(tree.nodes) and topic == tree.nodes[row.node_index].id
        candidates = tree.nodes[row.node_index].follow_ups if tree_topic else []
        nxt = hooks.after_answer(row.session_id, current, text, candidates)
        if nxt is not None:
            return nxt
        if tree_topic:
            row.node_index += 1
        item = hooks.next_queued(row.session_id, row.leaver, row.role)
        if item is not None:
            return Question(
                id=item.id,
                axis=item.axis,
                text=item.text,
                kind="queued",
                source=item.source,
                reason=item.reason,
            )
        if row.node_index < len(tree.nodes):
            return _main_question(tree, row.node_index)
        return None

    def end(self, session_id: str) -> int:
        row = self._get_open(session_id)
        answers = self._store.answers(session_id)
        ingested = self._kb.add(answers, row.leaver)
        summary = self._llm.summarize(answers)
        row.ended_at = self._clock()
        row.summary = list(summary)
        self._store.update_session(row)
        if self._hooks is not None:
            try:
                self._hooks.on_end(session_id, row.leaver, row.role, answers)
            except ProviderError:
                pass
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
