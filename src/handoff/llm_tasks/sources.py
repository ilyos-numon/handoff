"""Entity extraction and tailored question generation (implemented in stage S4)."""

from __future__ import annotations

from handoff.contracts import ChatFn, Entity, EntityDraft, GeneratedQuestion, SourceDoc


def fake_extract_entities(docs: list[SourceDoc]) -> list[EntityDraft]:
    raise NotImplementedError("sources: implemented in stage S4")


def qwen_extract_entities(chat: ChatFn, docs: list[SourceDoc]) -> list[EntityDraft]:
    raise NotImplementedError("sources: implemented in stage S4")


def fake_generate_questions(entities: list[Entity], limit: int) -> list[GeneratedQuestion]:
    raise NotImplementedError("sources: implemented in stage S4")


def qwen_generate_questions(
    chat: ChatFn, entities: list[Entity], limit: int
) -> list[GeneratedQuestion]:
    raise NotImplementedError("sources: implemented in stage S4")
