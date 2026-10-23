"""Typed knowledge cards: extraction from ended sessions and listing."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from handoff.contracts import (
    SESSION_NOT_FOUND,
    SESSION_OPEN,
    CardType,
    Evidence,
    KnowledgeCard,
    KnowledgeConflict,
    KnowledgeNotFound,
    NewCard,
)
from handoff.knowledge_store import KnowledgeStore
from handoff.privacy import scrub_contacts
from handoff.providers import LLM
from handoff.store import SessionStore

EXCERPT_MAX = 200


def utc_now() -> datetime:
    return datetime.now(UTC)


class CardService:
    def __init__(
        self,
        llm: LLM,
        store: KnowledgeStore,
        sessions: SessionStore,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._llm = llm
        self._store = store
        self._sessions = sessions
        self._clock = clock

    def extract_for_session(self, session_id: str) -> list[KnowledgeCard]:
        row = self._sessions.get_session(session_id)
        if row is None:
            raise KnowledgeNotFound(SESSION_NOT_FOUND)
        if row.ended_at is None:
            raise KnowledgeConflict(SESSION_OPEN)
        answers = self._sessions.answers(session_id)
        by_question: dict[str, str] = {}
        for entry in answers:
            by_question.setdefault(entry.question_id, entry.answer)
        known = [e.name for e in self._store.list_entities(row.leaver)]
        drafts = self._llm.extract_cards(answers, known)
        cards: list[NewCard] = []
        for draft in drafts:
            qids = list(dict.fromkeys(q for q in draft.evidence_question_ids if q in by_question))
            if not qids:
                continue
            evidence = [
                Evidence(
                    session_id=session_id,
                    question_id=q,
                    session_number=row.number,
                    excerpt=by_question[q][:EXCERPT_MAX],
                )
                for q in qids
            ]
            cards.append(
                NewCard(
                    leaver=row.leaver,
                    role=row.role,
                    session_id=session_id,
                    type=draft.type,
                    title=scrub_contacts(draft.title),
                    body=scrub_contacts(draft.body),
                    entities=draft.entities,
                    confidence=draft.confidence,
                    evidence=evidence,
                )
            )
        return self._store.replace_session_cards(session_id, cards, self._clock())

    def list(
        self, leaver: str, type: CardType | None, min_confidence: float
    ) -> list[KnowledgeCard]:
        return self._store.list_cards(leaver, type, min_confidence)
