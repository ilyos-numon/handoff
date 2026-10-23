"""Card-based handoff artifacts (fake and qwen section writers)."""

from __future__ import annotations

import json
import math

from pydantic import ValidationError

from handoff.contracts import (
    ARTIFACT_CARD_TYPES,
    ArtifactKind,
    ArtifactSection,
    CardType,
    ChatFn,
    KnowledgeCard,
)

RUNBOOK_MAX = 15
PITFALLS_MAX = 10
WEEKS = 4
EMPTY_WEEK_BODY = "지난 주차 내용을 실제 업무에 적용해 봅니다."
ONBOARDING_TYPE_ORDER: tuple[CardType, ...] = ("entity", "contact", "procedure", "pitfall", "decision")
QWEN_MAX_TOKENS = 1500

WRITE_ARTIFACT_SYSTEM = (
    "당신은 퇴사자 인수인계 지식 카드로 후임자용 문서를 만드는 도우미입니다. "
    "주어진 카드 내용만 씁니다. 근거에 없는 사실·이름을 지어내지 않습니다. "
    "사람 이름 대신 역할명을 씁니다. "
    "모든 절은 근거로 쓴 카드 id(예: c3)를 card_ids 에 1개 이상 답니다. 목록에 없는 id 는 쓰지 않습니다. "
    '반드시 {"sections": [{"heading": "<제목>", "body": "<본문>", "card_ids": ["<카드 id>"]}]} '
    "형식의 JSON 만 출력합니다."
)

KIND_INSTRUCTIONS: dict[ArtifactKind, str] = {
    "runbook": f"운영 런북을 씁니다. 절차·주의점마다 절 하나, 최대 {RUNBOOK_MAX}개 절.",
    "onboarding_30d": (
        "30일 온보딩 플랜을 씁니다. 정확히 4개 절이고 제목은 순서대로 "
        '"1주차", "2주차", "3주차", "4주차" 로 시작합니다. 앞 주차일수록 시스템·연락처, '
        "뒤 주차일수록 절차·함정·결정을 다룹니다."
    ),
    "pitfalls_top10": (
        f'함정 Top10 을 씁니다. 위험한 순서로 최대 {PITFALLS_MAX}개 절, 제목은 "1. ..." 처럼 번호로 시작합니다.'
    ),
    "adr": '결정 기록(ADR)을 씁니다. 결정마다 절 하나, 제목은 "결정 1: ..." 형식, 본문에 배경과 결정을 씁니다.',
}


def _card_num(card: KnowledgeCard) -> int:
    digits = card.id[1:]
    return int(digits) if digits.isdigit() else 0


def _targets(kind: ArtifactKind, cards: list[KnowledgeCard]) -> list[KnowledgeCard]:
    types = ARTIFACT_CARD_TYPES[kind]
    return sorted((c for c in cards if c.type in types), key=_card_num)


def _onboarding(cards: list[KnowledgeCard]) -> list[ArtifactSection]:
    order = {t: i for i, t in enumerate(ONBOARDING_TYPE_ORDER)}
    ordered = sorted(cards, key=lambda c: (order[c.type], _card_num(c)))
    k = math.ceil(len(ordered) / WEEKS)
    sections: list[ArtifactSection] = []
    for i in range(1, WEEKS + 1):
        chunk = ordered[(i - 1) * k : i * k]
        if chunk:
            body = "\n".join(f"- {c.title}" for c in chunk)
            ids = [c.id for c in chunk]
        else:
            body = EMPTY_WEEK_BODY
            ids = [ordered[-1].id]
        sections.append(ArtifactSection(heading=f"{i}주차", body=body, card_ids=ids))
    return sections


def fake_write_artifact(kind: ArtifactKind, cards: list[KnowledgeCard]) -> list[ArtifactSection]:
    targets = _targets(kind, cards)
    if not targets:
        return []
    if kind == "runbook":
        return [
            ArtifactSection(heading=c.title, body=c.body, card_ids=[c.id])
            for c in targets[:RUNBOOK_MAX]
        ]
    if kind == "pitfalls_top10":
        ranked = sorted(targets, key=lambda c: (-c.confidence, _card_num(c)))[:PITFALLS_MAX]
        return [
            ArtifactSection(heading=f"{i}. {c.title}", body=c.body, card_ids=[c.id])
            for i, c in enumerate(ranked, start=1)
        ]
    if kind == "adr":
        return [
            ArtifactSection(heading=f"결정 {i}: {c.title}", body=f"배경과 결정: {c.body}",
                            card_ids=[c.id])
            for i, c in enumerate(targets, start=1)
        ]
    return _onboarding(targets)


def qwen_write_artifact(
    chat: ChatFn, kind: ArtifactKind, cards: list[KnowledgeCard]
) -> list[ArtifactSection]:
    targets = _targets(kind, cards)
    if not targets:
        return []
    listing = "\n".join(
        f"[{c.id}] ({c.type}, 신뢰도 {c.confidence:.1f}) {c.title}: {c.body}" for c in targets
    )
    user = f"문서 종류: {KIND_INSTRUCTIONS[kind]}\n카드 목록:\n{listing}"
    content = chat(WRITE_ARTIFACT_SYSTEM, user, QWEN_MAX_TOKENS, json_mode=True)
    try:
        parsed = json.loads(content)
    except ValueError:
        return fake_write_artifact(kind, cards)
    raw = parsed.get("sections") if isinstance(parsed, dict) else None
    if not isinstance(raw, list) or not raw:
        return fake_write_artifact(kind, cards)
    try:
        return [ArtifactSection.model_validate(item) for item in raw]
    except ValidationError:
        return fake_write_artifact(kind, cards)
