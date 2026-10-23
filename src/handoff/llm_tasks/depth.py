"""Answer depth (slot) judgement and slot follow-ups (implemented in stage S5)."""

from __future__ import annotations

from handoff.contracts import ChatFn, Slot, SlotJudgement


def fake_judge_depth(question: str, answer: str) -> SlotJudgement:
    raise NotImplementedError("depth: implemented in stage S5")


def qwen_judge_depth(chat: ChatFn, question: str, answer: str) -> SlotJudgement:
    raise NotImplementedError("depth: implemented in stage S5")


def fake_slot_follow_up(question: str, answer: str, slot: Slot) -> str:
    raise NotImplementedError("depth: implemented in stage S5")


def qwen_slot_follow_up(chat: ChatFn, question: str, answer: str, slot: Slot) -> str:
    raise NotImplementedError("depth: implemented in stage S5")
