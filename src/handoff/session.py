"""Interview session service (in-memory state, KB ingest on end)."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

from handoff.kb import KBEntry, KnowledgeBase
from handoff.providers import LLM
from handoff.question_tree import Axis, QuestionTree, load_tree

QuestionKind = Literal["main", "follow_up"]


class Question(BaseModel):
    id: str
    axis: Axis
    text: str
    kind: QuestionKind


class SessionNotFoundError(LookupError):
    """Raised when a session id is unknown."""


class SessionClosedError(RuntimeError):
    """Raised when a closed session receives an answer or end request."""


@dataclass
class _Session:
    tree: QuestionTree
    node_index: int
    current: Question
    buffer: list[KBEntry] = field(default_factory=list)
    closed: bool = False


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _main_question(tree: QuestionTree, index: int) -> Question:
    node = tree.nodes[index]
    return Question(id=node.id, axis=node.axis, text=node.text, kind="main")


class SessionService:
    def __init__(
        self,
        kb: KnowledgeBase,
        llm: LLM,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._kb = kb
        self._llm = llm
        self._clock = clock
        self._sessions: dict[str, _Session] = {}

    def start(self, role: str) -> tuple[str, Question]:
        tree = load_tree(role)
        session_id = uuid.uuid4().hex
        first = _main_question(tree, 0)
        self._sessions[session_id] = _Session(tree=tree, node_index=0, current=first)
        return session_id, first

    def _get_open(self, session_id: str) -> _Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        if session.closed:
            raise SessionClosedError(session_id)
        return session

    def answer(self, session_id: str, text: str) -> Question | None:
        if text.strip() == "":
            raise ValueError("answer text must not be blank")
        session = self._get_open(session_id)
        if session.node_index >= len(session.tree.nodes):
            return None
        current = session.current
        session.buffer.append(
            KBEntry(
                session_id=session_id,
                question_id=current.id,
                axis=current.axis,
                question=current.text,
                answer=text,
                created_at=self._clock(),
            )
        )
        if current.kind == "main":
            node = session.tree.nodes[session.node_index]
            follow = self._llm.follow_up(node.text, text, node.follow_ups)
            if follow is not None:
                session.current = Question(
                    id=follow.id, axis=node.axis, text=follow.text, kind="follow_up"
                )
                return session.current
        next_index = session.node_index + 1
        session.node_index = next_index
        if next_index >= len(session.tree.nodes):
            return None
        session.current = _main_question(session.tree, next_index)
        return session.current

    def end(self, session_id: str) -> int:
        session = self._get_open(session_id)
        session.closed = True
        return self._kb.add(session.buffer)
