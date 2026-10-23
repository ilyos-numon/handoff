"""Answer depth (slot) judgement and slot follow-ups."""

from __future__ import annotations

import json
from typing import Any, get_args

from handoff.contracts import (
    SLOT_FOLLOW_UP_TEMPLATES,
    SLOTS,
    ChatFn,
    Slot,
    SlotJudgement,
    SlotState,
    judge_slots_by_cues,
)

SLOT_QUESTION_MAX = 150

JUDGE_SYSTEM = (
    "당신은 퇴사자 인수인계 답변이 후임에게 쓸모 있을 만큼 깊은지 판정하는 도우미입니다. "
    "질문과 답변을 읽고 다섯 슬롯을 판정합니다: "
    "why(이유), when(시점·조건), who(관련 역할·연락 대상), how(방법·순서), fallback(안 될 때 대안). "
    "답변에 그 내용이 있으면 filled, 질문 주제상 필요한데 없으면 missing, "
    "주제상 해당하지 않으면 not_applicable 입니다. "
    "근거에 없는 사실·이름을 지어내지 않습니다. 사람 이름 대신 역할명을 씁니다. "
    '반드시 {"slots": {"why": "...", "when": "...", "who": "...", "how": "...", "fallback": "..."}} '
    "형식의 JSON 만 출력합니다."
)

SLOT_SYSTEM = (
    "당신은 퇴사자 인수인계 인터뷰를 돕는 진행자입니다. "
    "질문과 답변을 읽고, 지정한 빈 슬롯을 채우도록 묻는 짧은 한국어 꼬리 질문 한 문장을 씁니다. "
    "근거에 없는 사실·이름을 지어내지 않습니다. 사람 이름 대신 역할명을 씁니다. "
    '반드시 {"question": "<꼬리 질문>"} 형식의 JSON 만 출력합니다.'
)

SLOT_LABELS: dict[Slot, str] = {
    "why": "why(이유)",
    "when": "when(언제·어떤 조건에서)",
    "who": "who(관련 역할·누구에게)",
    "how": "how(방법·순서)",
    "fallback": "fallback(안 될 때 대안)",
}

_STATES: tuple[str, ...] = get_args(SlotState)


def _load_object(content: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(content)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def fake_judge_depth(question: str, answer: str) -> SlotJudgement:
    return judge_slots_by_cues(answer)


def qwen_judge_depth(chat: ChatFn, question: str, answer: str) -> SlotJudgement:
    content = chat(JUDGE_SYSTEM, f"질문: {question}\n답변: {answer}", 200, json_mode=True)
    parsed = _load_object(content)
    raw = parsed.get("slots") if parsed is not None else None
    if not isinstance(raw, dict):
        return fake_judge_depth(question, answer)
    slots: dict[Slot, SlotState] = {}
    for slot in SLOTS:
        state = raw.get(slot)
        if not isinstance(state, str) or state not in _STATES:
            return fake_judge_depth(question, answer)
        slots[slot] = state  # type: ignore[assignment]
    return SlotJudgement(slots=slots, missing=[s for s in SLOTS if slots[s] == "missing"])


def fake_slot_follow_up(question: str, answer: str, slot: Slot) -> str:
    return SLOT_FOLLOW_UP_TEMPLATES[slot]


def qwen_slot_follow_up(chat: ChatFn, question: str, answer: str, slot: Slot) -> str:
    user = f"질문: {question}\n답변: {answer}\n빈 슬롯: {SLOT_LABELS[slot]}"
    content = chat(SLOT_SYSTEM, user, 150, json_mode=True)
    parsed = _load_object(content)
    text = parsed.get("question") if parsed is not None else None
    if isinstance(text, str) and 0 < len(text.strip()) <= SLOT_QUESTION_MAX:
        return text.strip()
    return fake_slot_follow_up(question, answer, slot)
