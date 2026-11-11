"""Typed knowledge cards: extraction from ended sessions and listing."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from handoff.contracts import (
    CHANGE_EVENT_LABELS,
    SESSION_NOT_FOUND,
    SESSION_OPEN,
    AtRiskCard,
    AtRiskReport,
    CardType,
    Certainty,
    ChangeEvent,
    Evidence,
    InvalidationCondition,
    KnowledgeCard,
    KnowledgeConflict,
    KnowledgeNotFound,
    NewCard,
)
from handoff.knowledge_store import KnowledgeStore
from handoff.llm_tasks.expiry import fake_annotate_invalidation
from handoff.privacy import scrub_contacts
from handoff.providers import LLM, ProviderError
from handoff.signals import UNCERTAIN_CONFIDENCE_MAX, card_uncertain_cues
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
                    excerpt=scrub_contacts(by_question[q])[:EXCERPT_MAX],
                )
                for q in qids
            ]
            title = scrub_contacts(draft.title)
            body = scrub_contacts(draft.body)
            confidence = draft.confidence
            cues = card_uncertain_cues(f"{title} {body}", [by_question[q] for q in qids])
            certainty: Certainty = "certain"
            if cues:
                certainty = "uncertain"
                confidence = min(confidence, UNCERTAIN_CONFIDENCE_MAX)
            cards.append(
                NewCard(
                    leaver=row.leaver,
                    role=row.role,
                    session_id=session_id,
                    type=draft.type,
                    title=title,
                    body=body,
                    entities=draft.entities,
                    confidence=confidence,
                    evidence=evidence,
                    certainty=certainty,
                    uncertain_cues=cues,
                )
            )
        if cards:
            conditions = self._annotate(cards)
            cards = [
                card.model_copy(update={"invalidated_when": conditions[i]})
                for i, card in enumerate(cards)
            ]
        return self._store.replace_session_cards(session_id, cards, self._clock())

    def _annotate(self, cards: list[NewCard]) -> list[list[InvalidationCondition]]:
        try:
            conditions = self._llm.annotate_invalidation(cards)
        except ProviderError:
            return fake_annotate_invalidation(cards)
        if len(conditions) != len(cards):
            return fake_annotate_invalidation(cards)
        return conditions

    def list(
        self,
        leaver: str,
        type: CardType | None,
        min_confidence: float,
        certainty: Certainty | None = None,
    ) -> list[KnowledgeCard]:
        return self._store.list_cards(leaver, type, min_confidence, certainty)

    def at_risk(
        self, leaver: str, event: ChangeEvent, target: str | None = None
    ) -> AtRiskReport:
        risky: list[AtRiskCard] = []
        for card in self._store.list_cards(leaver):
            conditions = [c for c in card.invalidated_when if c.event == event]
            if not conditions:
                continue
            if target is not None and not (
                any(target in name for name in card.entities)
                or target in card.title
                or target in card.body
            ):
                continue
            risky.append(AtRiskCard(card=card, conditions=conditions))
        return AtRiskReport(
            leaver=leaver,
            event=event,
            label=CHANGE_EVENT_LABELS[event],
            target=target,
            cards=risky,
        )
