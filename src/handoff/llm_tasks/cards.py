"""Typed knowledge card extraction (fake and qwen)."""

from __future__ import annotations

import json

from pydantic import ValidationError

from handoff.contracts import (
    CardDraft,
    CardType,
    ChatFn,
    KBEntry,
    containment,
    split_sentences,
)

TYPE_CUES: tuple[tuple[CardType, tuple[str, ...]], ...] = (
    ("contact", ("연락", "에게 묻", "문의", "채널")),
    ("pitfall", ("안 됩니다", "하지 않", "조심", "위험", "깨지", "실패", "주의", "놓치면")),
    ("decision", ("때문", "결정", "선택", "대신", "이유")),
    ("procedure", ("확인", "실행", "절차", "먼저", "그다음", "올립니다", "보냅니다", "복구", "롤백")),
)
TITLE_MAX = 80
BODY_MAX = 600
QWEN_MAX_TOKENS = 1500
# qwen evidence guard: bigram containment of card text in a cited answer.
MIN_EVIDENCE_OVERLAP = 0.15
EVIDENCE_RELATIVE = 0.5
# An answer "names a contact" when one sentence has both a recipient and a
# hand-off action (ask / notify / pass on).
CONTACT_RECIPIENT_CUES = ("에게", "채널", "담당")
CONTACT_ACTION_CUES = ("연락", "묻", "물어", "알리", "알립", "알려", "넘기", "넘깁", "넘겨",
                       "전달", "공유", "문의", "부탁", "요청", "보고")

EXTRACT_CARDS_SYSTEM = (
    "당신은 퇴사자 인수인계 답변을 지식 카드로 정리하는 도우미입니다. "
    "답변을 읽고 절차(procedure)·함정(pitfall)·결정(decision)·연락처(contact)·엔티티(entity) "
    "다섯 타입의 카드를 만듭니다. "
    "procedure 는 순서대로 하는 일, pitfall 은 하면 안 되는 일·놓치면 생기는 실패·주의할 점, "
    "decision 은 그렇게 정한 이유·선택, contact 는 어떤 역할·채널에 묻고 알리고 넘기는지, "
    "entity 는 시스템·도구·문서입니다. 한 답변에 여러 타입 내용이 있으면 타입마다 카드를 따로 만듭니다. "
    "답변에 묻거나 알리거나 넘기는 역할·채널이 나오면 다른 카드와 별도로 contact 카드도 만듭니다. "
    "카드마다 type, title(80자 이내), body(600자 이내), entities(관련 엔티티 이름 목록), "
    "confidence(0.0~1.0, 답변이 구체적일수록 높게), evidence_question_ids(근거 답변의 질문 id 목록, "
    "1개 이상)를 씁니다. "
    "답변은 [답변 id=...] 머리로 구분됩니다. 카드는 되도록 답변 하나의 내용으로 만들고, "
    "evidence_question_ids 에는 그 카드 내용을 직접 말한 답변의 id 만 그대로 옮겨 씁니다. "
    "같은 묶음에 있다는 이유로 다른 답변 id 를 넣지 않습니다. "
    "근거에 없는 사실·이름을 지어내지 않습니다. 사람 이름 대신 역할명을 씁니다. "
    "이메일·전화번호는 카드에 옮기지 않습니다. "
    'JSON 만 출력합니다. 형식: {"cards": [{"type": "...", "title": "...", "body": "...", '
    '"entities": ["..."], "confidence": 0.8, "evidence_question_ids": ["..."]}]}'
)

CONTACT_CARDS_SYSTEM = (
    "당신은 퇴사자 인수인계 답변에서 연락처(contact) 카드만 뽑는 도우미입니다. "
    "각 답변에서 어떤 역할·채널에 묻고, 알리고, 넘기고, 요청하는지를 찾아 "
    "답변마다 contact 카드를 하나씩 만듭니다. 누가 언제 어느 역할·채널로 무엇을 넘기는지 "
    "답변에 나온 역할명·채널명을 그대로 써서 body 에 적습니다. "
    "카드마다 type(항상 \"contact\"), title(80자 이내), body(600자 이내), entities, "
    "confidence(0.0~1.0), evidence_question_ids(그 연락 대상을 직접 말한 답변의 id 하나)를 씁니다. "
    "답변은 [답변 id=...] 머리로 구분됩니다. 근거에 없는 역할·채널을 지어내지 않습니다. "
    "사람 이름 대신 역할명을 쓰고, 이메일·전화번호는 옮기지 않습니다. "
    'JSON 만 출력합니다. 형식: {"cards": [{"type": "contact", "title": "...", "body": "...", '
    '"entities": ["..."], "confidence": 0.8, "evidence_question_ids": ["..."]}]}'
)


def _names_contact(answer: str) -> bool:
    return any(
        any(cue in sentence for cue in CONTACT_RECIPIENT_CUES)
        and any(cue in sentence for cue in CONTACT_ACTION_CUES)
        for sentence in split_sentences(answer)
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
        lines.append(
            f"[답변 id={entry.question_id}]\n질문: {entry.question}\n답변: {entry.answer}\n"
            f"[답변 id={entry.question_id} 끝]"
        )
    return "\n".join(lines)


def _ground_evidence(drafts: list[CardDraft], entries: list[KBEntry]) -> list[CardDraft]:
    """Point each card's evidence at answers that actually say its content.

    Cited ids whose answer barely overlaps the card text are dropped; the
    best-overlapping answer is added when it is not cited. Cards that overlap
    no answer at all are dropped.
    """
    texts: dict[str, str] = {}
    for entry in entries:
        texts[entry.question_id] = f"{texts.get(entry.question_id, '')} {entry.answer}"
    grounded: list[CardDraft] = []
    for draft in drafts:
        card_text = f"{draft.title} {draft.body}"
        scores = {qid: containment(card_text, text) for qid, text in texts.items()}
        best_qid = max(scores, key=lambda q: scores[q])
        floor = max(MIN_EVIDENCE_OVERLAP, EVIDENCE_RELATIVE * scores[best_qid])
        cited = [q for q in dict.fromkeys(draft.evidence_question_ids) if q in scores]
        kept = [q for q in cited if scores[q] >= floor]
        if scores[best_qid] >= MIN_EVIDENCE_OVERLAP and best_qid not in kept:
            kept.insert(0, best_qid)
        if not kept:
            continue
        grounded.append(draft.model_copy(update={"evidence_question_ids": kept}))
    return grounded


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
    grounded = _ground_evidence(drafts, entries)
    return grounded + _missing_contacts(chat, entries, known_entities, grounded)


def _missing_contacts(
    chat: ChatFn, entries: list[KBEntry], known_entities: list[str], grounded: list[CardDraft]
) -> list[CardDraft]:
    """Ask once more, contact-only, for answers naming a contact but cited by no contact card."""
    covered = {q for d in grounded if d.type == "contact" for q in d.evidence_question_ids}
    targets = [e for e in entries if e.question_id not in covered and _names_contact(e.answer)]
    if not targets:
        return []
    content = chat(
        CONTACT_CARDS_SYSTEM, _user_prompt(targets, known_entities), QWEN_MAX_TOKENS, json_mode=True
    )
    extra = [d for d in _parse(content) or [] if d.type == "contact"]
    return _ground_evidence(extra, targets)
