"""S5: card invalidation conditions (fake and qwen)."""

import json

import pytest

from handoff.contracts import ChatFn, Evidence, InvalidationCondition, NewCard
from handoff.llm_tasks.expiry import fake_annotate_invalidation, qwen_annotate_invalidation
from handoff.providers import FakeLLM, ProviderError
from handoff.signals import EVENT_CONDITION_TEMPLATES, fake_invalidation

EVIDENCE = Evidence(session_id="s1", session_number=1, question_id="q-01", excerpt="근거")


def _card(title: str, body: str, card_type: str = "procedure") -> NewCard:
    return NewCard.model_validate({
        "leaver": "lee", "role": "dev", "session_id": "s1", "type": card_type,
        "title": title, "body": body, "entities": [], "evidence": [EVIDENCE.model_dump()],
        "confidence": 0.8,
    })


# 시스템 단서(서버) + 일정 단서(매주)
SERVER_CARD = _card("백업 서버 점검", "매주 백업 서버 디스크를 확인합니다.")
# 외부 업체 단서만
VENDOR_CARD = _card("인쇄 업체 연락", "인쇄물은 외부 업체가 납품합니다.", "contact")
# 아무 단서도 없음
PLAIN_CARD = _card("메모 정리", "메모를 깔끔하게 씁니다.")


def _cond(event: str, condition: str = "바뀌면 틀릴 수 있습니다.") -> dict[str, str]:
    return {"event": event, "condition": condition}


def _reply(cards: list[dict[str, object]]) -> str:
    return json.dumps({"cards": cards}, ensure_ascii=False)


def _chat_returning(content: str, seen: list[tuple[str, str, int, bool]] | None = None) -> ChatFn:
    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        if seen is not None:
            seen.append((system, user, max_tokens, json_mode))
        return content

    return chat


# ---- fake
def test_fake_uses_signal_rule_per_card() -> None:
    cards = [SERVER_CARD, VENDOR_CARD, PLAIN_CARD]
    result = fake_annotate_invalidation(cards)
    assert result == [fake_invalidation(f"{c.title} {c.body}") for c in cards]
    assert [c.event for c in result[0]] == ["system_replaced", "schedule_changed"]
    assert [c.event for c in result[1]] == ["vendor_changed"]
    assert result[2] == []


def test_fake_condition_text_is_template() -> None:
    [conds] = fake_annotate_invalidation([VENDOR_CARD])
    assert conds[0].condition == EVENT_CONDITION_TEMPLATES["vendor_changed"]


def test_fake_empty_input() -> None:
    assert fake_annotate_invalidation([]) == []


def test_fake_llm_delegates_to_fake() -> None:
    assert FakeLLM().annotate_invalidation([SERVER_CARD]) == fake_annotate_invalidation([SERVER_CARD])


# ---- qwen
def test_qwen_prompt_lists_cards_and_asks_json() -> None:
    seen: list[tuple[str, str, int, bool]] = []
    content = _reply([{"index": 0, "invalidated_when": [_cond("policy_changed")]},
                      {"index": 1, "invalidated_when": [_cond("vendor_changed")]}])
    qwen_annotate_invalidation(_chat_returning(content, seen), [SERVER_CARD, VENDOR_CARD])
    system, user, _, json_mode = seen[0]
    assert json_mode is True
    assert "JSON" in system and "지어내지 않습니다" in system
    assert "[0] (procedure) 백업 서버 점검: 매주 백업 서버 디스크를 확인합니다." in user
    assert "[1] (contact) 인쇄 업체 연락:" in user


def test_qwen_parses_valid_conditions() -> None:
    content = _reply([
        {"index": 1, "invalidated_when": [_cond("vendor_changed", "  업체가 바뀌면 틀립니다. ")]},
        {"index": 0, "invalidated_when": [_cond("policy_changed")]},
    ])
    result = qwen_annotate_invalidation(_chat_returning(content), [SERVER_CARD, VENDOR_CARD])
    assert result == [
        [InvalidationCondition(event="policy_changed", condition="바뀌면 틀릴 수 있습니다.")],
        [InvalidationCondition(event="vendor_changed", condition="업체가 바뀌면 틀립니다.")],
    ]


def test_qwen_broken_json_falls_back_to_fake() -> None:
    cards = [SERVER_CARD, VENDOR_CARD]
    assert qwen_annotate_invalidation(_chat_returning("{nope"), cards) == fake_annotate_invalidation(cards)


def test_qwen_wrong_shape_falls_back_to_fake() -> None:
    cards = [SERVER_CARD]
    result = qwen_annotate_invalidation(_chat_returning('{"items": []}'), cards)
    assert result == fake_annotate_invalidation(cards)


def test_qwen_missing_index_fills_only_that_card() -> None:
    content = _reply([{"index": 1, "invalidated_when": [_cond("policy_changed")]}])
    result = qwen_annotate_invalidation(_chat_returning(content), [SERVER_CARD, VENDOR_CARD])
    assert result[0] == fake_invalidation(f"{SERVER_CARD.title} {SERVER_CARD.body}")
    assert [c.event for c in result[1]] == ["policy_changed"]


def test_qwen_out_of_range_event_fills_only_that_card() -> None:
    content = _reply([
        {"index": 0, "invalidated_when": [_cond("weather_changed"), _cond("system_replaced", "  ")]},
        {"index": 1, "invalidated_when": [_cond("team_reorganized")]},
    ])
    result = qwen_annotate_invalidation(_chat_returning(content), [SERVER_CARD, VENDOR_CARD])
    assert result[0] == fake_invalidation(f"{SERVER_CARD.title} {SERVER_CARD.body}")
    assert [c.event for c in result[1]] == ["team_reorganized"]


def test_qwen_skips_invalid_items_but_keeps_valid_ones() -> None:
    content = _reply([{"index": 0, "invalidated_when": [
        _cond("weather_changed"), "text", _cond("schedule_changed"),
    ]}])
    [conds] = qwen_annotate_invalidation(_chat_returning(content), [SERVER_CARD])
    assert [c.event for c in conds] == ["schedule_changed"]


def test_qwen_keeps_first_three_conditions() -> None:
    events = ["system_replaced", "vendor_changed", "team_reorganized", "policy_changed"]
    content = _reply([{"index": 0, "invalidated_when": [_cond(e) for e in events]}])
    [conds] = qwen_annotate_invalidation(_chat_returning(content), [SERVER_CARD])
    assert [c.event for c in conds] == events[:3]


def test_qwen_truncates_long_condition() -> None:
    content = _reply([{"index": 0, "invalidated_when": [_cond("system_replaced", "가" * 200)]}])
    [conds] = qwen_annotate_invalidation(_chat_returning(content), [SERVER_CARD])
    assert conds[0].condition == "가" * 120


def test_qwen_result_length_matches_input() -> None:
    content = _reply([{"index": 5, "invalidated_when": [_cond("policy_changed")]},
                      {"index": "0", "invalidated_when": [_cond("policy_changed")]}])
    cards = [SERVER_CARD, VENDOR_CARD, PLAIN_CARD]
    result = qwen_annotate_invalidation(_chat_returning(content), cards)
    assert result == fake_annotate_invalidation(cards)


def test_qwen_empty_list_means_no_conditions() -> None:
    content = _reply([{"index": 0, "invalidated_when": []}])
    assert qwen_annotate_invalidation(_chat_returning(content), [SERVER_CARD]) == [[]]


def test_qwen_no_cards_no_call() -> None:
    seen: list[tuple[str, str, int, bool]] = []
    assert qwen_annotate_invalidation(_chat_returning("{}", seen), []) == []
    assert seen == []


def test_qwen_provider_error_propagates() -> None:
    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        raise ProviderError("down")

    with pytest.raises(ProviderError):
        qwen_annotate_invalidation(chat, [SERVER_CARD])
