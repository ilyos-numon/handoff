"""Contradiction detection between knowledge cards (implemented in stage S7)."""

from __future__ import annotations

from handoff.contracts import ChatFn, ContradictionDraft, KnowledgeCard


def fake_find_contradictions(cards: list[KnowledgeCard]) -> list[ContradictionDraft]:
    raise NotImplementedError("gaps: implemented in stage S7")


def qwen_find_contradictions(
    chat: ChatFn, cards: list[KnowledgeCard]
) -> list[ContradictionDraft]:
    raise NotImplementedError("gaps: implemented in stage S7")
