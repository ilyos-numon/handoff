from datetime import UTC, datetime

from handoff.kb import KBEntry
from handoff.providers import SUMMARY_EMPTY, FakeLLM, ProviderError, fake_summary
from handoff.question_tree import Axis


def _entry(question_id: str, axis: Axis, answer: str) -> KBEntry:
    return KBEntry(
        session_id="sess-1",
        question_id=question_id,
        axis=axis,
        question="질문",
        answer=answer,
        created_at=datetime(2026, 10, 23, tzinfo=UTC),
    )


def test_provider_error_is_runtime_error() -> None:
    assert issubclass(ProviderError, RuntimeError)


def test_fake_summary_empty_returns_copy() -> None:
    result = fake_summary([])
    assert result == SUMMARY_EMPTY
    assert result is not SUMMARY_EMPTY
    result.append("x")
    assert len(SUMMARY_EMPTY) == 3


def test_fake_summary_two_entries_padded() -> None:
    result = fake_summary(
        [_entry("sys-01", "systems", "정산 API"), _entry("rou-01", "routines", "월말 마감")]
    )
    assert result == ["담당 시스템: 정산 API", "반복 업무: 월말 마감", "-"]


def test_fake_summary_five_entries_first_three() -> None:
    entries = [
        _entry("sys-01", "systems", "a1"),
        _entry("rou-01", "routines", "a2"),
        _entry("inc-01", "incidents", "a3"),
        _entry("ppl-01", "people", "a4"),
        _entry("sys-02", "systems", "a5"),
    ]
    assert fake_summary(entries) == ["담당 시스템: a1", "반복 업무: a2", "사고 이력: a3"]


def test_fake_summary_main_before_follow_ups() -> None:
    entries = [
        _entry("inc-01-f1", "incidents", "꼬리1"),
        _entry("inc-01", "incidents", "주1"),
        _entry("ppl-01-f1", "people", "꼬리2"),
        _entry("ppl-01", "people", "주2"),
    ]
    assert fake_summary(entries) == ["사고 이력: 주1", "관계자: 주2", "사고 이력: 꼬리1"]


def test_fake_summary_truncates_to_60_and_collapses_whitespace() -> None:
    answer = "가  나\n다\t" + "라" * 100
    (line, _, _) = fake_summary([_entry("sys-01", "systems", answer)])
    body = line.removeprefix("담당 시스템: ")
    assert body == ("가 나 다 " + "라" * 100)[:60]
    assert len(body) == 60


def test_fake_llm_summarize_matches_fake_summary() -> None:
    entries = [_entry("sys-01", "systems", "정산 API")]
    result = FakeLLM().summarize(entries)
    assert result == fake_summary(entries)
    assert len(result) == 3
    assert len(FakeLLM().summarize([])) == 3
