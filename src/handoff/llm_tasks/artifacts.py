"""Card-based handoff artifacts (fake and qwen section writers)."""

from __future__ import annotations

import json
import math

from pydantic import ValidationError

from handoff.contracts import (
    ARTIFACT_CARD_TYPES,
    NOT_RECORDED_RESPONSE,
    NOT_RECORDED_SIGN,
    PREMORTEM_TOP,
    ArtifactKind,
    ArtifactSection,
    CardType,
    ChatFn,
    KnowledgeCard,
    split_sentences,
)

RUNBOOK_MAX = 15
PITFALLS_MAX = 10
WEEKS = 4
EMPTY_WEEK_BODY = "지난 주차 내용을 실제 업무에 적용해 봅니다."
ONBOARDING_TYPE_ORDER: tuple[CardType, ...] = ("entity", "contact", "procedure", "pitfall", "decision")
QWEN_MAX_TOKENS = 1500
SIGN_CUES = ("징후", "알림", "느려", "늘어", "쌓입", "오류", "지연", "튀")
RESPONSE_CUES = ("확인", "롤백", "재실행", "알리", "연락", "복구", "먼저")
SIGN_LABEL = "징후:"
RESPONSE_LABEL = "대응:"

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
    "premortem": (
        "먼저 터질 일 Top5 를 씁니다. 위험한 순서로 최대 5개 절, 제목은 \"1. ...\" 형식, "
        "본문은 \"징후: ...\" 줄과 \"대응: ...\" 줄 두 줄입니다. "
        "카드에 징후·대응이 없으면 '기록되지 않았습니다' 라고 씁니다."
    ),
}


def _card_num(card: KnowledgeCard) -> int:
    digits = card.id[1:]
    return int(digits) if digits.isdigit() else 0


def _targets(kind: ArtifactKind, cards: list[KnowledgeCard]) -> list[KnowledgeCard]:
    types = ARTIFACT_CARD_TYPES[kind]
    if kind == "premortem":  # 입력 순서 = 위험 순서 (서비스가 정렬해서 넘긴다)
        return [c for c in cards if c.type in types]
    return sorted((c for c in cards if c.type in types), key=_card_num)


def premortem_ok(sections: list[ArtifactSection], card_ids: set[str]) -> bool:
    """1~PREMORTEM_TOP sections, all card ids from the input, every body has 징후/대응 lines."""
    return 1 <= len(sections) <= PREMORTEM_TOP and all(
        set(s.card_ids) <= card_ids and SIGN_LABEL in s.body and RESPONSE_LABEL in s.body
        for s in sections
    )


def _first_with(sentences: list[str], cues: tuple[str, ...], skip: str | None = None) -> str | None:
    return next((x for x in sentences if x != skip and any(cue in x for cue in cues)), None)


def _premortem(targets: list[KnowledgeCard]) -> list[ArtifactSection]:
    bombs = [c for c in targets if c.type == "pitfall"][:PREMORTEM_TOP]
    procs = [c for c in targets if c.type == "procedure"]
    sections: list[ArtifactSection] = []
    for i, bomb in enumerate(bombs, start=1):
        sentences = split_sentences(bomb.body)
        sign = _first_with(sentences, SIGN_CUES)
        ids = [bomb.id]
        proc = next((p for p in procs if set(p.entities) & set(bomb.entities)), None)
        proc_sentences = split_sentences(proc.body) if proc is not None else []
        response: str | None
        if proc is not None and proc_sentences:
            response = proc_sentences[0]
            ids.append(proc.id)
        else:
            response = _first_with(sentences, RESPONSE_CUES, skip=sign)
        body = (f"{SIGN_LABEL} {sign or NOT_RECORDED_SIGN}\n"
                f"{RESPONSE_LABEL} {response or NOT_RECORDED_RESPONSE}")
        sections.append(ArtifactSection(heading=f"{i}. {bomb.title}", body=body, card_ids=ids))
    return sections


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
    if kind == "premortem":
        return _premortem(targets)
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
        sections = [ArtifactSection.model_validate(item) for item in raw]
    except ValidationError:
        return fake_write_artifact(kind, cards)
    if kind == "premortem" and not premortem_ok(sections, {c.id for c in targets}):
        return fake_write_artifact(kind, cards)
    return sections
