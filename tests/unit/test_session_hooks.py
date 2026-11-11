from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from handoff.contracts import QueueItem
from handoff.kb import KBEntry, KnowledgeBase
from handoff.providers import FakeEmbedder, FakeLLM, ProviderError
from handoff.question_tree import FollowUp, load_tree
from handoff.session import Question, SessionService, TreeExhaustedError
from handoff.store import SessionStore

ROLE = "backend_developer"
LEAVER = "kim"
MIN_SCORE = 0.25
FIXED_NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
ANSWER = "배포 전에 변경 목록을 확인합니다. 장애가 잦았기 때문입니다."


def _item(item_id: str, text: str = "정산 배치가 실패하면 어떻게 하나요?") -> QueueItem:
    return QueueItem(
        id=item_id,
        leaver=LEAVER,
        role=ROLE,
        source="successor",
        axis="incidents",
        text=text,
        reason="후임 미답 질문",
        status="pending",
        created_at=FIXED_NOW,
    )


class FakeHooks:
    def __init__(
        self,
        queue: list[QueueItem] | None = None,
        follow_ups: dict[str, Question] | None = None,
        end_error: Exception | None = None,
    ) -> None:
        self.queue = list(queue or [])
        self.follow_ups = dict(follow_ups or {})
        self.end_error = end_error
        self.next_calls: list[tuple[str, str, str]] = []
        self.after_calls: list[tuple[str, Question, str, list[FollowUp]]] = []
        self.end_calls: list[tuple[str, str, str, list[KBEntry]]] = []

    def next_queued(self, session_id: str, leaver: str, role: str) -> QueueItem | None:
        self.next_calls.append((session_id, leaver, role))
        return self.queue.pop(0) if self.queue else None

    def after_answer(
        self, session_id: str, answered: Question, text: str, candidates: list[FollowUp]
    ) -> Question | None:
        self.after_calls.append((session_id, answered, text, list(candidates)))
        return self.follow_ups.pop(answered.id, None)

    def on_end(self, session_id: str, leaver: str, role: str, answers: list[KBEntry]) -> None:
        self.end_calls.append((session_id, leaver, role, list(answers)))
        if self.end_error is not None:
            raise self.end_error


@pytest.fixture
def kb(tmp_path: Path) -> Iterator[KnowledgeBase]:
    instance = KnowledgeBase(tmp_path / "qdrant", FakeEmbedder(), FakeLLM(), MIN_SCORE)
    yield instance
    instance.close()


@pytest.fixture
def store() -> Iterator[SessionStore]:
    instance = SessionStore(":memory:")
    yield instance
    instance.close()


def _service(
    kb: KnowledgeBase, store: SessionStore, hooks: FakeHooks | None
) -> SessionService:
    return SessionService(kb, FakeLLM(), clock=lambda: FIXED_NOW, store=store, hooks=hooks)


def test_start_returns_queued_question_first(kb: KnowledgeBase, store: SessionStore) -> None:
    hooks = FakeHooks(queue=[_item("q1")])
    session_id, question = _service(kb, store, hooks).start(ROLE, LEAVER)
    assert question == Question(
        id="q1",
        axis="incidents",
        text="정산 배치가 실패하면 어떻게 하나요?",
        kind="queued",
        source="successor",
        reason="후임 미답 질문",
    )
    assert hooks.next_calls == [(session_id, LEAVER, ROLE)]


def test_start_without_queue_returns_tree_main(kb: KnowledgeBase, store: SessionStore) -> None:
    hooks = FakeHooks()
    _, question = _service(kb, store, hooks).start(ROLE, LEAVER)
    assert question.kind == "main"
    assert question.id == load_tree(ROLE).nodes[0].id


def test_queued_topic_done_then_tree_main(kb: KnowledgeBase, store: SessionStore) -> None:
    hooks = FakeHooks(queue=[_item("q1")])
    service = _service(kb, store, hooks)
    session_id, _ = service.start(ROLE, LEAVER)
    nxt = service.answer(session_id, ANSWER)
    first = load_tree(ROLE).nodes[0]
    assert nxt is not None
    assert nxt.kind == "main"
    assert nxt.id == first.id
    _, answered, text, candidates = hooks.after_calls[0]
    assert answered.id == "q1"
    assert text == ANSWER
    assert candidates == []
    row = store.get_session(session_id)
    assert row is not None and row.node_index == 0


def test_tree_answer_passes_candidates_and_follow_up(
    kb: KnowledgeBase, store: SessionStore
) -> None:
    tree = load_tree(ROLE)
    first = tree.nodes[0]
    slot_q = Question(id=f"{first.id}-s1", axis=first.axis, text="언제 하나요?", kind="follow_up")
    hooks = FakeHooks(follow_ups={first.id: slot_q})
    service = _service(kb, store, hooks)
    session_id, _ = service.start(ROLE, LEAVER)
    nxt = service.answer(session_id, ANSWER)
    assert nxt == slot_q
    assert hooks.after_calls[0][3] == first.follow_ups
    row = store.get_session(session_id)
    assert row is not None and row.node_index == 0
    nxt2 = service.answer(session_id, "매주 월요일 오전에 합니다.")
    assert hooks.after_calls[1][3] == first.follow_ups
    assert nxt2 is not None
    assert nxt2.kind == "main"
    assert nxt2.id == tree.nodes[1].id


def test_queue_between_tree_topics(kb: KnowledgeBase, store: SessionStore) -> None:
    hooks = FakeHooks()
    service = _service(kb, store, hooks)
    session_id, _ = service.start(ROLE, LEAVER)
    hooks.queue.append(_item("q7"))
    nxt = service.answer(session_id, ANSWER)
    assert nxt is not None and nxt.kind == "queued" and nxt.id == "q7"
    row = store.get_session(session_id)
    assert row is not None and row.node_index == 1
    nxt2 = service.answer(session_id, ANSWER)
    assert nxt2 is not None and nxt2.id == load_tree(ROLE).nodes[1].id
    assert hooks.after_calls[1][3] == []


def test_unanswered_queue_question_keeps_tree_position(
    kb: KnowledgeBase, store: SessionStore
) -> None:
    hooks = FakeHooks()
    service = _service(kb, store, hooks)
    sid1, _ = service.start(ROLE, LEAVER)
    service.answer(sid1, ANSWER)
    service.answer(sid1, ANSWER)
    hooks.queue.append(_item("q3"))
    nxt = service.answer(sid1, ANSWER)
    assert nxt is not None and nxt.kind == "queued"
    row = store.get_session(sid1)
    assert row is not None
    tree_index = row.node_index
    service.end(sid1)
    _, question = service.start(ROLE, LEAVER)
    assert question.kind == "main"
    assert question.id == load_tree(ROLE).nodes[tree_index].id


def test_queue_follow_up_current_keeps_tree_position_without_hooks(
    kb: KnowledgeBase, store: SessionStore
) -> None:
    follow = Question(id="q1-s1", axis="incidents", text="언제 하나요?", kind="follow_up")
    hooks = FakeHooks(queue=[_item("q1")], follow_ups={"q1": follow})
    service = _service(kb, store, hooks)
    sid1, _ = service.start(ROLE, LEAVER)
    nxt = service.answer(sid1, ANSWER)
    assert nxt == follow
    service.end(sid1)
    _, question = _service(kb, store, None).start(ROLE, LEAVER)
    assert question.id == load_tree(ROLE).nodes[0].id


def test_answered_queue_then_end_restarts_same_main(
    kb: KnowledgeBase, store: SessionStore
) -> None:
    hooks = FakeHooks(queue=[_item("q1")])
    service = _service(kb, store, hooks)
    sid1, _ = service.start(ROLE, LEAVER)
    service.answer(sid1, ANSWER)
    service.end(sid1)
    _, question = service.start(ROLE, LEAVER)
    assert question.id == load_tree(ROLE).nodes[0].id


def test_on_end_receives_answers(kb: KnowledgeBase, store: SessionStore) -> None:
    hooks = FakeHooks(queue=[_item("q1")])
    service = _service(kb, store, hooks)
    session_id, _ = service.start(ROLE, LEAVER)
    service.answer(session_id, ANSWER)
    assert service.end(session_id) == 1
    assert len(hooks.end_calls) == 1
    sid, leaver, role, answers = hooks.end_calls[0]
    assert (sid, leaver, role) == (session_id, LEAVER, ROLE)
    assert [a.question_id for a in answers] == ["q1"]


def test_on_end_provider_error_ignored(kb: KnowledgeBase, store: SessionStore) -> None:
    hooks = FakeHooks(end_error=ProviderError("down"))
    service = _service(kb, store, hooks)
    session_id, _ = service.start(ROLE, LEAVER)
    service.answer(session_id, ANSWER)
    assert service.end(session_id) == 1
    assert service.view(session_id).status == "ended"


def test_on_end_other_error_propagates(kb: KnowledgeBase, store: SessionStore) -> None:
    hooks = FakeHooks(end_error=ValueError("bug"))
    service = _service(kb, store, hooks)
    session_id, _ = service.start(ROLE, LEAVER)
    with pytest.raises(ValueError):
        service.end(session_id)


def test_exhausted_tree_still_serves_queue(kb: KnowledgeBase, store: SessionStore) -> None:
    tree = load_tree(ROLE)
    service = _service(kb, store, FakeHooks())
    service_plain = _service(kb, store, None)
    session_id, _ = service_plain.start(ROLE, LEAVER)
    nxt: Question | None = Question(id="x", axis="systems", text="x", kind="main")
    while nxt is not None:
        nxt = service_plain.answer(session_id, ANSWER)
    service_plain.end(session_id)
    with pytest.raises(TreeExhaustedError):
        service.start(ROLE, LEAVER)
    hooks = FakeHooks(queue=[_item("q9")])
    service_q = _service(kb, store, hooks)
    sid, question = service_q.start(ROLE, LEAVER)
    assert question.kind == "queued" and question.id == "q9"
    assert service_q.answer(sid, ANSWER) is None
    assert len(hooks.after_calls) == 1
    row = store.get_session(sid)
    assert row is not None and row.node_index == len(tree.nodes)
