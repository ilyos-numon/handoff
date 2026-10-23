"""Tacit-knowledge follow-up questions (implemented in stage S4)."""

from __future__ import annotations

from handoff.contracts import ChatFn


def fake_tacit_follow_up(question: str, answer: str, cues: list[str]) -> str:
    raise NotImplementedError("tacit: implemented in stage S4")


def qwen_tacit_follow_up(chat: ChatFn, question: str, answer: str, cues: list[str]) -> str:
    raise NotImplementedError("tacit: implemented in stage S4")
