from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from handoff.kb import KnowledgeBase
from handoff.providers import FakeEmbedder, FakeLLM
from handoff.question_tree import FollowUp, UnknownRoleError, load_tree
from handoff.session import (
    Question,
    SessionClosedError,
    SessionNotFoundError,
    SessionService,
)

ANSWER_MAIN = "금요일 배포는 피합니다. 작년 3월 금요일 저녁 배포 후 정산 배치가 주말 내내 실패했습니다."
ANSWER_FOLLOW = "복구는 큐를 비우고 정산 배치를 수동으로 재실행했습니다."
QUERY_HIT = "금요일에 배포하면 왜 안 되나요?"

MIN_SCORE = 0.25
ROLE = "backend_developer"
FIXED_NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)


class NoFollowUpLLM(FakeLLM):
    def follow_up(
        self, question: str, answer: str, candidates: list[FollowUp]
    ) -> FollowUp | None:
        return None


@pytest.fixture
def kb(tmp_path: Path) -> Iterator[KnowledgeBase]:
    instance = KnowledgeBase(tmp_path / "qdrant", FakeEmbedder(), FakeLLM(), MIN_SCORE)
    yield instance
    instance.close()


@pytest.fixture
def service(kb: KnowledgeBase) -> SessionService:
    return SessionService(kb, FakeLLM(), clock=lambda: FIXED_NOW)


def test_start_returns_first_main_question(service: SessionService) -> None:
    session_id, question = service.start(ROLE)
    assert len(session_id) == 32
    assert question == Question(
        id="sys-01", axis="systems", text=load_tree(ROLE).nodes[0].text, kind="main"
    )


def test_start_unknown_role_raises(service: SessionService) -> None:
    with pytest.raises(UnknownRoleError):
        service.start("unknown_role")


def test_main_answer_returns_follow_up(service: SessionService) -> None:
    session_id, _ = service.start(ROLE)
    nxt = service.answer(session_id, ANSWER_MAIN)
    assert nxt is not None
    assert nxt.kind == "follow_up"
    assert nxt.axis == "systems"
    assert nxt.id == "sys-01-f1"


def test_follow_up_answer_returns_second_main(service: SessionService) -> None:
    session_id, _ = service.start(ROLE)
    service.answer(session_id, ANSWER_MAIN)
    nxt = service.answer(session_id, ANSWER_FOLLOW)
    second = load_tree(ROLE).nodes[1]
    assert nxt is not None
    assert nxt.kind == "main"
    assert nxt.id == second.id
    assert nxt.axis == second.axis


def test_answers_not_in_kb_until_end(service: SessionService, kb: KnowledgeBase) -> None:
    session_id, _ = service.start(ROLE)
    service.answer(session_id, ANSWER_MAIN)
    service.answer(session_id, ANSWER_FOLLOW)
    assert kb.query(QUERY_HIT).found is False

    assert service.end(session_id) == 2

    result = kb.query(QUERY_HIT)
    assert result.found is True
    top = result.citations[0]
    assert top.session_id == session_id
    assert top.question_id == "sys-01"
    assert top.axis == "systems"


def test_blank_answer_raises_and_is_not_recorded(service: SessionService) -> None:
    session_id, _ = service.start(ROLE)
    service.answer(session_id, ANSWER_MAIN)
    for blank in ("", "   ", "\n\t"):
        with pytest.raises(ValueError):
            service.answer(session_id, blank)
    nxt = service.answer(session_id, ANSWER_FOLLOW)
    assert nxt is not None and nxt.kind == "main"
    assert service.end(session_id) == 2


def test_unknown_session_raises(service: SessionService) -> None:
    with pytest.raises(SessionNotFoundError):
        service.answer("missing", ANSWER_MAIN)
    with pytest.raises(SessionNotFoundError):
        service.end("missing")


def test_closed_session_rejects_answer_and_end(service: SessionService) -> None:
    session_id, _ = service.start(ROLE)
    service.answer(session_id, ANSWER_MAIN)
    assert service.end(session_id) == 1
    with pytest.raises(SessionClosedError):
        service.answer(session_id, ANSWER_FOLLOW)
    with pytest.raises(SessionClosedError):
        service.end(session_id)


def test_end_without_answers_ingests_zero(service: SessionService) -> None:
    session_id, _ = service.start(ROLE)
    assert service.end(session_id) == 0


def test_exhausting_all_nodes_returns_none(service: SessionService) -> None:
    tree = load_tree(ROLE)
    session_id, question = service.start(ROLE)
    answered = 0
    current: Question | None = question
    while current is not None:
        current = service.answer(session_id, f"답변 {answered}")
        answered += 1
    assert answered == len(tree.nodes) * 2
    assert service.end(session_id) == answered


def test_llm_without_follow_up_moves_to_next_main(kb: KnowledgeBase) -> None:
    service = SessionService(kb, NoFollowUpLLM(), clock=lambda: FIXED_NOW)
    session_id, _ = service.start(ROLE)
    nxt = service.answer(session_id, ANSWER_MAIN)
    assert nxt is not None
    assert nxt.kind == "main"
    assert nxt.id == load_tree(ROLE).nodes[1].id


def test_default_clock_is_used(kb: KnowledgeBase) -> None:
    service = SessionService(kb, FakeLLM())
    session_id, _ = service.start(ROLE)
    service.answer(session_id, ANSWER_MAIN)
    assert service.end(session_id) == 1
