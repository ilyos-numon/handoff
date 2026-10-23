import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from handoff.contracts import (
    SESSION_NOT_FOUND,
    SLOT_FOLLOW_UP_TEMPLATES,
    SLOT_TARGET_CUES,
    SLOTS,
    DepthJudgement,
    KnowledgeNotFound,
    Slot,
    SlotJudgement,
)
from handoff.depth import DepthService
from handoff.kb import KBEntry
from handoff.knowledge_store import KnowledgeStore
from handoff.llm_tasks.depth import (
    fake_judge_depth,
    fake_slot_follow_up,
    qwen_judge_depth,
    qwen_slot_follow_up,
)
from handoff.providers import FakeLLM, ProviderError
from handoff.question_tree import FollowUp
from handoff.routes_depth import router
from handoff.session import Question
from handoff.store import SessionRow, SessionStore

T0 = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
SPEC_ANSWER = "배포 전에 변경 목록을 확인합니다. 장애가 잦았기 때문입니다."
FULL_ANSWER = (
    "배포 전에 변경 목록을 확인합니다. 장애가 잦았기 때문입니다. 매주 화요일 오전에 합니다. "
    "결제팀 리드에게 알립니다. 안 되면 이전 버전으로 되돌립니다."
)
ROOT_Q = "담당하는 시스템 중 배포가 가장 무서운 부분은 어디인가요? 왜요?"
CANDIDATES = [
    FollowUp(id="sys-01-f1", text="그 부분에서 문제가 났을 때 어떻게 복구했나요?"),
    FollowUp(id="sys-01-f2", text="꼭 확인하는 설정값이 있나요?"),
]


class FailingLLM(FakeLLM):
    """LLM whose every call raises ProviderError."""

    def judge_depth(self, question: str, answer: str) -> SlotJudgement:
        raise ProviderError("llm request failed: status=503")

    def slot_follow_up(self, question: str, answer: str, slot: Slot) -> str:
        raise ProviderError("llm request failed: status=503")

    def follow_up(
        self, question: str, answer: str, candidates: list[FollowUp]
    ) -> FollowUp | None:
        raise ProviderError("llm request failed: status=503")


class EmptySlotLLM(FakeLLM):
    def slot_follow_up(self, question: str, answer: str, slot: Slot) -> str:
        return "   "


class PickSecondLLM(FakeLLM):
    def follow_up(
        self, question: str, answer: str, candidates: list[FollowUp]
    ) -> FollowUp | None:
        return candidates[1]


@pytest.fixture
def sessions() -> SessionStore:
    store = SessionStore(":memory:")
    store.insert_session(
        SessionRow(
            session_id="s1", leaver="alice", role="backend_developer", number=1,
            started_at=T0, deadline=T0 + timedelta(minutes=30), ended_at=None,
            node_index=0, current_json=None, summary=[],
        )
    )
    return store


@pytest.fixture
def ks(sessions: SessionStore) -> KnowledgeStore:
    return KnowledgeStore.from_session_store(sessions)


def _service(sessions: SessionStore, ks: KnowledgeStore, llm: object = None,
             max_followups: int = 1) -> DepthService:
    return DepthService(llm or FakeLLM(), ks, sessions, max_followups, clock=lambda: T0)  # type: ignore[arg-type]


def _answer(sessions: SessionStore, question_id: str, question: str, answer: str) -> None:
    sessions.add_answer(
        KBEntry(session_id="s1", question_id=question_id, axis="systems", question=question,
                answer=answer, created_at=T0, session_number=1)
    )


def _main_q() -> Question:
    return Question(id="sys-01", axis="systems", text=ROOT_Q, kind="main")


# ---- llm_tasks fake


def test_fake_judge_spec_example() -> None:
    j = fake_judge_depth("배포 전에 무엇을 하나요?", SPEC_ANSWER)
    assert j.slots["how"] == "filled"
    assert j.slots["why"] == "filled"
    assert j.missing[0] == "when"
    assert j.missing == [s for s in SLOTS if j.slots[s] == "missing"]


def test_fake_slot_follow_up_uses_template() -> None:
    assert fake_slot_follow_up("q", "a", "when") == SLOT_FOLLOW_UP_TEMPLATES["when"]
    assert "언제" in fake_slot_follow_up("q", "a", "when")


# ---- llm_tasks qwen (fake chat function only)


def _chat_returning(content: str) -> tuple[list[tuple[str, str, int, bool]], object]:
    calls: list[tuple[str, str, int, bool]] = []

    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        calls.append((system, user, max_tokens, json_mode))
        return content

    return calls, chat


def test_qwen_judge_parses_slots_in_slot_order() -> None:
    payload = {"slots": {"why": "filled", "when": "missing", "who": "not_applicable",
                         "how": "filled", "fallback": "missing"}}
    calls, chat = _chat_returning(json.dumps(payload))
    j = qwen_judge_depth(chat, "질문", "답변")  # type: ignore[arg-type]
    assert j.slots["who"] == "not_applicable"
    assert j.missing == ["when", "fallback"]
    system, user, _, json_mode = calls[0]
    assert json_mode is True
    assert "JSON" in system
    assert "답변" in user


@pytest.mark.parametrize(
    "content",
    ["not json", "[]", json.dumps({"slots": {"why": "filled"}}),
     json.dumps({"slots": {s: "maybe" for s in SLOTS}})],
)
def test_qwen_judge_parse_failure_falls_back_to_fake(content: str) -> None:
    _, chat = _chat_returning(content)
    assert qwen_judge_depth(chat, "q", SPEC_ANSWER) == fake_judge_depth("q", SPEC_ANSWER)  # type: ignore[arg-type]


def test_qwen_judge_propagates_provider_error() -> None:
    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        raise ProviderError("llm request failed: status=timeout")

    with pytest.raises(ProviderError):
        qwen_judge_depth(chat, "q", "a")


def test_qwen_slot_follow_up_parses_question() -> None:
    _, chat = _chat_returning(json.dumps({"question": " 그 작업은 언제 하나요? "}))
    assert qwen_slot_follow_up(chat, "q", "a", "when") == "그 작업은 언제 하나요?"  # type: ignore[arg-type]


@pytest.mark.parametrize("content", ["oops", json.dumps({"question": ""}), json.dumps({"q": 1})])
def test_qwen_slot_follow_up_parse_failure_uses_template(content: str) -> None:
    _, chat = _chat_returning(content)
    assert qwen_slot_follow_up(chat, "q", "a", "who") == SLOT_FOLLOW_UP_TEMPLATES["who"]  # type: ignore[arg-type]


def _chat_sequence(contents: list[str]) -> tuple[list[tuple[str, str, int, bool]], object]:
    calls: list[tuple[str, str, int, bool]] = []

    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        calls.append((system, user, max_tokens, json_mode))
        return contents[min(len(calls) - 1, len(contents) - 1)]

    return calls, chat


@pytest.mark.parametrize("slot", SLOTS)
def test_qwen_slot_follow_up_prompt_names_target_cues(slot: Slot) -> None:
    calls, chat = _chat_returning(json.dumps({"question": "그 일은 언제 하나요?"}))
    qwen_slot_follow_up(chat, "q", "a", slot)  # type: ignore[arg-type]
    _, user, _, _ = calls[0]
    assert any(cue in user for cue in SLOT_TARGET_CUES[slot])


def test_qwen_slot_follow_up_on_target_first_try_calls_once() -> None:
    calls, chat = _chat_sequence([json.dumps({"question": "그렇게 하는 이유는 무엇인가요?"})])
    assert qwen_slot_follow_up(chat, "q", "a", "why") == "그렇게 하는 이유는 무엇인가요?"  # type: ignore[arg-type]
    assert len(calls) == 1


def test_qwen_slot_follow_up_off_target_retries_once() -> None:
    calls, chat = _chat_sequence([
        json.dumps({"question": "이 경우 누구에게 확인하나요?"}),
        json.dumps({"question": "그렇게 확인하는 이유는 무엇인가요?"}),
    ])
    assert qwen_slot_follow_up(chat, "q", "a", "why") == "그렇게 확인하는 이유는 무엇인가요?"  # type: ignore[arg-type]
    assert len(calls) == 2
    assert "이유" in calls[1][1]


def test_qwen_slot_follow_up_off_target_twice_returns_last_text() -> None:
    calls, chat = _chat_sequence([json.dumps({"question": "그 밖에 더 말씀하실 것이 있나요?"})])
    assert qwen_slot_follow_up(chat, "q", "a", "why") == "그 밖에 더 말씀하실 것이 있나요?"  # type: ignore[arg-type]
    assert len(calls) == 2


def test_qwen_judge_prompt_defines_every_slot_and_prefers_missing() -> None:
    calls, chat = _chat_returning(json.dumps({"slots": {s: "missing" for s in SLOTS}}))
    qwen_judge_depth(chat, "q", "a")  # type: ignore[arg-type]
    system = calls[0][0]
    for slot in SLOTS:
        assert f"- {slot}:" in system
    assert "확실하지 않으면 missing" in system


# ---- DepthService.judge / suggest


def test_judge_provider_error_falls_back_to_cues(sessions: SessionStore, ks: KnowledgeStore) -> None:
    svc = _service(sessions, ks, FailingLLM())
    assert svc.judge("q", SPEC_ANSWER) == fake_judge_depth("q", SPEC_ANSWER)


def test_suggest_spec_example(sessions: SessionStore, ks: KnowledgeStore) -> None:
    resp = _service(sessions, ks).suggest("q", SPEC_ANSWER)
    assert resp.slot == "when"
    assert resp.text is not None and "언제" in resp.text
    assert ks.list_depth("s1") == []


def test_suggest_no_missing_returns_nones(sessions: SessionStore, ks: KnowledgeStore) -> None:
    resp = _service(sessions, ks).suggest("q", FULL_ANSWER)
    assert resp.slot is None and resp.text is None


@pytest.mark.parametrize("llm", [FailingLLM(), EmptySlotLLM()])
def test_suggest_falls_back_to_template(sessions: SessionStore, ks: KnowledgeStore,
                                        llm: FakeLLM) -> None:
    resp = _service(sessions, ks, llm).suggest("q", SPEC_ANSWER)
    assert resp.slot == "when"
    assert resp.text == SLOT_FOLLOW_UP_TEMPLATES["when"]


# ---- DepthService.decide_follow_up


def test_decide_spec_example_slot_question(sessions: SessionStore, ks: KnowledgeStore) -> None:
    svc = _service(sessions, ks)
    _answer(sessions, "sys-01", ROOT_Q, SPEC_ANSWER)
    q = svc.decide_follow_up("s1", _main_q(), SPEC_ANSWER, CANDIDATES)
    assert q is not None
    assert q.id == "sys-01-s1"
    assert q.kind == "follow_up"
    assert q.axis == "systems"
    assert "언제" in q.text
    [rec] = ks.list_depth("s1")
    assert rec.question_id == "sys-01"
    assert rec.follow_up_id == "sys-01-s1"
    assert rec.missing[0] == "when"
    assert rec.created_at == T0


def test_decide_round_cap_returns_none_on_second_call(sessions: SessionStore,
                                                      ks: KnowledgeStore) -> None:
    svc = _service(sessions, ks, max_followups=1)
    _answer(sessions, "sys-01", ROOT_Q, SPEC_ANSWER)
    first = svc.decide_follow_up("s1", _main_q(), SPEC_ANSWER, CANDIDATES)
    assert first is not None
    follow_answer = "그건 그냥 합니다."
    _answer(sessions, first.id, first.text, follow_answer)
    second = svc.decide_follow_up("s1", first, follow_answer, [])
    assert second is None
    recs = ks.list_depth("s1")
    assert [r.question_id for r in recs] == ["sys-01", "sys-01-s1"]
    assert recs[1].follow_up_id is None
    assert recs[1].missing  # still has empty slots, but the cap stops it


def test_decide_second_round_allowed_with_higher_cap(sessions: SessionStore,
                                                     ks: KnowledgeStore) -> None:
    svc = _service(sessions, ks, max_followups=2)
    _answer(sessions, "sys-01", ROOT_Q, SPEC_ANSWER)
    first = svc.decide_follow_up("s1", _main_q(), SPEC_ANSWER, CANDIDATES)
    assert first is not None
    follow_answer = "매주 월요일 오전에 합니다."
    _answer(sessions, first.id, first.text, follow_answer)
    second = svc.decide_follow_up("s1", first, follow_answer, [])
    assert second is not None
    assert second.id == "sys-01-s2"
    assert second.text == SLOT_FOLLOW_UP_TEMPLATES["who"]  # when filled by thread, next is who


def test_decide_no_missing_picks_tree_candidate(sessions: SessionStore, ks: KnowledgeStore) -> None:
    svc = _service(sessions, ks, PickSecondLLM())
    _answer(sessions, "sys-01", ROOT_Q, FULL_ANSWER)
    q = svc.decide_follow_up("s1", _main_q(), FULL_ANSWER, CANDIDATES)
    assert q is not None
    assert q.id == "sys-01-f2"
    assert q.text == CANDIDATES[1].text
    assert q.kind == "follow_up"
    [rec] = ks.list_depth("s1")
    assert rec.missing == []
    assert rec.follow_up_id == "sys-01-f2"


def test_decide_no_missing_no_candidates_returns_none(sessions: SessionStore,
                                                      ks: KnowledgeStore) -> None:
    svc = _service(sessions, ks)
    _answer(sessions, "sys-01", ROOT_Q, FULL_ANSWER)
    assert svc.decide_follow_up("s1", _main_q(), FULL_ANSWER, []) is None
    [rec] = ks.list_depth("s1")
    assert rec.follow_up_id is None


def test_decide_provider_error_fallbacks(sessions: SessionStore, ks: KnowledgeStore) -> None:
    svc = _service(sessions, ks, FailingLLM())
    _answer(sessions, "sys-01", ROOT_Q, SPEC_ANSWER)
    q = svc.decide_follow_up("s1", _main_q(), SPEC_ANSWER, CANDIDATES)
    assert q is not None
    assert q.id == "sys-01-s1"
    assert q.text == SLOT_FOLLOW_UP_TEMPLATES["when"]


def test_decide_provider_error_on_tree_follow_up_uses_first_candidate(
    sessions: SessionStore, ks: KnowledgeStore
) -> None:
    svc = _service(sessions, ks, FailingLLM())
    _answer(sessions, "sys-01", ROOT_Q, FULL_ANSWER)
    q = svc.decide_follow_up("s1", _main_q(), FULL_ANSWER, CANDIDATES)
    assert q is not None
    assert q.id == "sys-01-f1"


def test_decide_queue_topic(sessions: SessionStore, ks: KnowledgeStore) -> None:
    svc = _service(sessions, ks)
    _answer(sessions, "q7", "정산 배치는 언제 도나요?", SPEC_ANSWER)
    queued = Question(id="q7", axis="routines", text="정산 배치는 언제 도나요?", kind="queued")
    q = svc.decide_follow_up("s1", queued, SPEC_ANSWER, [])
    assert q is not None
    assert q.id == "q7-s1"
    assert q.axis == "routines"


# ---- DepthService.report


def _judgement(slots: dict[Slot, str], question_id: str = "sys-01") -> DepthJudgement:
    return DepthJudgement.model_validate({
        "session_id": "s1", "question_id": question_id, "slots": slots,
        "missing": [s for s in SLOTS if slots[s] == "missing"], "follow_up_id": None,
        "created_at": T0,
    })


def test_report_rate(sessions: SessionStore, ks: KnowledgeStore) -> None:
    ks.add_depth(_judgement({"why": "filled", "when": "missing", "who": "not_applicable",
                             "how": "filled", "fallback": "missing"}))
    ks.add_depth(_judgement({"why": "filled", "when": "filled", "who": "missing",
                             "how": "not_applicable", "fallback": "not_applicable"}, "sys-01-s1"))
    report = _service(sessions, ks).report("s1")
    assert report.session_id == "s1"
    assert len(report.judgements) == 2
    assert report.filled == 4
    assert report.applicable == 7
    assert report.slot_fill_rate == round(4 / 7, 3) == 0.571


def test_report_empty_is_zero(sessions: SessionStore, ks: KnowledgeStore) -> None:
    report = _service(sessions, ks).report("s1")
    assert report.judgements == []
    assert report.filled == 0 and report.applicable == 0
    assert report.slot_fill_rate == 0.0


def test_report_unknown_session(sessions: SessionStore, ks: KnowledgeStore) -> None:
    with pytest.raises(KnowledgeNotFound) as exc:
        _service(sessions, ks).report("nope")
    assert str(exc.value) == SESSION_NOT_FOUND


# ---- routes


class _BoomService:
    def judge(self, question: str, answer: str) -> SlotJudgement:
        raise ProviderError("llm request failed: status=503")

    def suggest(self, question: str, answer: str) -> object:
        raise ProviderError("llm request failed: status=503")


def _client(service: object) -> TestClient:
    app = FastAPI()
    app.state.knowledge = SimpleNamespace(depth=service)
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def client(sessions: SessionStore, ks: KnowledgeStore) -> Iterator[TestClient]:
    with _client(_service(sessions, ks)) as c:
        yield c


def test_route_judge(client: TestClient) -> None:
    resp = client.post("/depth/judge", json={"question": "q", "answer": SPEC_ANSWER})
    assert resp.status_code == 200
    body = resp.json()
    assert body["slots"]["how"] == "filled" and body["slots"]["why"] == "filled"
    assert body["missing"][0] == "when"


def test_route_judge_blank_answer_422(client: TestClient) -> None:
    resp = client.post("/depth/judge", json={"question": "q", "answer": "   "})
    assert resp.status_code == 422


def test_route_follow_up(client: TestClient, ks: KnowledgeStore) -> None:
    resp = client.post("/depth/follow-up", json={"question": "q", "answer": SPEC_ANSWER})
    assert resp.status_code == 200
    body = resp.json()
    assert body["slot"] == "when"
    assert "언제" in body["text"]
    assert ks.list_depth("s1") == []


def test_route_follow_up_none(client: TestClient) -> None:
    resp = client.post("/depth/follow-up", json={"question": "q", "answer": FULL_ANSWER})
    assert resp.status_code == 200
    assert resp.json() == {"slot": None, "text": None}


def test_route_follow_up_blank_422(client: TestClient) -> None:
    resp = client.post("/depth/follow-up", json={"question": " ", "answer": "a"})
    assert resp.status_code == 422


def test_route_session_depth(client: TestClient, ks: KnowledgeStore) -> None:
    ks.add_depth(_judgement({"why": "filled", "when": "missing", "who": "missing",
                             "how": "filled", "fallback": "missing"}))
    resp = client.get("/sessions/s1/depth")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["judgements"]) == 1
    assert body["filled"] == 2 and body["applicable"] == 5
    assert body["slot_fill_rate"] == 0.4
    assert not {"score", "rank", "grade"} & set(body)


def test_route_session_depth_404(client: TestClient) -> None:
    resp = client.get("/sessions/missing/depth")
    assert resp.status_code == 404
    assert resp.json() == {"detail": SESSION_NOT_FOUND}


@pytest.mark.parametrize("path", ["/depth/judge", "/depth/follow-up"])
def test_route_provider_error_503(path: str) -> None:
    with _client(_BoomService()) as c:
        resp = c.post(path, json={"question": "q", "answer": "a"})
    assert resp.status_code == 503
    assert resp.json() == {"detail": "provider unavailable"}

