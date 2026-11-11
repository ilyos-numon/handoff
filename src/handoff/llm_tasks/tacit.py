"""Tacit-knowledge follow-up questions ("just", "by feel" answers -> ask for the criteria)."""

from __future__ import annotations

import json

from handoff.contracts import ChatFn
from handoff.signals import TACIT_FOLLOW_UP_TEMPLATE, TACIT_TARGET_CUES

TACIT_QUESTION_MAX = 150

TACIT_SYSTEM = (
    "당신은 퇴사자 인수인계 인터뷰를 돕는 진행자입니다. "
    "답변에 '그냥', '감으로', '경험상' 처럼 판단 기준을 말하지 않고 넘어가는 표현이 있습니다. "
    "그 표현을 짚어서, 그렇게 판단하는 기준이나 보고 정하는 신호를 묻는 짧은 한국어 꼬리 질문 한 문장을 씁니다. "
    "꼬리 질문에는 '기준', '판단', '신호', '근거' 중 하나를 그대로 넣습니다. "
    "예) 답변이 '화분 물은 그냥 흙 보고 줍니다' 이면 "
    "\"'그냥' 이라고 하셨는데, 흙의 어떤 상태를 기준으로 물 줄 때를 판단하나요?\" 처럼 씁니다. "
    "근거에 없는 사실·이름을 지어내지 않습니다. 사람 이름 대신 역할명을 씁니다. "
    '반드시 {"question": "<꼬리 질문>"} 형식의 JSON 만 출력합니다.'
)


def fake_tacit_follow_up(question: str, answer: str, cues: list[str]) -> str:
    return TACIT_FOLLOW_UP_TEMPLATE.format(cue=cues[0])


def _parse_question(content: str) -> str | None:
    try:
        parsed = json.loads(content)
    except ValueError:
        return None
    text = parsed.get("question") if isinstance(parsed, dict) else None
    if isinstance(text, str) and 0 < len(text.strip()) <= TACIT_QUESTION_MAX:
        return text.strip()
    return None


def qwen_tacit_follow_up(chat: ChatFn, question: str, answer: str, cues: list[str]) -> str:
    cue_list = ", ".join(f"'{cue}'" for cue in cues)
    user = f"질문: {question}\n답변: {answer}\n짚을 표현: {cue_list}"
    text = _parse_question(chat(TACIT_SYSTEM, user, 150, json_mode=True))
    if text is None or not any(cue in text for cue in TACIT_TARGET_CUES):
        return fake_tacit_follow_up(question, answer, cues)
    return text
