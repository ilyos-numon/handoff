"""Typed knowledge card extraction (implemented in stage S6)."""

from __future__ import annotations

from handoff.contracts import CardDraft, ChatFn, KBEntry


def fake_extract_cards(entries: list[KBEntry], known_entities: list[str]) -> list[CardDraft]:
    raise NotImplementedError("cards: implemented in stage S6")


def qwen_extract_cards(
    chat: ChatFn, entries: list[KBEntry], known_entities: list[str]
) -> list[CardDraft]:
    raise NotImplementedError("cards: implemented in stage S6")
