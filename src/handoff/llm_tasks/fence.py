"""Chesterton's fence explanations (implemented in stage S8)."""

from __future__ import annotations

from handoff.contracts import ChatFn, FenceDraft, KnowledgeCard


def fake_explain_fence(subject: str, cards: list[KnowledgeCard]) -> FenceDraft:
    raise NotImplementedError("fence: implemented in stage S8")


def qwen_explain_fence(chat: ChatFn, subject: str, cards: list[KnowledgeCard]) -> FenceDraft:
    raise NotImplementedError("fence: implemented in stage S8")
