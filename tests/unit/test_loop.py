from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from handoff.cards import CardService
from handoff.contracts import CardDraft, QueueItem
from handoff.depth import DepthService
from handoff.kb import KBEntry, KnowledgeBase
from handoff.knowledge_store import KnowledgeStore
from handoff.loop import KnowledgeHooks
from handoff.providers import FakeEmbedder, FakeLLM, ProviderError
from handoff.question_tree import load_tree
from handoff.session import Question
from handoff.store import SessionRow, SessionStore

ROLE = "backend_developer"
LEAVER = "kim"
NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
FULL_ANSWER = (
    "매주 월요일에 정산 배치를 확인합니다. 누락을 막기 때문입니다. 결제팀 리드에게 공유하고 "
    "스크립트로 순서대로 재실행합니다. 안 되면 수동으로 대체합니다."
)
PARTIAL_ANSWER = "배포 전에 변경 목록을 확인합니다. 장애가 잦았기 때문입니다."


class FailingCardsLLM(FakeLLM):
    def extract_cards(self, entries: list[KBEntry], known_entities: list[str]) -> list[CardDraft]:
        raise ProviderError("down")


@pytest.fixture
def sessions() -> Iterator[SessionStore]:
    store = SessionStore(":memory:")
    yield store
    store.close()


@pytest.fixture
def kstore(sessions: SessionStore) -> KnowledgeStore:
    return KnowledgeStore.from_session_store(sessions)


def _hooks(
    kstore: KnowledgeStore,
    sessions: SessionStore,
    per_session: int = 2,
    max_followups: int = 1,
    llm: FakeLLM | None = None,
) -> KnowledgeHooks:
    model = llm if llm is not None else FakeLLM()
    depth = DepthService(model, kstore, sessions, max_followups, clock=lambda: NOW)
    cards = CardService(model, kstore, sessions, clock=lambda: NOW)
    return KnowledgeHooks(kstore, sessions, depth, cards, per_session)


def _enqueue(kstore: KnowledgeStore, text: str, source: str = "successor") -> QueueItem:
    item, created = kstore.enqueue(
        LEAVER, ROLE, source, "incidents", text, "후임 미답 질문", None, NOW  # type: ignore[arg-type]
    )
    assert created
    return item


def _session(sessions: SessionStore, session_id: str, ended: bool = False) -> None:
    sessions.insert_session(
        SessionRow(
            session_id=session_id,
            leaver=LEAVER,
            role=ROLE,
            number=1,
            started_at=NOW,
            deadline=NOW + timedelta(seconds=900),
            ended_at=NOW if ended else None,
            node_index=0,
            current_json=None,
            summary=[],
        )
    )


def _answer(sessions: SessionStore, session_id: str, q: Question, text: str) -> None:
    sessions.add_answer(
        KBEntry(
            session_id=session_id,
            question_id=q.id,
            axis=q.axis,
            question=q.text,
            answer=text,
            created_at=NOW,
            session_number=1,
        )
    )


def _queued(item: QueueItem) -> Question:
    return Question(id=item.id, axis=item.axis, text=item.text, kind="queued")


def test_next_queued_marks_asked(kstore: KnowledgeStore, sessions: SessionStore) -> None:
    item = _enqueue(kstore, "정산 배치가 실패하면 어떻게 하나요?")
    hooks = _hooks(kstore, sessions)
    got = hooks.next_queued("s1", LEAVER, ROLE)
    assert got is not None and got.id == item.id
    stored = kstore.get_queue_item(item.id)
    assert stored is not None
    assert stored.status == "asked"
    assert stored.asked_session_id == "s1"


def test_next_queued_none_when_empty(kstore: KnowledgeStore, sessions: SessionStore) -> None:
    assert _hooks(kstore, sessions).next_queued("s1", LEAVER, ROLE) is None


def test_next_queued_respects_priority(kstore: KnowledgeStore, sessions: SessionStore) -> None:
    _enqueue(kstore, "생성 질문입니다?", source="generated")
    gap = _enqueue(kstore, "빈 구멍 질문입니다?", source="gap")
    succ = _enqueue(kstore, "후임 질문입니다?", source="successor")
    hooks = _hooks(kstore, sessions)
    first = hooks.next_queued("s1", LEAVER, ROLE)
    second = hooks.next_queued("s1", LEAVER, ROLE)
    assert first is not None and first.id == succ.id
    assert second is not None and second.id == gap.id


def test_next_queued_per_session_limit(kstore: KnowledgeStore, sessions: SessionStore) -> None:
    for i in range(3):
        _enqueue(kstore, f"질문 {i} 은 무엇인가요?")
    hooks = _hooks(kstore, sessions, per_session=2)
    assert hooks.next_queued("s1", LEAVER, ROLE) is not None
    assert hooks.next_queued("s1", LEAVER, ROLE) is not None
    assert hooks.next_queued("s1", LEAVER, ROLE) is None
    # 다른 세션은 자기 상한을 따로 갖는다
    assert hooks.next_queued("s2", LEAVER, ROLE) is not None


def test_next_queued_limit_zero(kstore: KnowledgeStore, sessions: SessionStore) -> None:
    item = _enqueue(kstore, "정산 배치가 실패하면 어떻게 하나요?")
    assert _hooks(kstore, sessions, per_session=0).next_queued("s1", LEAVER, ROLE) is None
    stored = kstore.get_queue_item(item.id)
    assert stored is not None and stored.status == "pending"


def test_after_answer_slot_follow_up_keeps_item_asked(
    kstore: KnowledgeStore, sessions: SessionStore
) -> None:
    item = _enqueue(kstore, "배포 전에 무엇을 하나요?")
    hooks = _hooks(kstore, sessions)
    _session(sessions, "s1")
    assert hooks.next_queued("s1", LEAVER, ROLE) is not None
    q = _queued(item)
    _answer(sessions, "s1", q, PARTIAL_ANSWER)
    nxt = hooks.after_answer("s1", q, PARTIAL_ANSWER, [])
    assert nxt is not None
    assert nxt.kind == "follow_up"
    assert nxt.id == f"{item.id}-s1"
    assert "언제" in nxt.text
    stored = kstore.get_queue_item(item.id)
    assert stored is not None and stored.status == "asked"
    assert len(kstore.list_depth("s1")) == 1


def test_after_answer_follow_up_limit_marks_answered(
    kstore: KnowledgeStore, sessions: SessionStore
) -> None:
    item = _enqueue(kstore, "배포 전에 무엇을 하나요?")
    hooks = _hooks(kstore, sessions)
    _session(sessions, "s1")
    hooks.next_queued("s1", LEAVER, ROLE)
    q = _queued(item)
    _answer(sessions, "s1", q, PARTIAL_ANSWER)
    follow = hooks.after_answer("s1", q, PARTIAL_ANSWER, [])
    assert follow is not None
    _answer(sessions, "s1", follow, "추가로 기억나는 것은 없습니다.")
    assert hooks.after_answer("s1", follow, "추가로 기억나는 것은 없습니다.", []) is None
    stored = kstore.get_queue_item(item.id)
    assert stored is not None
    assert stored.status == "answered"
    assert stored.answered_session_id == "s1"


def test_after_answer_full_queue_answer_marks_answered(
    kstore: KnowledgeStore, sessions: SessionStore
) -> None:
    item = _enqueue(kstore, "정산 배치는 어떻게 다루나요?")
    hooks = _hooks(kstore, sessions)
    _session(sessions, "s1")
    hooks.next_queued("s1", LEAVER, ROLE)
    q = _queued(item)
    _answer(sessions, "s1", q, FULL_ANSWER)
    assert hooks.after_answer("s1", q, FULL_ANSWER, []) is None
    stored = kstore.get_queue_item(item.id)
    assert stored is not None and stored.status == "answered"


def test_after_answer_tree_question_uses_candidates(
    kstore: KnowledgeStore, sessions: SessionStore
) -> None:
    node = load_tree(ROLE).nodes[0]
    hooks = _hooks(kstore, sessions)
    _session(sessions, "s1")
    q = Question(id=node.id, axis=node.axis, text=node.text, kind="main")
    _answer(sessions, "s1", q, FULL_ANSWER)
    nxt = hooks.after_answer("s1", q, FULL_ANSWER, node.follow_ups)
    assert nxt is not None
    assert nxt.kind == "follow_up"
    assert nxt.id in {f.id for f in node.follow_ups}
    assert kstore.list_queue(LEAVER) == []


def test_on_end_marks_answered_and_resets_unanswered(
    kstore: KnowledgeStore, sessions: SessionStore
) -> None:
    answered = _enqueue(kstore, "정산 배치는 어떻게 다루나요?")
    unanswered = _enqueue(kstore, "로그 보관은 어떻게 하나요?")
    other = _enqueue(kstore, "다른 세션 질문은 무엇인가요?")
    hooks = _hooks(kstore, sessions, per_session=3)
    _session(sessions, "s1")
    hooks.next_queued("s1", LEAVER, ROLE)
    hooks.next_queued("s1", LEAVER, ROLE)
    hooks.next_queued("s0", LEAVER, ROLE)  # 다른 세션에서 출제 중
    q = _queued(answered)
    # 꼬리 질문 답만 남은 경우도 주제 id 로 답한 것으로 본다
    follow = Question(id=f"{answered.id}-s1", axis=q.axis, text="언제 하나요?", kind="follow_up")
    _answer(sessions, "s1", q, FULL_ANSWER)
    _answer(sessions, "s1", follow, "매주 합니다.")
    sessions.update_session(_ended_row(sessions, "s1"))
    hooks.on_end("s1", LEAVER, ROLE, sessions.answers("s1"))

    a = kstore.get_queue_item(answered.id)
    u = kstore.get_queue_item(unanswered.id)
    o = kstore.get_queue_item(other.id)
    assert a is not None and a.status == "answered" and a.answered_session_id == "s1"
    assert u is not None and u.status == "pending" and u.asked_session_id is None
    assert o is not None and o.status == "asked" and o.asked_session_id == "s0"


def _ended_row(sessions: SessionStore, session_id: str) -> SessionRow:
    row = sessions.get_session(session_id)
    assert row is not None
    row.ended_at = NOW
    return row


def test_on_end_extracts_cards(kstore: KnowledgeStore, sessions: SessionStore) -> None:
    node = load_tree(ROLE).nodes[0]
    hooks = _hooks(kstore, sessions)
    _session(sessions, "s1", ended=True)
    q = Question(id=node.id, axis=node.axis, text=node.text, kind="main")
    _answer(sessions, "s1", q, FULL_ANSWER)
    hooks.on_end("s1", LEAVER, ROLE, sessions.answers("s1"))
    cards = kstore.list_cards(LEAVER)
    assert len(cards) >= 1
    assert all(e.session_id == "s1" for c in cards for e in c.evidence)


def test_on_end_ignores_card_provider_error(
    kstore: KnowledgeStore, sessions: SessionStore
) -> None:
    item = _enqueue(kstore, "정산 배치는 어떻게 다루나요?")
    hooks = _hooks(kstore, sessions, llm=FailingCardsLLM())
    _session(sessions, "s1", ended=True)
    hooks.next_queued("s1", LEAVER, ROLE)
    _answer(sessions, "s1", _queued(item), FULL_ANSWER)
    hooks.on_end("s1", LEAVER, ROLE, sessions.answers("s1"))
    stored = kstore.get_queue_item(item.id)
    assert stored is not None and stored.status == "answered"
    assert kstore.list_cards(LEAVER) == []


def test_hooks_drive_session_service(tmp_path: Path) -> None:
    from handoff.session import SessionService

    store = SessionStore(":memory:")
    kstore = KnowledgeStore.from_session_store(store)
    hooks = _hooks(kstore, store)
    kb = KnowledgeBase(tmp_path / "qdrant", FakeEmbedder(), FakeLLM(), 0.25)
    service = SessionService(kb, FakeLLM(), clock=lambda: NOW, store=store, hooks=hooks)
    try:
        item = _enqueue(kstore, "정산 배치는 어떻게 다루나요?")
        sid, first = service.start(ROLE, LEAVER)
        assert first.kind == "queued" and first.id == item.id
        nxt = service.answer(sid, FULL_ANSWER)
        assert nxt is not None and nxt.kind == "main"
        assert nxt.id == load_tree(ROLE).nodes[0].id
        service.end(sid)
        stored = kstore.get_queue_item(item.id)
        assert stored is not None and stored.status == "answered"
    finally:
        service.close()
        kb.close()
