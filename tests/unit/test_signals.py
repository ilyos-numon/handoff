import pytest

from handoff.contracts import CHANGE_EVENTS, InvalidationCondition
from handoff.signals import (
    EVENT_CONDITION_TEMPLATES,
    EVENT_CUES,
    TACIT_FOLLOW_UP_TEMPLATE,
    TACIT_TARGET_CUES,
    UNCERTAIN_CUES,
    card_uncertain_cues,
    extract_roles,
    fake_invalidation,
    find_cues,
    tacit_cues,
    uncertain_cues,
)


def test_find_cues_follows_cue_order_not_text_order() -> None:
    assert find_cues("느낌상 그냥 합니다", ("그냥", "느낌")) == ["그냥", "느낌"]


def test_find_cues_no_duplicates_and_empty() -> None:
    assert find_cues("그냥 그냥 그냥", ("그냥",)) == ["그냥"]
    assert find_cues("확인합니다", ("그냥", "느낌")) == []
    assert find_cues("", ("그냥",)) == []


def test_uncertain_cues() -> None:
    assert uncertain_cues("아마 전임자가 만든 것 같고 확실하지 않아요") == [
        "아마",
        "전임자가",
        "확실하지 않",
    ]
    assert uncertain_cues("매일 새벽 3시에 실행합니다") == []


def test_tacit_cues() -> None:
    assert tacit_cues("경험상 그냥 감으로 정합니다") == ["그냥", "감으로", "경험상"]
    assert tacit_cues("로그를 보고 판단합니다") == []


def test_tacit_template_targets_criteria() -> None:
    text = TACIT_FOLLOW_UP_TEMPLATE.format(cue="그냥")
    assert "'그냥'" in text
    assert any(cue in text for cue in TACIT_TARGET_CUES)


def test_card_uncertain_cues_from_card_text() -> None:
    assert card_uncertain_cues("아마 월말에 돌립니다", []) == ["아마"]


def test_card_uncertain_cues_from_overlapping_answer_sentence() -> None:
    card = "정산 배치는 월말 새벽에 수동으로 재실행한다"
    answers = ["정산 배치는 아마 월말 새벽에 수동으로 재실행합니다. 다른 건 없습니다."]
    assert card_uncertain_cues(card, answers) == ["아마"]


def test_card_uncertain_cues_ignores_non_overlapping_sentence() -> None:
    card = "정산 배치는 월말 새벽에 수동으로 재실행한다"
    answers = ["정산 배치는 월말 새벽에 재실행합니다. 고객 문의 채널은 잘 모르겠어요."]
    assert card_uncertain_cues(card, answers) == []


def test_card_uncertain_cues_union_in_cue_order_without_duplicates() -> None:
    card = "전임자가 정한 정산 배치 재실행 순서"
    answers = [
        "정산 배치 재실행 순서는 아마 전임자가 정했습니다.",
        "정산 배치 재실행 순서는 아마 그대로입니다.",
    ]
    result = card_uncertain_cues(card, answers)
    assert result == ["아마", "전임자가"]
    assert result == [cue for cue in UNCERTAIN_CUES if cue in result]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("결제팀 리드에게 먼저 묻습니다", ["결제팀 리드"]),
        ("정산 담당자에게 보냅니다", ["정산 담당자"]),
        ("야간에는 운영 당번이 먼저 받고", ["운영 당번"]),
        ("고객센터가 대응을 준비합니다", ["고객센터"]),
        ("인프라팀 요청 게시판에 올립니다", ["인프라팀"]),
        ("질문을 올리고 리드를 언급하면", ["리드"]),
        ("결제 개발 채널에 올립니다", []),
    ],
)
def test_extract_roles_examples(text: str, expected: list[str]) -> None:
    assert extract_roles(text) == expected


def test_extract_roles_order_and_dedup() -> None:
    text = "인프라팀 요청 후 결제팀 리드에게 알리고, 다시 인프라팀에 확인합니다"
    assert extract_roles(text) == ["인프라팀", "결제팀 리드"]


def test_extract_roles_empty_text() -> None:
    assert extract_roles("") == []


def test_extract_roles_respects_length_limit() -> None:
    assert extract_roles("가나다라마바사아자차카타파하가나다라마바팀에 묻습니다") == []


def test_fake_invalidation_event_order() -> None:
    result = fake_invalidation("매일 새벽 대행사에서 서버 점검을 합니다")
    assert [c.event for c in result] == ["system_replaced", "vendor_changed", "schedule_changed"]
    assert all(isinstance(c, InvalidationCondition) for c in result)
    assert result[0].condition == EVENT_CONDITION_TEMPLATES["system_replaced"]


def test_fake_invalidation_max_three() -> None:
    text = "서버 대행사 팀 규정 매일"
    result = fake_invalidation(text)
    assert len(result) == 3
    assert [c.event for c in result] == list(CHANGE_EVENTS[:3])


def test_fake_invalidation_none() -> None:
    assert fake_invalidation("기록을 꼼꼼히 남깁니다") == []


def test_event_tables_cover_change_events() -> None:
    assert set(EVENT_CUES) == set(CHANGE_EVENTS)
    assert set(EVENT_CONDITION_TEMPLATES) == set(CHANGE_EVENTS)
    for template in EVENT_CONDITION_TEMPLATES.values():
        assert len(template) <= 120
