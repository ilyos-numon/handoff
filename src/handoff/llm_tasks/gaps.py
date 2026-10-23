"""Contradiction detection between knowledge cards (implemented in stage S7)."""

from __future__ import annotations

import json
import re
from itertools import combinations

from handoff.contracts import ChatFn, ContradictionDraft, KnowledgeCard

_NUMBER_RE = re.compile(r"\d+")
CONTRADICTION_MAX_TOKENS = 800
CARD_BODY_MAX = 300

CONTRADICTION_SYSTEM = (
    "당신은 인수인계 지식 카드들 사이의 모순을 찾는 검토자입니다. "
    "서로 다른 세션에서 나온 두 카드가 같은 대상에 대해 동시에 참일 수 없는 내용"
    "(다른 숫자·시점·절차·담당 역할 등)을 적은 경우만 모순으로 봅니다. "
    "표현만 다르거나 서로 보완하는 내용은 모순이 아닙니다. "
    "근거에 없는 사실·이름을 지어내지 않습니다. 사람 이름 대신 역할명을 씁니다. "
    '반드시 {"contradictions": [{"card_ids": ["<카드 id>", "<카드 id>"], '
    '"subject": "<대상 이름>", "explanation": "<한국어 한 문장 설명>"}]} '
    "형식의 JSON 만 출력합니다. 모순이 없으면 contradictions 를 빈 배열로 둡니다."
)


def _numbers(text: str) -> set[str]:
    return set(_NUMBER_RE.findall(text))


def _card_num(card_id: str) -> int:
    return int(card_id[1:]) if card_id[1:].isdigit() else 0


def fake_find_contradictions(cards: list[KnowledgeCard]) -> list[ContradictionDraft]:
    ordered = sorted(cards, key=lambda c: (_card_num(c.id), c.id))
    out: list[ContradictionDraft] = []
    for a, b in combinations(ordered, 2):
        if a.session_id == b.session_id:
            continue
        shared = sorted(set(a.entities) & set(b.entities))
        if not shared:
            continue
        na, nb = _numbers(a.body), _numbers(b.body)
        if not na or not nb or na & nb:
            continue
        out.append(
            ContradictionDraft(
                card_ids=[a.id, b.id],
                subject=shared[0],
                explanation=f"세션 카드 {a.id} 와 {b.id} 의 숫자가 다릅니다",
            )
        )
    return out


def _card_line(card: KnowledgeCard) -> str:
    payload = {
        "id": card.id,
        "session_id": card.session_id,
        "type": card.type,
        "title": card.title,
        "body": card.body[:CARD_BODY_MAX],
        "entities": card.entities,
    }
    return json.dumps(payload, ensure_ascii=False)


def _parse_contradictions(
    content: str, cards: list[KnowledgeCard]
) -> list[ContradictionDraft] | None:
    try:
        parsed = json.loads(content)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    items = parsed.get("contradictions")
    if not isinstance(items, list):
        return None
    by_id = {c.id: c for c in cards}
    seen: set[tuple[str, str]] = set()
    out: list[ContradictionDraft] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ids = item.get("card_ids")
        subject = item.get("subject")
        explanation = item.get("explanation")
        if not (isinstance(ids, list) and len(ids) == 2 and all(isinstance(i, str) for i in ids)):
            continue
        if not isinstance(subject, str) or not subject.strip():
            continue
        if not isinstance(explanation, str) or not explanation.strip():
            continue
        a, b = by_id.get(ids[0]), by_id.get(ids[1])
        if a is None or b is None or a.id == b.id or a.session_id == b.session_id:
            continue
        pair = (a.id, b.id) if _card_num(a.id) <= _card_num(b.id) else (b.id, a.id)
        if pair in seen:
            continue
        seen.add(pair)
        out.append(
            ContradictionDraft(
                card_ids=[pair[0], pair[1]],
                subject=subject.strip(),
                explanation=explanation.strip(),
            )
        )
    return out


def qwen_find_contradictions(
    chat: ChatFn, cards: list[KnowledgeCard]
) -> list[ContradictionDraft]:
    if len({c.session_id for c in cards}) < 2:
        return []
    user = "카드 목록 (한 줄에 카드 하나, JSON):\n" + "\n".join(_card_line(c) for c in cards)
    content = chat(CONTRADICTION_SYSTEM, user, CONTRADICTION_MAX_TOKENS, json_mode=True)
    parsed = _parse_contradictions(content, cards)
    if parsed is None:
        return fake_find_contradictions(cards)
    return parsed
