from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from handoff.kb import (
    COMPOSE_FAILED_MESSAGE,
    NOT_FOUND_MESSAGE,
    Citation,
    KBEntry,
    KnowledgeBase,
    Verifier,
)
from handoff.providers import FakeEmbedder, FakeLLM, ProviderError

ANSWER_MAIN = "금요일 배포는 피합니다. 작년 3월 금요일 저녁 배포 후 정산 배치가 주말 내내 실패했습니다."
QUERY_HIT = "금요일에 배포하면 왜 안 되나요?"
MIN_SCORE = 0.25


def _entries() -> list[KBEntry]:
    return [
        KBEntry(
            session_id="sess-1",
            question_id="inc-01",
            axis="incidents",
            question="가장 기억에 남는 장애는?",
            answer=ANSWER_MAIN,
            created_at=datetime(2026, 10, 23, tzinfo=UTC),
        )
    ]


class RecordingVerifier:
    def __init__(self, result: tuple[str | None, int]) -> None:
        self.result = result
        self.calls: list[tuple[str, str, list[Citation]]] = []

    def __call__(
        self, question: str, answer: str, citations: list[Citation]
    ) -> tuple[str | None, int]:
        self.calls.append((question, answer, list(citations)))
        return self.result


class FailingComposeLLM(FakeLLM):
    def compose_answer(self, question: str, citations: list[Citation]) -> str | None:
        raise ProviderError("down")


class NoneComposeLLM(FakeLLM):
    def compose_answer(self, question: str, citations: list[Citation]) -> str | None:
        return None


def _kb(
    tmp_path: Path, verifier: Verifier | None, llm: FakeLLM | None = None
) -> KnowledgeBase:
    kb = KnowledgeBase(
        tmp_path / "qdrant", FakeEmbedder(), llm or FakeLLM(), MIN_SCORE, verifier=verifier
    )
    kb.add(_entries())
    return kb


@pytest.fixture
def plain_kb(tmp_path: Path) -> Iterator[KnowledgeBase]:
    kb = _kb(tmp_path, None)
    yield kb
    kb.close()


def test_no_verifier_claims_removed_zero(plain_kb: KnowledgeBase) -> None:
    result = plain_kb.query(QUERY_HIT)
    assert result.found is True
    assert result.claims_removed == 0
    assert result.answer.startswith("인수인계 기록에 따르면:")


def test_verifier_kept_answer(tmp_path: Path) -> None:
    verifier = RecordingVerifier(("금요일 배포는 피합니다.", 1))
    kb = _kb(tmp_path, verifier)
    try:
        result = kb.query(QUERY_HIT)
    finally:
        kb.close()
    assert result.found is True
    assert result.answer == "금요일 배포는 피합니다."
    assert result.claims_removed == 1
    assert result.citations
    question, answer, citations = verifier.calls[0]
    assert question == QUERY_HIT
    assert answer.startswith("인수인계 기록에 따르면:")
    assert citations == result.citations


def test_verifier_none_means_not_found(tmp_path: Path) -> None:
    verifier = RecordingVerifier((None, 2))
    kb = _kb(tmp_path, verifier)
    try:
        result = kb.query(QUERY_HIT)
    finally:
        kb.close()
    assert result.found is False
    assert result.answer == NOT_FOUND_MESSAGE
    assert result.citations == []
    assert result.claims_removed == 2


def test_compose_failure_skips_verifier(tmp_path: Path) -> None:
    verifier = RecordingVerifier((None, 5))
    kb = _kb(tmp_path, verifier, FailingComposeLLM())
    try:
        result = kb.query(QUERY_HIT)
    finally:
        kb.close()
    assert result.found is True
    assert result.answer == COMPOSE_FAILED_MESSAGE
    assert result.claims_removed == 0
    assert verifier.calls == []


def test_compose_none_skips_verifier(tmp_path: Path) -> None:
    verifier = RecordingVerifier(("x", 0))
    kb = _kb(tmp_path, verifier, NoneComposeLLM())
    try:
        result = kb.query(QUERY_HIT)
    finally:
        kb.close()
    assert result.found is False
    assert verifier.calls == []


def test_kb_answer_default_claims_removed() -> None:
    from handoff.kb import KBAnswer

    assert KBAnswer(found=False, answer="x", citations=[]).claims_removed == 0
