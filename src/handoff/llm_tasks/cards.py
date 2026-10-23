"""Typed knowledge card extraction (fake and qwen)."""

from __future__ import annotations

import json

from pydantic import ValidationError

from handoff.contracts import CardDraft, CardType, ChatFn, KBEntry, split_sentences

TYPE_CUES: tuple[tuple[CardType, tuple[str, ...]], ...] = (
    ("contact", ("연락", "에게 묻", "문의", "채널")),
    ("pitfall", ("안 됩니다", "하지 않", "조심", "위험", "깨지", "실패", "주의", "놓치면")),
    ("decision", ("때문", "결정", "선택", "대신", "이유")),
    ("procedure", ("확인", "실행", "절차", "먼저", "그다음", "올립니다", "보냅니다", "복구", "롤백")),
)
TITLE_MAX = 80
BODY_MAX = 600
QWEN_MAX_TOKENS = 1500

EXTRACT_CARDS_SYSTEM = (
    "당신은 퇴사자 인수인계 답변을 지식 카드로 정리하는 도우미입니다. "
    "답변을 읽고 절차(procedure)·함정(pitfall)·결정(decision)·연락처(contact)·엔티티(entity) "
    "다섯 타입의 카드를 만듭니다. "
    "카드마다 type, title(80자 이내), body(600자 이내), entities(관련 엔티티 이름 목록), "
    "confidence(0.0~1.0, 답변이 구체적일수록 높게), evidence_question_ids(근거 답변의 질문 id 목록, "
    "1개 이상)를 씁니다. "
    "근거에 없는 사실·이름을 지어내지 않습니다. 사람 이름 대신 역할명을 씁니다. "
    "이메일·전화번호는 카드에 옮기지 않습니다. "
    'JSON 만 출력합니다. 형식: {"cards": [{"type": "...", "title": "...", "body": "...", '
    '"entities": ["..."], "confidence": 0.8, "evidence_question_ids": ["..."]}]}'
)


def _classify(sentence: str) -> CardType | None:
    for card_type, cues in TYPE_CUES:
        if any(cue in sentence for cue in cues):
            return card_type
    return None


def fake_extract_cards(entries: list[KBEntry], known_entities: list[str]) -> list[CardDraft]:
    drafts: list[CardDraft] = []
    seen_entities: set[str] = set()
    for entry in entries:
        sentences = split_sentences(entry.answer)
        mentioned = [name for name in known_entities if name in entry.answer]
        grouped: dict[CardType, list[str]] = {}
        for sentence in sentences:
            card_type = _classify(sentence)
            if card_type is not None:
                grouped.setdefault(card_type, []).append(sentence)
        for card_type, group in grouped.items():
            drafts.append(
                CardDraft(
                    type=card_type,
                    title=group[0][:TITLE_MAX],
                    body=" ".join(group)[:BODY_MAX],
                    entities=mentioned,
                    confidence=0.8 if len(group) >= 2 else 0.6,
                    evidence_question_ids=[entry.question_id],
                )
            )
        for name in mentioned:
            if name in seen_entities:
                continue
            seen_entities.add(name)
            first = next((s for s in sentences if name in s), entry.answer)
            drafts.append(
                CardDraft(
                    type="entity",
                    title=name[:TITLE_MAX],
                    body=first[:BODY_MAX],
                    entities=[name],
                    confidence=0.6,
                    evidence_question_ids=[entry.question_id],
                )
            )
    return drafts


def _user_prompt(entries: list[KBEntry], known_entities: list[str]) -> str:
    lines = [f"알려진 엔티티: {', '.join(known_entities) if known_entities else '(없음)'}", "답변:"]
    for entry in entries:
        lines.append(f"- 질문 id: {entry.question_id}\n  질문: {entry.question}\n  답변: {entry.answer}")
    return "\n".join(lines)


def _parse(content: str) -> list[CardDraft] | None:
    try:
        parsed = json.loads(content)
    except ValueError:
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("cards"), list):
        return None
    raw_cards = parsed["cards"]
    drafts: list[CardDraft] = []
    for raw in raw_cards:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        for key, limit in (("title", TITLE_MAX), ("body", BODY_MAX)):
            if isinstance(item.get(key), str):
                item[key] = item[key].strip()[:limit]
        try:
            drafts.append(CardDraft.model_validate(item))
        except ValidationError:
            continue
    if raw_cards and not drafts:
        return None
    return drafts


def qwen_extract_cards(
    chat: ChatFn, entries: list[KBEntry], known_entities: list[str]
) -> list[CardDraft]:
    if not entries:
        return []
    content = chat(
        EXTRACT_CARDS_SYSTEM, _user_prompt(entries, known_entities), QWEN_MAX_TOKENS, json_mode=True
    )
    drafts = _parse(content)
    if drafts is None:
        return fake_extract_cards(entries, known_entities)
    return drafts
