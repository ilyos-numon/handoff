from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from handoff.contracts import AskRequest, AskResponse, Citation, KBEntry, topic_id
from handoff.kb import KnowledgeBase, Verifier
from handoff.knowledge_store import KnowledgeStore
from handoff.providers import FakeEmbedder, FakeLLM
from handoff.store import SessionRow, SessionStore
from handoff.verify import ClaimVerifier, SuccessorService, normalize_question

QUESTION = "PG 점검 공지는 어디서 봐요?"
ANSWER = "결제대행사 관리자 페이지 게시판을 매주 월요일 아침에 먼저 확인합니다."
PHONE = "010-1234-5678"
ROLE = "backend_developer"
MIN_SCORE = 0.25
NOW = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)


@pytest.fixture
def sessions() -> SessionStore:
    return SessionStore(":memory:")


@pytest.fixture
def store(sessions: SessionStore) -> KnowledgeStore:
    return KnowledgeStore.from_session_store(sessions)


def _kb(tmp_path: Path, verifier: Verifier | None) -> KnowledgeBase:
    return KnowledgeBase(
        tmp_path / "qdrant", FakeEmbedder(), FakeLLM(), MIN_SCORE, verifier=verifier
    )


@pytest.fixture
def kb(tmp_path: Path) -> Iterator[KnowledgeBase]:
    k = _kb(tmp_path, ClaimVerifier(FakeLLM()))
    yield k
    k.close()


def _answer_in_session(
    sessions: SessionStore,
    store: KnowledgeStore,
    kb: KnowledgeBase,
    item_id: str,
    answer: str,
    *,
    leaver: str = "loop",
    ended: bool = True,
) -> None:
    """Leaver answers queue item ``item_id`` in session 1; optionally the session is ended."""
    row = SessionRow(
        session_id="sess-1",
        leaver=leaver,
        role=ROLE,
        number=1,
        started_at=NOW,
        deadline=NOW + timedelta(minutes=30),
        ended_at=None,
        node_index=0,
        current_json=None,
        summary=[],
    )
    sessions.insert_session(row)
    entry = KBEntry(
        session_id="sess-1",
        question_id=item_id,
        axis="systems",
        question=QUESTION,
        answer=answer,
        created_at=NOW,
        session_number=1,
    )
    sessions.add_answer(entry)
    store.mark_answered(item_id, "sess-1")
    if ended:
        row.ended_at = NOW + timedelta(minutes=10)
        sessions.update_session(row)
        kb.add([entry], leaver)


def _ask(svc: SuccessorService, kb: KnowledgeBase, question: str, leaver: str = "loop") -> AskResponse:
    return svc.ask(kb, AskRequest(question=question, leaver=leaver))


def _first(res: AskResponse) -> Citation:
    assert res.answer.citations
    return res.answer.citations[0]


def test_linked_answer_without_shared_characters(
    sessions: SessionStore, store: KnowledgeStore, kb: KnowledgeBase
) -> None:
    svc = SuccessorService(store, clock=lambda: NOW, sessions=sessions)
    first = _ask(svc, kb, QUESTION)
    assert first.answer.found is False
    assert first.queued is not None and first.queued.id == "q1"
    _answer_in_session(sessions, store, kb, "q1", ANSWER)

    res = _ask(svc, kb, QUESTION)
    assert res.answer.found is True
    assert _first(res).score == 1.0
    assert topic_id(_first(res).question_id) == "q1"
    assert _first(res).session_number == 1
    assert res.queued is None
    assert res.route is None
    assert res.certainty in ("certain", "uncertain")
    assert len(svc.list("loop")) == 1


def test_linked_answer_without_question_mark(
    sessions: SessionStore, store: KnowledgeStore, kb: KnowledgeBase
) -> None:
    svc = SuccessorService(store, clock=lambda: NOW, sessions=sessions)
    _ask(svc, kb, QUESTION)
    _answer_in_session(sessions, store, kb, "q1", ANSWER)

    res = _ask(svc, kb, "PG 점검 공지는 어디서 봐요")
    assert res.answer.found is True
    assert topic_id(_first(res).question_id) == "q1"
    assert len(svc.list("loop")) == 1


def test_answered_but_not_ended_is_not_requeued(
    sessions: SessionStore, store: KnowledgeStore, kb: KnowledgeBase
) -> None:
    svc = SuccessorService(store, clock=lambda: NOW, sessions=sessions)
    _ask(svc, kb, QUESTION)
    _answer_in_session(sessions, store, kb, "q1", ANSWER, ended=False)

    res = _ask(svc, kb, QUESTION)
    assert res.answer.found is False
    assert res.queued is not None
    assert res.queued.id == "q1"
    assert res.queued.status == "answered"
    assert len(svc.list("loop")) == 1


def test_all_claims_rejected_falls_back_to_excerpt(
    sessions: SessionStore, store: KnowledgeStore, tmp_path: Path
) -> None:
    kb = _kb(tmp_path, lambda q, a, c: (None, 1))
    try:
        svc = SuccessorService(store, clock=lambda: NOW, sessions=sessions)
        _ask(svc, kb, QUESTION)
        _answer_in_session(sessions, store, kb, "q1", ANSWER)

        res = _ask(svc, kb, QUESTION)
        assert res.answer.found is True
        assert res.answer.answer == _first(res).excerpt
        assert res.answer.claims_removed == 1
    finally:
        kb.close()


def test_other_leaver_answered_item_not_used(
    sessions: SessionStore, store: KnowledgeStore, kb: KnowledgeBase
) -> None:
    svc = SuccessorService(store, clock=lambda: NOW, sessions=sessions)
    _ask(svc, kb, QUESTION, leaver="kim")
    _answer_in_session(sessions, store, kb, "q1", ANSWER, leaver="kim")

    res = _ask(svc, kb, QUESTION, leaver="loop")
    assert res.answer.found is False
    assert res.queued is not None
    assert res.queued.id != "q1"
    assert res.queued.status == "pending"
    assert len(svc.list("loop")) == 1
    assert len(svc.list("kim")) == 1


def test_linked_excerpt_scrubs_contacts(
    sessions: SessionStore, store: KnowledgeStore, tmp_path: Path
) -> None:
    kb = _kb(tmp_path, lambda q, a, c: (None, 1))
    try:
        svc = SuccessorService(store, clock=lambda: NOW, sessions=sessions)
        _ask(svc, kb, QUESTION)
        _answer_in_session(sessions, store, kb, "q1", f"{ANSWER} 급하면 {PHONE} 로 연락합니다.")

        res = _ask(svc, kb, QUESTION)
        assert res.answer.found is True
        assert all(PHONE not in c.excerpt for c in res.answer.citations)
        assert PHONE not in res.answer.answer
    finally:
        kb.close()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  A  b ?", "A b"),
        ("x？", "x"),
        ("x?!.", "x"),
        ("PG 점검 공지는 어디서 봐요?", "PG 점검 공지는 어디서 봐요"),
    ],
)
def test_normalize_question(text: str, expected: str) -> None:
    assert normalize_question(text) == expected
