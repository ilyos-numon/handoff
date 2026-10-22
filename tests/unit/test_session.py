from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from handoff.kb import KnowledgeBase
from handoff.providers import SUMMARY_EMPTY, FakeEmbedder, FakeLLM, ProviderError
from handoff.question_tree import FollowUp, UnknownRoleError, load_tree
from handoff.session import (
    DEFAULT_LEAVER,
    Question,
    SessionClosedError,
    SessionLimitError,
    SessionNotFoundError,
    SessionService,
    SessionTimeOverError,
    TreeExhaustedError,
)
from handoff.store import SessionStore

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


SESSION_SECONDS = 900
GRACE_SECONDS = 30


class Clock:
    def __init__(self) -> None:
        self.now = FIXED_NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class FailingEmbedder(FakeEmbedder):
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise ProviderError("embedding failed")


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def svc(kb: KnowledgeBase, clock: Clock) -> Iterator[SessionService]:
    instance = SessionService(
        kb,
        FakeLLM(),
        clock=clock,
        session_seconds=SESSION_SECONDS,
        grace_seconds=GRACE_SECONDS,
        planned=10,
    )
    yield instance
    instance.close()


def _finish_first_node(service: SessionService, leaver: str = DEFAULT_LEAVER) -> str:
    session_id, _ = service.start(ROLE, leaver)
    service.answer(session_id, ANSWER_MAIN)
    service.answer(session_id, ANSWER_FOLLOW)
    service.end(session_id)
    return session_id


def test_session_numbers_increase(svc: SessionService) -> None:
    first = _finish_first_node(svc)
    second, _ = svc.start(ROLE)
    assert svc.view(first).number == 1
    assert svc.view(second).number == 2
    assert svc.view(second).planned == 10


def test_second_session_resumes_at_next_node(svc: SessionService) -> None:
    _finish_first_node(svc)
    _, question = svc.start(ROLE)
    assert question.id == "rou-01"
    assert question.kind == "main"


def test_unanswered_main_question_resumes_same_node(svc: SessionService) -> None:
    _finish_first_node(svc)
    second, question = svc.start(ROLE)
    assert question.id == "rou-01"
    svc.end(second)
    _, again = svc.start(ROLE)
    assert again.id == "rou-01"


def test_pending_follow_up_resumes_next_node(svc: SessionService) -> None:
    session_id, _ = svc.start(ROLE)
    follow = svc.answer(session_id, ANSWER_MAIN)
    assert follow is not None and follow.kind == "follow_up"
    svc.end(session_id)
    _, question = svc.start(ROLE)
    assert question.id == "rou-01"


def test_empty_first_session_restarts_at_first_node(svc: SessionService) -> None:
    session_id, _ = svc.start(ROLE)
    svc.end(session_id)
    _, question = svc.start(ROLE)
    assert question.id == "sys-01"


def test_session_limit_raises_without_side_effects(kb: KnowledgeBase, clock: Clock) -> None:
    store = SessionStore(":memory:")
    service = SessionService(kb, FakeLLM(), clock=clock, store=store, planned=2)
    first, _ = service.start(ROLE)
    service.end(first)
    second, _ = service.start(ROLE)
    with pytest.raises(SessionLimitError):
        service.start(ROLE)
    rows = store.list_sessions(DEFAULT_LEAVER, ROLE)
    assert len(rows) == 2
    assert service.view(second).status == "open"
    service.close()


def test_start_ends_previous_open_session(svc: SessionService, kb: KnowledgeBase) -> None:
    first, _ = svc.start(ROLE)
    svc.answer(first, ANSWER_MAIN)
    second, _ = svc.start(ROLE)
    view = svc.view(first)
    assert view.status == "ended"
    assert len(view.summary) == 3
    assert svc.view(second).status == "open"
    assert kb.query(QUERY_HIT).found is True


def test_tree_exhausted_raises(kb: KnowledgeBase, clock: Clock) -> None:
    store = SessionStore(":memory:")
    service = SessionService(kb, NoFollowUpLLM(), clock=clock, store=store)
    session_id, _ = service.start(ROLE)
    nxt: Question | None = Question(id="x", axis="systems", text="x", kind="main")
    count = 0
    while nxt is not None:
        nxt = service.answer(session_id, f"답변 {count}")
        count += 1
    assert count == len(load_tree(ROLE).nodes)
    service.end(session_id)
    with pytest.raises(TreeExhaustedError):
        service.start(ROLE)
    assert len(store.list_sessions(DEFAULT_LEAVER, ROLE)) == 1
    service.close()


def test_answer_after_grace_raises_and_is_not_recorded(
    svc: SessionService, clock: Clock
) -> None:
    session_id, _ = svc.start(ROLE)
    clock.advance(SESSION_SECONDS + GRACE_SECONDS + 1)
    with pytest.raises(SessionTimeOverError):
        svc.answer(session_id, ANSWER_MAIN)
    view = svc.view(session_id)
    assert view.items_today == 0
    assert view.question is not None and view.question.id == "sys-01"
    assert svc.end(session_id) == 0


def test_answer_within_grace_is_recorded_and_returns_none(
    svc: SessionService, clock: Clock
) -> None:
    session_id, _ = svc.start(ROLE)
    clock.advance(SESSION_SECONDS + 10)
    assert svc.answer(session_id, ANSWER_MAIN) is None
    view = svc.view(session_id)
    assert view.items_today == 1
    assert view.remaining_seconds == 0
    assert view.question is not None and view.question.id == "sys-01-f1"


def test_answer_at_grace_boundary_is_accepted(svc: SessionService, clock: Clock) -> None:
    session_id, _ = svc.start(ROLE)
    clock.advance(SESSION_SECONDS + GRACE_SECONDS)
    assert svc.answer(session_id, ANSWER_MAIN) is None
    assert svc.view(session_id).items_today == 1


def test_answer_before_deadline_returns_next(svc: SessionService, clock: Clock) -> None:
    session_id, _ = svc.start(ROLE)
    clock.advance(SESSION_SECONDS - 1)
    nxt = svc.answer(session_id, ANSWER_MAIN)
    assert nxt is not None and nxt.id == "sys-01-f1"


def test_end_after_deadline_is_accepted(svc: SessionService, clock: Clock) -> None:
    session_id, _ = svc.start(ROLE)
    svc.answer(session_id, ANSWER_MAIN)
    clock.advance(SESSION_SECONDS * 3)
    assert svc.end(session_id) == 1
    assert svc.view(session_id).status == "ended"


def test_end_summary_has_three_lines(svc: SessionService) -> None:
    session_id = _finish_first_node(svc)
    summary = svc.view(session_id).summary
    assert len(summary) == 3
    assert summary[0].startswith("담당 시스템:")


def test_empty_session_summary(svc: SessionService) -> None:
    session_id, _ = svc.start(ROLE)
    svc.end(session_id)
    assert svc.view(session_id).summary == SUMMARY_EMPTY


def test_open_session_summary_is_empty(svc: SessionService) -> None:
    session_id, _ = svc.start(ROLE)
    assert svc.view(session_id).summary == []


def test_end_provider_error_keeps_session_open(tmp_path: Path, clock: Clock) -> None:
    kb = KnowledgeBase(tmp_path / "qdrant-fail", FailingEmbedder(), FakeLLM(), MIN_SCORE)
    service = SessionService(kb, FakeLLM(), clock=clock)
    session_id, _ = service.start(ROLE)
    service.answer(session_id, ANSWER_MAIN)
    with pytest.raises(ProviderError):
        service.end(session_id)
    view = service.view(session_id)
    assert view.status == "open"
    assert view.summary == []
    assert view.items_today == 1
    nxt = service.answer(session_id, ANSWER_FOLLOW)
    assert nxt is not None and nxt.kind == "main"
    service.close()
    kb.close()


def test_file_store_survives_service_restart(
    tmp_path: Path, kb: KnowledgeBase, clock: Clock
) -> None:
    db = tmp_path / "handoff.db"
    first_service = SessionService(kb, FakeLLM(), clock=clock, store=SessionStore(db))
    session_id, _ = first_service.start(ROLE, "leaver_a")
    first_service.answer(session_id, ANSWER_MAIN)
    first_service.close()

    second_service = SessionService(kb, FakeLLM(), clock=clock, store=SessionStore(db))
    view = second_service.view(session_id)
    assert view.number == 1
    assert view.leaver == "leaver_a"
    assert view.status == "open"
    assert view.items_today == 1
    assert view.question is not None and view.question.id == "sys-01-f1"
    nxt = second_service.answer(session_id, ANSWER_FOLLOW)
    assert nxt is not None and nxt.id == "rou-01"
    new_id, question = second_service.start(ROLE, "leaver_a")
    assert second_service.view(new_id).number == 2
    assert second_service.view(session_id).status == "ended"
    assert question.id == "rou-01"
    second_service.close()


def test_items_today_and_total(svc: SessionService) -> None:
    first = _finish_first_node(svc)
    first_view = svc.view(first)
    assert first_view.items_today == 2
    assert first_view.items_total == 2
    second, _ = svc.start(ROLE)
    svc.answer(second, ANSWER_MAIN)
    view = svc.view(second)
    assert view.items_today == 1
    assert view.items_total == 3
    assert svc.view(first).items_total == 2


def test_remaining_seconds(svc: SessionService, clock: Clock) -> None:
    session_id, _ = svc.start(ROLE)
    assert svc.view(session_id).remaining_seconds == SESSION_SECONDS
    clock.advance(0.5)
    assert svc.view(session_id).remaining_seconds == SESSION_SECONDS
    clock.advance(SESSION_SECONDS)
    assert svc.view(session_id).remaining_seconds == 0
    svc.end(session_id)
    assert svc.view(session_id).remaining_seconds == 0


def test_invalid_leaver_raises(svc: SessionService) -> None:
    for bad in ("", "Upper", "has space", "a" * 41, "한글", "a.b"):
        with pytest.raises(ValueError):
            svc.start(ROLE, bad)
    session_id, _ = svc.start(ROLE, "a" * 40)
    view = svc.view(session_id)
    assert view.leaver == "a" * 40
    assert view.number == 1


def test_leavers_are_independent(svc: SessionService) -> None:
    _finish_first_node(svc, "alpha")
    beta, question = svc.start(ROLE, "beta")
    assert question.id == "sys-01"
    assert svc.view(beta).number == 1
    assert svc.view(beta).items_total == 0


def test_view_unknown_session_raises(svc: SessionService) -> None:
    with pytest.raises(SessionNotFoundError):
        svc.view("missing")


def test_ingested_citation_has_session_number(svc: SessionService, kb: KnowledgeBase) -> None:
    _finish_first_node(svc)
    second, _ = svc.start(ROLE)
    svc.answer(second, "정산 배치 금요일 배포 재실행 기록")
    svc.end(second)
    numbers = {c.session_number for c in kb.query(QUERY_HIT).citations}
    assert 1 in numbers


def test_coverage_counts_ended_sessions_only(svc: SessionService) -> None:
    _finish_first_node(svc)
    second, _ = svc.start(ROLE)
    svc.answer(second, ANSWER_MAIN)
    report = svc.coverage(DEFAULT_LEAVER, ROLE)
    assert report.sessions_done == 1
    assert report.sessions_planned == 10
    assert report.items_total == 2
    by_axis = {a.axis: a for a in report.axes}
    assert by_axis["systems"].covered == 1
    assert by_axis["routines"].covered == 0
    assert "sys-01" not in [t.question_id for t in report.empty_topics]
    assert "rou-01" in [t.question_id for t in report.empty_topics]


def test_coverage_unknown_role_raises(svc: SessionService) -> None:
    with pytest.raises(UnknownRoleError):
        svc.coverage(DEFAULT_LEAVER, "unknown_role")
