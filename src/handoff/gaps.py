"""Semantic knowledge gaps (mentioned only, contradiction, bus factor) and the question queue."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from handoff.contracts import (
    CARD_TYPE_AXIS,
    ENTITY_TYPE_AXIS,
    ContradictionDraft,
    Entity,
    GapKind,
    GapReport,
    KnowledgeCard,
    NewGap,
    QueueItem,
    QueueSource,
    QueueStatus,
    UnknownArea,
)
from handoff.llm_tasks.gaps import fake_find_contradictions
from handoff.providers import ProviderError
from handoff.question_tree import Axis
from handoff.signals import UNCERTAIN_CUES

if TYPE_CHECKING:
    from handoff.knowledge_store import KnowledgeStore
    from handoff.providers import LLM
    from handoff.store import SessionStore

MENTIONED_ONLY_HIGH = 3
BUS_FACTOR_MIN_MENTIONS = 2
BUS_FACTOR_DETAIL = "기록이 한 세션에만 있고 대신 맡을 역할이 기록되지 않았습니다."
GAP_QUESTION_TEMPLATES: dict[GapKind, str] = {
    "mentioned_only": "자료에 나온 '{topic}' 은 무엇이고 어떻게 다루나요? 문제가 생기면 어떻게 하나요?",
    "contradiction": (
        "'{topic}' 에 대해 세션마다 다르게 기록되어 있습니다. "
        "지금 기준으로 맞는 내용과 바뀐 이유를 알려 주세요."
    ),
    "bus_factor": (
        "'{topic}' 은 기록이 한 세션에만 있습니다. "
        "이 일을 대신 맡을 수 있는 역할과 그 역할이 먼저 볼 자료는 무엇인가요?"
    ),
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def unknown_areas(cards: list[KnowledgeCard]) -> list[UnknownArea]:
    uncertain = sorted(
        (c for c in cards if c.certainty == "uncertain"), key=lambda c: int(c.id[1:])
    )
    groups: dict[str, list[KnowledgeCard]] = {}
    for c in uncertain:
        groups.setdefault(c.entities[0] if c.entities else c.title, []).append(c)
    areas: list[UnknownArea] = []
    for topic, group in groups.items():
        found = {cue for c in group for cue in c.uncertain_cues}
        cues = [cue for cue in UNCERTAIN_CUES if cue in found]
        areas.append(UnknownArea(
            topic=topic,
            card_ids=[c.id for c in group],
            cues=cues,
            detail=f"답변에 불확실 표현({', '.join(cues)})이 있어 아무도 확실히 모르는 영역입니다.",
        ))
    return areas


class GapService:
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

    def _contradictions(self, cards: list[KnowledgeCard]) -> list[ContradictionDraft]:
        try:
            return self._llm.find_contradictions(cards)
        except ProviderError:
            return fake_find_contradictions(cards)

    def refresh(self, leaver: str, role: str) -> GapReport:
        now = self._clock()
        entities = self._store.list_entities(leaver)
        cards = self._store.list_cards(leaver)
        answers = self._sessions.leaver_answers(leaver, role, ended_only=True)

        def total_mentions(e: Entity) -> int:
            return e.mentions + sum(1 for a in answers if e.name in a.answer)

        # (gap, axis) pairs in output order
        found: list[tuple[NewGap, Axis]] = []

        for e in entities:
            if any(e.name in c.entities for c in cards):
                continue
            total = total_mentions(e)
            found.append((
                NewGap(
                    leaver=leaver,
                    kind="mentioned_only",
                    topic=e.name,
                    detail=f"자료·답변에 {total}번 나왔지만 지식 카드가 없습니다.",
                    risk="high" if total >= MENTIONED_ONLY_HIGH else "medium",
                    entity_ids=[e.id],
                ),
                ENTITY_TYPE_AXIS[e.type],
            ))

        by_id = {c.id: c for c in cards}
        for draft in self._contradictions(cards):
            pair = [by_id[i] for i in draft.card_ids if i in by_id]
            if len(pair) != len(draft.card_ids):
                continue
            found.append((
                NewGap(
                    leaver=leaver,
                    kind="contradiction",
                    topic=draft.subject,
                    detail=draft.explanation,
                    risk="high",
                    card_ids=list(draft.card_ids),
                    entity_ids=[e.id for e in entities if e.name == draft.subject],
                ),
                CARD_TYPE_AXIS[pair[0].type],
            ))

        for e in entities:
            related = [c for c in cards if e.name in c.entities]
            if not related or total_mentions(e) < BUS_FACTOR_MIN_MENTIONS:
                continue
            sessions = {ev.session_id for c in related for ev in c.evidence}
            if len(sessions) != 1 or any(c.type == "contact" for c in related):
                continue
            found.append((
                NewGap(
                    leaver=leaver,
                    kind="bus_factor",
                    topic=e.name,
                    detail=BUS_FACTOR_DETAIL,
                    risk="medium",
                    card_ids=[c.id for c in related],
                    entity_ids=[e.id],
                ),
                ENTITY_TYPE_AXIS[e.type],
            ))

        gaps: list[NewGap] = []
        queued: list[QueueItem] = []
        for gap, axis in found:
            text = GAP_QUESTION_TEMPLATES[gap.kind].format(topic=gap.topic)
            item, created = self._store.enqueue(
                leaver, role, "gap", axis, text, f"빈 구멍: {gap.kind}", None, now
            )
            if created:
                queued.append(item)
            gaps.append(gap.model_copy(update={"queue_item_id": item.id}))

        saved = self._store.replace_gaps(leaver, gaps, now)
        return GapReport(
            leaver=leaver, gaps=saved, queued=queued, unknown_areas=unknown_areas(cards)
        )

    def report(self, leaver: str) -> GapReport:
        return GapReport(
            leaver=leaver,
            gaps=self._store.list_gaps(leaver),
            queued=self._store.list_queue(leaver, source="gap"),
            unknown_areas=unknown_areas(self._store.list_cards(leaver)),
        )

    def queue(
        self, leaver: str, status: QueueStatus | None, source: QueueSource | None
    ) -> list[QueueItem]:
        return self._store.list_queue(leaver, status=status, source=source)
