"""Incident drill generation and review (implemented in stage S10)."""

from __future__ import annotations

from handoff.contracts import ChatFn, DrillDraft, DrillFeedbackDraft, DrillScenario, KnowledgeCard


def fake_make_drill(cards: list[KnowledgeCard], seed: KnowledgeCard | None) -> DrillDraft:
    raise NotImplementedError("drill: implemented in stage S10")


def qwen_make_drill(
    chat: ChatFn, cards: list[KnowledgeCard], seed: KnowledgeCard | None
) -> DrillDraft:
    raise NotImplementedError("drill: implemented in stage S10")


def fake_review_drill(
    drill: DrillScenario, cards: list[KnowledgeCard], answer: str
) -> DrillFeedbackDraft:
    raise NotImplementedError("drill: implemented in stage S10")


def qwen_review_drill(
    chat: ChatFn, drill: DrillScenario, cards: list[KnowledgeCard], answer: str
) -> DrillFeedbackDraft:
    raise NotImplementedError("drill: implemented in stage S10")
