"""Chesterton's fence explanations (fake and qwen)."""

from __future__ import annotations

import json

from pydantic import ValidationError

from handoff.contracts import ChatFn, FenceDraft, FencePoint, KnowledgeCard, split_sentences
from handoff.signals import FENCE_POINTS_MAX

REASON_CUES: tuple[str, ...] = ("때문", "이유", "위해", "하려고", "덕분")
RISK_CUES: tuple[str, ...] = ("안 됩니다", "하지 않", "깨지", "실패", "위험", "밀립", "놓치면", "장애")
POINT_TEXT_MAX = 300
QWEN_MAX_TOKENS = 900

EXPLAIN_FENCE_SYSTEM = (
    "당신은 퇴사자 인수인계 지식 카드로 '왜 지금 이렇게 되어 있는지' 를 설명하는 도우미입니다. "
    "후임이 바꾸거나 없애려는 설정·절차(주제)와 번호가 붙은 카드 목록을 읽고 두 가지를 씁니다.\n"
    "1. reasons: 그 주제가 지금 방식으로 되어 있는 이유. 카드에 이유가 직접 적혀 있을 때만 씁니다.\n"
    "2. risks: 그 주제를 바꾸거나 없애면 깨지는 것·생기는 위험. 카드에 직접 적혀 있을 때만 씁니다.\n"
    "근거에 없는 사실·이름을 지어내지 않습니다. 카드에 이유나 위험이 없으면 빈 목록을 씁니다. "
    "사람 이름 대신 역할명을 씁니다. "
    "각 항목의 text 는 카드 문장을 거의 그대로 옮긴 한 문장(300자 이내)이고, "
    "card_ids 에는 그 문장의 근거 카드 id(예: c3)를 1개 이상 씁니다. 목록에 없는 id 는 쓰지 않습니다. "
    "예: 주제가 '도서관 열람실 밤 10시 소등' 이고 카드에 '야간 경비 순찰이 10시에 끝나기 때문에 "
    "소등합니다' 가 있으면 reasons 에 그 문장을, '소등을 늦추면 순찰 없이 문이 열려 있어 분실이 "
    "생깁니다' 가 있으면 risks 에 그 문장을 씁니다. "
    '반드시 {"reasons": [{"text": "<문장>", "card_ids": ["<카드 id>"]}], '
    '"risks": [{"text": "<문장>", "card_ids": ["<카드 id>"]}]} 형식의 JSON 만 출력합니다.'
)


def _add(points: list[FencePoint], text: str, card_id: str) -> None:
    if len(points) >= FENCE_POINTS_MAX or any(p.text == text for p in points):
        return
    points.append(FencePoint(text=text, card_ids=[card_id]))


def fake_explain_fence(subject: str, cards: list[KnowledgeCard]) -> FenceDraft:
    reasons: list[FencePoint] = []
    risks: list[FencePoint] = []
    for card in cards:
        for sentence in split_sentences(card.body):
            text = sentence[:POINT_TEXT_MAX]
            if any(cue in sentence for cue in REASON_CUES) or card.type == "decision":
                _add(reasons, text, card.id)
            elif any(cue in sentence for cue in RISK_CUES) or card.type == "pitfall":
                _add(risks, text, card.id)
    return FenceDraft(reasons=reasons, risks=risks)


def qwen_explain_fence(chat: ChatFn, subject: str, cards: list[KnowledgeCard]) -> FenceDraft:
    if not cards:
        return FenceDraft()
    listing = "\n".join(f"[{c.id}] ({c.type}) {c.title}: {c.body}" for c in cards)
    user = f"주제: {subject}\n카드 목록:\n{listing}"
    content = chat(EXPLAIN_FENCE_SYSTEM, user, QWEN_MAX_TOKENS, json_mode=True)
    try:
        parsed = json.loads(content)
    except ValueError:
        return fake_explain_fence(subject, cards)
    if not isinstance(parsed, dict):
        return fake_explain_fence(subject, cards)
    try:
        return FenceDraft.model_validate(
            {"reasons": parsed.get("reasons", []), "risks": parsed.get("risks", [])}
        )
    except ValidationError:
        return fake_explain_fence(subject, cards)
