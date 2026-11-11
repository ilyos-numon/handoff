"""Incident drill service: scenarios from cards and self-check feedback (feedback only, no grading)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from handoff.contracts import (
    CARD_NOT_FOUND,
    DRILL_NOT_FOUND,
    NO_CARDS,
    DrillAnswerRequest,
    DrillCreateRequest,
    DrillFeedback,
    DrillScenario,
    KnowledgeConflict,
    KnowledgeNotFound,
    NewDrill,
)
from handoff.knowledge_store import KnowledgeStore
from handoff.llm_tasks.drill import DRILL_CARD_TYPES, fake_make_drill
from handoff.providers import LLM


def utc_now() -> datetime:
    return datetime.now(UTC)


class DrillService:
    def __init__(
        self, llm: LLM, store: KnowledgeStore, clock: Callable[[], datetime] = utc_now
    ) -> None:
        self._llm = llm
        self._store = store
        self._clock = clock

    def create(self, leaver: str, req: DrillCreateRequest) -> DrillScenario:
        cards = self._store.list_cards(leaver)
        seed = None
        if req.seed_card_id is not None:
            seed = next((c for c in cards if c.id == req.seed_card_id), None)
            if seed is None:
                raise KnowledgeNotFound(CARD_NOT_FOUND)
        candidates = [c for c in cards if c.type in DRILL_CARD_TYPES]
        if not candidates:
            raise KnowledgeConflict(NO_CARDS)
        draft = self._llm.make_drill(candidates, seed)
        known = {c.id for c in cards}
        card_ids = list(dict.fromkeys(i for i in draft.card_ids if i in known))
        if not card_ids:
            draft = fake_make_drill(candidates, seed)
            card_ids = list(draft.card_ids)
        if seed is not None and seed.id not in card_ids:
            card_ids.insert(0, seed.id)
        new = NewDrill(
            leaver=leaver,
            title=draft.title,
            situation=draft.situation,
            ask=draft.ask,
            expected_points=draft.expected_points,
            card_ids=card_ids,
        )
        return self._store.add_drill(new, self._clock())

    def get(self, drill_id: str) -> DrillScenario:
        drill = self._store.get_drill(drill_id)
        if drill is None:
            raise KnowledgeNotFound(DRILL_NOT_FOUND)
        return drill

    def review(self, drill_id: str, req: DrillAnswerRequest) -> DrillFeedback:
        drill = self.get(drill_id)
        cards = [c for c in (self._store.get_card(i) for i in drill.card_ids) if c is not None]
        draft = self._llm.review_drill(drill, cards, req.text)
        claimed = set(draft.covered_points)
        covered = [p for p in drill.expected_points if p in claimed]
        missed = [p for p in drill.expected_points if p not in claimed]
        return DrillFeedback(
            drill_id=drill.id,
            covered_points=covered,
            missed_points=missed,
            feedback=draft.feedback,
            card_refs=list(drill.card_ids),
        )
