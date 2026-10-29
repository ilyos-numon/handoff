from datetime import UTC, datetime, timedelta

import pytest

from handoff.contracts import QUESTION_NOT_FOUND, KBEntry, QuestionThread, QueueItem
from handoff.knowledge_store import KnowledgeStore
from handoff.store import SessionRow, SessionStore
from handoff.verify import SuccessorService

LEAVER = "kim"
ROLE = "backend_developer"
OTHER_ROLE = "data_engineer"
QUESTION = "PG 점검 공지는 어디서 봐요?"
ANSWER = "결제대행사 관리자 페이지 게시판을 매주 월요일 아침에 먼저 확인합니다."
FOLLOW_Q = "공지가 없으면 누구에게 물어보나요?"
FOLLOW_A = "결제 담당 운영팀에 메신저로 먼저 물어봅니다."
NOW = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)


@pytest.fixture
def sessions() -> SessionStore:
    return SessionStore(":memory:")


@pytest.fixture
def store(sessions: SessionStore) -> KnowledgeStore:
    return KnowledgeStore.from_session_store(sessions)


@pytest.fixture
def svc(sessions: SessionStore, store: KnowledgeStore) -> SuccessorService:
    return SuccessorService(store, clock=lambda: NOW, sessions=sessions)


def _enqueue(store: KnowledgeStore, *, leaver: str = LEAVER, role: str = ROLE,
             text: str = QUESTION) -> QueueItem:
    item, _ = store.enqueue(leaver, role, "successor", "systems", text, "후임 질문", None, NOW)
    return item


def _session(sessions: SessionStore, sid: str = "sess-1", *, leaver: str = LEAVER,
             role: str = ROLE, number: int = 1) -> SessionRow:
    row = SessionRow(
        session_id=sid,
        leaver=leaver,
        role=role,
        number=number,
        started_at=NOW,
        deadline=NOW + timedelta(minutes=30),
        ended_at=None,
        node_index=0,
        current_json=None,
        summary=[],
    )
    sessions.insert_session(row)
    return row


def _answer(sessions: SessionStore, row: SessionRow, qid: str, question: str, answer: str,
            minutes: int = 1) -> None:
    sessions.add_answer(
        KBEntry(
            session_id=row.session_id,
            question_id=qid,
            axis="systems",
            question=question,
            answer=answer,
            created_at=NOW + timedelta(minutes=minutes),
            session_number=row.number,
        )
    )


def _end(sessions: SessionStore, row: SessionRow) -> None:
    row.ended_at = NOW + timedelta(minutes=10)
    sessions.update_session(row)


def _thread(svc: SuccessorService, qid: str, leaver: str = LEAVER) -> QuestionThread:
    t = svc.thread(leaver, qid)
    assert t is not None
    return t


def test_question_not_found_constant() -> None:
    assert QUESTION_NOT_FOUND == "question not found"


def test_waiting(svc: SuccessorService, store: KnowledgeStore) -> None:
    item = _enqueue(store)
    t = _thread(svc, item.id)
    assert t.state == "waiting"
    assert t.turns == []
    assert t.item.id == item.id


def test_asking(svc: SuccessorService, store: KnowledgeStore, sessions: SessionStore) -> None:
    item = _enqueue(store)
    _session(sessions)
    store.mark_asked(item.id, "sess-1")
    t = _thread(svc, item.id)
    assert t.state == "asking"
    assert t.turns == []


def test_answered_open_before_session_end(
    svc: SuccessorService, store: KnowledgeStore, sessions: SessionStore
) -> None:
    item = _enqueue(store)
    row = _session(sessions)
    _answer(sessions, row, item.id, QUESTION, ANSWER)
    store.mark_answered(item.id, "sess-1")
    t = _thread(svc, item.id)
    assert t.state == "answered_open"
    assert t.turns == []


def test_answered_after_session_end(
    svc: SuccessorService, store: KnowledgeStore, sessions: SessionStore
) -> None:
    item = _enqueue(store)
    row = _session(sessions)
    _answer(sessions, row, item.id, QUESTION, ANSWER)
    store.mark_answered(item.id, "sess-1")
    _end(sessions, row)
    t = _thread(svc, item.id)
    assert t.state == "answered"
    assert len(t.turns) == 1
    turn = t.turns[0]
    assert turn.question_id == item.id
    assert turn.follow_up is False
    assert turn.question == QUESTION
    assert turn.answer == ANSWER
    assert turn.session_number == 1
    assert turn.created_at == NOW + timedelta(minutes=1)


def test_follow_up_turns_in_order(
    svc: SuccessorService, store: KnowledgeStore, sessions: SessionStore
) -> None:
    item = _enqueue(store)
    row = _session(sessions)
    _answer(sessions, row, item.id, QUESTION, ANSWER, minutes=1)
    _answer(sessions, row, f"{item.id}-s1", FOLLOW_Q, FOLLOW_A, minutes=2)
    store.mark_answered(item.id, "sess-1")
    _end(sessions, row)
    t = _thread(svc, item.id)
    assert t.state == "answered"
    assert [x.question_id for x in t.turns] == [item.id, f"{item.id}-s1"]
    assert [x.follow_up for x in t.turns] == [False, True]
    assert t.turns[1].question == FOLLOW_Q
    assert t.turns[1].answer == FOLLOW_A


def test_turns_scrub_contacts_and_keep_full_text(
    svc: SuccessorService, store: KnowledgeStore, sessions: SessionStore
) -> None:
    item = _enqueue(store)
    row = _session(sessions)
    long_tail = "가" * 400
    _answer(
        sessions, row, item.id, "a.b@example.com 로 물어봐도 되나요?",
        f"담당자 a.b@example.com 또는 010-1234-5678 로 연락합니다. {long_tail}",
    )
    store.mark_answered(item.id, "sess-1")
    _end(sessions, row)
    turn = _thread(svc, item.id).turns[0]
    assert "a.b@example.com" not in turn.answer
    assert "010-1234-5678" not in turn.answer
    assert "[이메일]" in turn.answer
    assert "[전화번호]" in turn.answer
    assert turn.answer.endswith(long_tail)
    assert "a.b@example.com" not in turn.question
    assert "[이메일]" in turn.question


def test_other_leaver_path_is_none(svc: SuccessorService, store: KnowledgeStore) -> None:
    item = _enqueue(store)
    assert svc.thread("park", item.id) is None


def test_missing_qid_is_none(svc: SuccessorService, store: KnowledgeStore) -> None:
    _enqueue(store)
    assert svc.thread(LEAVER, "q999") is None


def test_other_role_session_answers_excluded(
    svc: SuccessorService, store: KnowledgeStore, sessions: SessionStore
) -> None:
    item = _enqueue(store)
    other = _session(sessions, "sess-x", role=OTHER_ROLE)
    _answer(sessions, other, item.id, QUESTION, "다른 직무 세션의 답")
    _end(sessions, other)
    store.mark_answered(item.id, "sess-x")
    t = _thread(svc, item.id)
    assert t.turns == []
    assert t.state == "answered_open"


def test_other_item_answers_excluded(
    svc: SuccessorService, store: KnowledgeStore, sessions: SessionStore
) -> None:
    item = _enqueue(store)
    other_item = _enqueue(store, text="배포는 언제 하나요?")
    row = _session(sessions)
    _answer(sessions, row, other_item.id, "배포는 언제 하나요?", "매주 목요일 오후에 합니다.")
    _answer(sessions, row, f"{item.id}0", "다른 번호", "다른 번호 답")
    _end(sessions, row)
    t = _thread(svc, item.id)
    assert t.turns == []
    assert t.state == "waiting"


def test_no_session_store_gives_empty_turns(store: KnowledgeStore) -> None:
    svc = SuccessorService(store, clock=lambda: NOW)
    item = _enqueue(store)
    store.mark_answered(item.id, "sess-1")
    t = _thread(svc, item.id)
    assert t.turns == []
    assert t.state == "answered_open"
