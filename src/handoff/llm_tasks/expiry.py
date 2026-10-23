"""Card invalidation conditions (implemented in stage S5)."""

from __future__ import annotations

from handoff.contracts import ChatFn, InvalidationCondition, NewCard


def fake_annotate_invalidation(cards: list[NewCard]) -> list[list[InvalidationCondition]]:
    raise NotImplementedError("expiry: implemented in stage S5")


def qwen_annotate_invalidation(
    chat: ChatFn, cards: list[NewCard]
) -> list[list[InvalidationCondition]]:
    raise NotImplementedError("expiry: implemented in stage S5")
