"""Card invalidation conditions: which organisational changes make a card wrong."""

from __future__ import annotations

import json

from pydantic import ValidationError

from handoff.contracts import CHANGE_EVENT_LABELS, ChatFn, InvalidationCondition, NewCard
from handoff.signals import fake_invalidation

QWEN_MAX_TOKENS = 1500
CONDITIONS_MAX = 3
CONDITION_MAX = 120

ANNOTATE_SYSTEM = (
    "당신은 인수인계 지식 카드의 유통기한을 표시하는 도우미입니다. "
    "카드마다 어떤 조직 변화가 생기면 그 카드 내용이 틀려질 수 있는지 최대 3개 고릅니다. "
    "event 는 다음 값 중 하나만 씁니다: "
    + ", ".join(f"{event}({label})" for event, label in CHANGE_EVENT_LABELS.items())
    + ". condition 은 그 변화가 생기면 왜 틀려지는지 한 문장(120자 이내)으로 씁니다. "
    "예: 카드가 '창고 재고 집계는 매주 월요일 아침에 엑셀로 올립니다' 라면 "
    "schedule_changed(집계 주기가 바뀌면 틀려질 수 있음)와 system_replaced(재고 시스템이 바뀌면 틀려질 수 있음)를 고릅니다. "
    "카드 내용과 관계없는 변화는 고르지 않습니다. 근거에 없는 사실·이름을 지어내지 않습니다. "
    "사람 이름 대신 역할명을 씁니다. "
    'JSON 만 출력합니다. 형식: {"cards": [{"index": 0, "invalidated_when": '
    '[{"event": "...", "condition": "..."}]}]}'
)


def fake_annotate_invalidation(cards: list[NewCard]) -> list[list[InvalidationCondition]]:
    return [fake_invalidation(f"{c.title} {c.body}") for c in cards]


def _user_prompt(cards: list[NewCard]) -> str:
    lines = ["카드:"]
    for i, card in enumerate(cards):
        lines.append(f"[{i}] ({card.type}) {card.title}: {card.body}")
    return "\n".join(lines)


def _conditions(raw: object) -> list[InvalidationCondition]:
    """Valid conditions from one card's raw list; invalid items are skipped."""
    if not isinstance(raw, list):
        return []
    conditions: list[InvalidationCondition] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        data = dict(item)
        if isinstance(data.get("condition"), str):
            data["condition"] = data["condition"].strip()[:CONDITION_MAX]
        try:
            condition = InvalidationCondition.model_validate(data)
        except ValidationError:
            continue
        if condition.event in seen:
            continue
        seen.add(condition.event)
        conditions.append(condition)
        if len(conditions) == CONDITIONS_MAX:
            break
    return conditions


def _parse(content: str, count: int) -> dict[int, list[InvalidationCondition]] | None:
    """Map card index → valid conditions. Cards whose items are all invalid are left out."""
    try:
        parsed = json.loads(content)
    except ValueError:
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("cards"), list):
        return None
    by_index: dict[int, list[InvalidationCondition]] = {}
    for raw in parsed["cards"]:
        if not isinstance(raw, dict):
            continue
        index = raw.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < count:
            continue
        if index in by_index:
            continue
        items = raw.get("invalidated_when")
        if not isinstance(items, list):
            continue
        conditions = _conditions(items)
        if items and not conditions:
            continue
        by_index[index] = conditions
    return by_index


def qwen_annotate_invalidation(
    chat: ChatFn, cards: list[NewCard]
) -> list[list[InvalidationCondition]]:
    if not cards:
        return []
    content = chat(ANNOTATE_SYSTEM, _user_prompt(cards), QWEN_MAX_TOKENS, json_mode=True)
    by_index = _parse(content, len(cards))
    if by_index is None:
        return fake_annotate_invalidation(cards)
    return [
        by_index[i] if i in by_index else fake_invalidation(f"{card.title} {card.body}")
        for i, card in enumerate(cards)
    ]
