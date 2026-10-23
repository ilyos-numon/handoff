from handoff.privacy import (
    EMAIL_MASK,
    PHONE_MASK,
    AnonymizeResult,
    anonymize,
    find_pii,
    scrub_contacts,
)


def test_spec_example_handle_and_email() -> None:
    result = anonymize("장애 시 @pay-lead 에게 연락 (pay@example.com)", {"@pay-lead": "결제팀 리드"})
    assert isinstance(result, AnonymizeResult)
    assert "결제팀 리드" in result.text
    assert EMAIL_MASK in result.text
    assert "@pay-lead" not in result.text
    assert "pay@example.com" not in result.text
    assert result.replaced == 2


def test_phone_masked_with_and_without_dashes() -> None:
    result = anonymize("야간 당번 010-1234-5678, 대표 0212345678", {})
    assert result.text == f"야간 당번 {PHONE_MASK}, 대표 {PHONE_MASK}"
    assert result.replaced == 2


def test_phone_not_masked_inside_longer_number() -> None:
    text = "주문번호 9010123456789 확인"
    assert anonymize(text, {}).text == text
    assert anonymize(text, {}).replaced == 0


def test_role_map_longest_key_first() -> None:
    result = anonymize("@pay 와 @pay-lead 에게", {"@pay": "결제팀", "@pay-lead": "결제팀 리드"})
    assert result.text == "결제팀 와 결제팀 리드 에게"
    assert result.replaced == 2


def test_role_map_counts_every_occurrence() -> None:
    result = anonymize("@ops 확인 후 @ops 에게 보고", {"@ops": "운영팀"})
    assert result.text == "운영팀 확인 후 운영팀 에게 보고"
    assert result.replaced == 2


def test_email_masked_before_role_map() -> None:
    result = anonymize("ops@example.com", {"ops": "운영팀"})
    assert result.text == EMAIL_MASK
    assert result.replaced == 1


def test_no_change_returns_zero() -> None:
    result = anonymize("배포 전에 변경 목록을 확인합니다.", {"@x": "역할"})
    assert result.text == "배포 전에 변경 목록을 확인합니다."
    assert result.replaced == 0


def test_scrub_contacts_keeps_handles() -> None:
    out = scrub_contacts("@pay-lead (pay@example.com, 010-1234-5678)")
    assert out == f"@pay-lead ({EMAIL_MASK}, {PHONE_MASK})"


def test_find_pii_lists_email_phone_handle() -> None:
    found = find_pii("@pay-lead 에게 pay@example.com 또는 010-1234-5678")
    assert "pay@example.com" in found
    assert "010-1234-5678" in found
    assert "@pay-lead" in found
    assert "@example" not in found


def test_find_pii_clean_text_empty() -> None:
    assert find_pii(f"결제팀 리드 에게 {EMAIL_MASK} 로 연락") == []
