"""Answer depth (slot) judgement and slot follow-ups."""

from __future__ import annotations

import json
from typing import Any, get_args

from handoff.contracts import (
    SLOT_FOLLOW_UP_TEMPLATES,
    SLOT_TARGET_CUES,
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
    "질문과 답변을 읽고 다섯 슬롯을 판정합니다. 판정 근거는 답변 문장뿐입니다. "
    "질문에 이미 적힌 내용, 상식, 짐작으로 슬롯을 채우지 않습니다.\n"
    "슬롯 정의:\n"
    "- why: 답변이 그렇게 하는 이유·목적·하지 않았을 때의 결과를 직접 말한다. "
    "예) '~때문입니다', '~하려고', '안 하면 ~가 밀립니다'.\n"
    "- when: 답변이 그 일을 하는 시점·주기·계기 조건을 말한다. "
    "예) '매주 화요일', '마감 사흘 전', '분기 결산 직후', '오류가 10분 넘게 이어지는 경우'. "
    "답변에 시점·주기·조건이 적혀 있으면 질문과 겹쳐도 filled 입니다. "
    "'~한 뒤' 같은 작업 순서만 있으면 when 이 아닙니다.\n"
    "- who: 답변이 관련된 역할·팀·연락 대상을 이름 붙여 말한다. "
    "예) '보안팀에', '야간 당직자가', '경리 담당에게'.\n"
    "- how: 답변이 구체적 수단(화면·도구·채널·스크립트·문서)이나 단계 순서를 말한다. "
    "예) '관리 화면에서 조회해', '점검 스크립트로', '먼저 A 를 보고 B 를 한다'. "
    "질문의 동사를 되풀이하기만 하면('알립니다', '처리합니다') how 가 아닙니다.\n"
    "- fallback: 원래 방법이 안 되거나 급하거나 담당이 없을 때 대신 쓰는 다른 방법을 말한다. "
    "예) '화면이 안 열리면 쿼리로 뽑는다', '급하면 전화한다'. "
    "결과가 정상인지 아닌지에 따라 판단을 나누는 것은 fallback 이 아닙니다.\n"
    "상태: 답변에 그 내용이 있으면 filled, 없으면 missing 입니다. "
    "다섯 슬롯은 모두 인수인계에 필요하므로 거의 항상 filled 또는 missing 입니다. "
    "not_applicable 은 그 슬롯을 묻는 것 자체가 말이 안 될 때만 씁니다. "
    "확실하지 않으면 missing 입니다. "
    '반드시 {"slots": {"why": "...", "when": "...", "who": "...", "how": "...", "fallback": "..."}} '
    "형식의 JSON 만 출력합니다."
)

SLOT_SYSTEM = (
    "당신은 퇴사자 인수인계 인터뷰를 돕는 진행자입니다. "
    "질문과 답변을 읽고, 지정한 빈 슬롯 하나만 채우도록 묻는 짧은 한국어 꼬리 질문 한 문장을 씁니다. "
    "다른 슬롯을 묻지 않습니다. 꼬리 질문에는 지정한 필수 단어 중 하나를 그대로 넣습니다. "
    "영어 슬롯 이름(why, when, who, how, fallback)은 문장에 쓰지 않습니다. "
    "근거에 없는 사실·이름을 지어내지 않습니다. 사람 이름 대신 역할명을 씁니다. "
    '반드시 {"question": "<꼬리 질문>"} 형식의 JSON 만 출력합니다.'
)

SLOT_MEANINGS: dict[Slot, str] = {
    "why": "그렇게 하는 이유",
    "when": "그 일을 하는 시점·조건",
    "who": "관련 역할·연락 대상",
    "how": "구체적 방법·순서",
    "fallback": "원래 방법이 안 될 때의 대안",
}
SLOT_LABELS: dict[Slot, str] = {
    slot: f"{SLOT_MEANINGS[slot]} (필수 단어: "
    + " 또는 ".join(f"'{cue}'" for cue in SLOT_TARGET_CUES[slot])
    + ")"
    for slot in SLOTS
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


def _on_target(text: str, slot: Slot) -> bool:
    return any(cue in text for cue in SLOT_TARGET_CUES[slot])


def _parse_question(content: str) -> str | None:
    parsed = _load_object(content)
    text = parsed.get("question") if parsed is not None else None
    if isinstance(text, str) and 0 < len(text.strip()) <= SLOT_QUESTION_MAX:
        return text.strip()
    return None


def qwen_slot_follow_up(chat: ChatFn, question: str, answer: str, slot: Slot) -> str:
    user = f"질문: {question}\n답변: {answer}\n빈 슬롯: {SLOT_LABELS[slot]}"
    text = _parse_question(chat(SLOT_SYSTEM, user, 150, json_mode=True))
    if text is None:
        return fake_slot_follow_up(question, answer, slot)
    if _on_target(text, slot):
        return text
    retry = (
        f"{user}\n앞서 쓴 꼬리 질문 \"{text}\" 은 빈 슬롯을 묻지 않았습니다. "
        f"{SLOT_MEANINGS[slot]}만 묻고 필수 단어를 넣어 다시 씁니다."
    )
    again = _parse_question(chat(SLOT_SYSTEM, retry, 150, json_mode=True))
    return again if again is not None else text
