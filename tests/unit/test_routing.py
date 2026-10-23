import json
from datetime import UTC, datetime

import pytest

from handoff.contracts import (
    Evidence,
    NewCard,
    RouteCandidate,
    RouteDraft,
)
from handoff.kb import KBEntry
from handoff.knowledge_store import KnowledgeStore
from handoff.llm_tasks.routing import (
    ROUTE_SYSTEM,
    fake_route_question,
    qwen_route_question,
)
from handoff.providers import FakeLLM, ProviderError
from handoff.routing import ROUTE_CANDIDATES_MAX, QuestionRouter
from handoff.store import SessionRow, SessionStore

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
LEAVER = "demo"
ROLE = "backend_developer"
CONTACT_BODY = "재고 동기화 문제는 물류팀 리드에게 먼저 묻습니다."
PEOPLE_ANSWER = "창고 라벨 프린터 고장은 시설 담당자에게 보냅니다."
Q_CONTACT = "재고 동기화 문제는 누구에게 묻나요?"
Q_PEOPLE = "창고 라벨 프린터 고장은 어디로 보내나요?"
Q_UNRELATED = "사내 카페 메뉴는 언제 바뀌나요?"


class FakeChat:
    def __init__(self, content: str | Exception) -> None:
        self.content = content
        self.calls: list[tuple[str, str, int, bool]] = []

    def __call__(self, system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        self.calls.append((system, user, max_tokens, json_mode))
        if isinstance(self.content, Exception):
            raise self.content
        return self.content


def _cand(ref: str, basis: str, text: str, roles: list[str]) -> RouteCandidate:
    return RouteCandidate(ref=ref, basis=basis, text=text, roles=roles)  # type: ignore[arg-type]


def _card(session_id: str, type_: str, title: str, body: str) -> NewCard:
    return NewCard(
        leaver=LEAVER,
        role=ROLE,
        session_id=session_id,
        type=type_,  # type: ignore[arg-type]
        title=title,
        body=body,
        entities=[],
        confidence=0.8,
        evidence=[Evidence(session_id=session_id, question_id="ppl-01", session_number=1, excerpt=body)],
    )


@pytest.fixture
def sessions() -> SessionStore:
    return SessionStore(":memory:")


@pytest.fixture
def store(sessions: SessionStore) -> KnowledgeStore:
    return KnowledgeStore(*sessions.shared())


def _add_session(sessions: SessionStore, sid: str, ended: bool, answers: list[tuple[str, str, str]]) -> None:
    sessions.insert_session(
        SessionRow(sid, LEAVER, ROLE, 1, NOW, NOW, NOW if ended else None, 0, None, [])
    )
    for qid, axis, text in answers:
        sessions.add_answer(
            KBEntry(
                session_id=sid,
                question_id=qid,
                axis=axis,  # type: ignore[arg-type]
                question="질문",
                answer=text,
                created_at=NOW,
            )
        )


# ---- fake_route_question
def test_fake_route_picks_best_match() -> None:
    cands = [
        _cand("c1", "contact_card", "회의실 예약은 총무 담당에게 요청합니다.", ["총무 담당"]),
        _cand("c2", "contact_card", CONTACT_BODY, ["물류팀 리드"]),
    ]
    assert fake_route_question(Q_CONTACT, cands) == RouteDraft(ref="c2", role="물류팀 리드")


def test_fake_route_none_below_threshold() -> None:
    cands = [_cand("c1", "contact_card", CONTACT_BODY, ["물류팀 리드"])]
    assert fake_route_question(Q_UNRELATED, cands) is None
    assert fake_route_question(Q_CONTACT, []) is None


def test_fake_route_tie_prefers_contact_card_then_input_order() -> None:
    cands = [
        _cand("a:s1:ppl-01", "people_answer", CONTACT_BODY, ["물류팀 리드"]),
        _cand("c5", "contact_card", CONTACT_BODY, ["물류팀 리드"]),
        _cand("c6", "contact_card", CONTACT_BODY, ["재고 담당"]),
    ]
    assert fake_route_question(Q_CONTACT, cands) == RouteDraft(ref="c5", role="물류팀 리드")


# ---- qwen_route_question
def test_qwen_route_valid_choice() -> None:
    cands = [
        _cand("c1", "contact_card", CONTACT_BODY, ["물류팀 리드"]),
        _cand("a:s1:ppl-02", "people_answer", PEOPLE_ANSWER, ["시설 담당자"]),
    ]
    chat = FakeChat(json.dumps({"ref": "a:s1:ppl-02", "role": "시설 담당자"}))
    assert qwen_route_question(chat, Q_CONTACT, cands) == RouteDraft(
        ref="a:s1:ppl-02", role="시설 담당자"
    )
    system, user, _, json_mode = chat.calls[0]
    assert system == ROUTE_SYSTEM and json_mode is True
    assert "[c1] (contact_card)" in user and "역할: 물류팀 리드" in user


def test_qwen_route_null_ref_is_none() -> None:
    cands = [_cand("c1", "contact_card", CONTACT_BODY, ["물류팀 리드"])]
    chat = FakeChat(json.dumps({"ref": None, "role": ""}))
    assert qwen_route_question(chat, Q_CONTACT, cands) is None


@pytest.mark.parametrize(
    "content",
    [
        json.dumps({"ref": "c1", "role": "대표 이사"}),
        json.dumps({"ref": "c9", "role": "물류팀 리드"}),
        "not json",
        json.dumps(["c1"]),
    ],
)
def test_qwen_route_invalid_falls_back_to_fake(content: str) -> None:
    cands = [_cand("c1", "contact_card", CONTACT_BODY, ["물류팀 리드"])]
    assert qwen_route_question(FakeChat(content), Q_CONTACT, cands) == RouteDraft(
        ref="c1", role="물류팀 리드"
    )


def test_qwen_route_provider_error_propagates() -> None:
    cands = [_cand("c1", "contact_card", CONTACT_BODY, ["물류팀 리드"])]
    with pytest.raises(ProviderError):
        qwen_route_question(FakeChat(ProviderError("down")), Q_CONTACT, cands)


# ---- QuestionRouter
def test_candidates_contact_cards_and_people_answers(
    store: KnowledgeStore, sessions: SessionStore
) -> None:
    store.replace_session_cards(
        "s1",
        [
            _card("s1", "contact", "재고 동기화 연락처", CONTACT_BODY),
            _card("s1", "procedure", "재고 확인", "물류팀 리드에게 확인합니다."),
            _card("s1", "contact", "연락처 없음", "채널에 올립니다."),
        ],
        NOW,
    )
    _add_session(
        sessions,
        "s1",
        True,
        [("ppl-01", "people", PEOPLE_ANSWER), ("sys-01", "systems", "시설 담당자가 관리합니다.")],
    )
    _add_session(sessions, "s2", False, [("ppl-02", "people", "보안 담당자에게 묻습니다.")])
    cands = QuestionRouter(FakeLLM(), store, sessions).candidates(LEAVER, ROLE)
    assert [(c.ref, c.basis, c.roles) for c in cands] == [
        ("c1", "contact_card", ["물류팀 리드"]),
        ("a:s1:ppl-01", "people_answer", ["시설 담당자"]),
    ]
    assert cands[0].text == f"재고 동기화 연락처 {CONTACT_BODY}"


def test_candidates_capped(store: KnowledgeStore, sessions: SessionStore) -> None:
    store.replace_session_cards(
        "s1",
        [_card("s1", "contact", f"연락처 {i}", CONTACT_BODY) for i in range(ROUTE_CANDIDATES_MAX + 3)],
        NOW,
    )
    cands = QuestionRouter(FakeLLM(), store, sessions).candidates(LEAVER, ROLE)
    assert len(cands) == ROUTE_CANDIDATES_MAX


def test_route_contact_card(store: KnowledgeStore, sessions: SessionStore) -> None:
    store.replace_session_cards("s1", [_card("s1", "contact", "재고 동기화 연락처", CONTACT_BODY)], NOW)
    route = QuestionRouter(FakeLLM(), store, sessions).route(Q_CONTACT, LEAVER, ROLE)
    assert route is not None
    assert route.basis == "contact_card"
    assert route.ask_role == "물류팀 리드"
    assert route.ask_role in CONTACT_BODY
    assert route.card_ids == ["c1"] and route.question_ids == []
    assert route.reason == "'물류팀 리드' 이 이 주제의 연락처로 기록되어 있습니다."


def test_route_people_answer(store: KnowledgeStore, sessions: SessionStore) -> None:
    _add_session(sessions, "s1", True, [("ppl-03", "people", PEOPLE_ANSWER)])
    route = QuestionRouter(FakeLLM(), store, sessions).route(Q_PEOPLE, LEAVER, ROLE)
    assert route is not None
    assert route.basis == "people_answer"
    assert route.ask_role == "시설 담당자"
    assert route.card_ids == [] and route.question_ids == ["ppl-03"]


def test_route_no_candidates_none(store: KnowledgeStore, sessions: SessionStore) -> None:
    assert QuestionRouter(FakeLLM(), store, sessions).route(Q_CONTACT, LEAVER, ROLE) is None


def test_route_unrelated_question_none(store: KnowledgeStore, sessions: SessionStore) -> None:
    store.replace_session_cards("s1", [_card("s1", "contact", "재고 동기화 연락처", CONTACT_BODY)], NOW)
    assert QuestionRouter(FakeLLM(), store, sessions).route(Q_UNRELATED, LEAVER, ROLE) is None


class OutsideRoleLLM(FakeLLM):
    def route_question(self, question: str, candidates: list[RouteCandidate]) -> RouteDraft | None:
        return RouteDraft(ref=candidates[0].ref, role="외부 고문")


class UnknownRefLLM(FakeLLM):
    def route_question(self, question: str, candidates: list[RouteCandidate]) -> RouteDraft | None:
        return RouteDraft(ref="c999", role="물류팀 리드")


class NullLLM(FakeLLM):
    def route_question(self, question: str, candidates: list[RouteCandidate]) -> RouteDraft | None:
        return None


class DownLLM(FakeLLM):
    def route_question(self, question: str, candidates: list[RouteCandidate]) -> RouteDraft | None:
        raise ProviderError("down")


@pytest.mark.parametrize("llm", [OutsideRoleLLM(), UnknownRefLLM(), DownLLM()])
def test_route_invalid_draft_falls_back_to_fake(
    store: KnowledgeStore, sessions: SessionStore, llm: FakeLLM
) -> None:
    store.replace_session_cards("s1", [_card("s1", "contact", "재고 동기화 연락처", CONTACT_BODY)], NOW)
    route = QuestionRouter(llm, store, sessions).route(Q_CONTACT, LEAVER, ROLE)
    assert route is not None
    assert route.ask_role == "물류팀 리드" and route.card_ids == ["c1"]


def test_route_invalid_draft_and_fake_none(store: KnowledgeStore, sessions: SessionStore) -> None:
    store.replace_session_cards("s1", [_card("s1", "contact", "재고 동기화 연락처", CONTACT_BODY)], NOW)
    assert QuestionRouter(OutsideRoleLLM(), store, sessions).route(Q_UNRELATED, LEAVER, ROLE) is None


def test_route_llm_none_is_none(store: KnowledgeStore, sessions: SessionStore) -> None:
    store.replace_session_cards("s1", [_card("s1", "contact", "재고 동기화 연락처", CONTACT_BODY)], NOW)
    assert QuestionRouter(NullLLM(), store, sessions).route(Q_CONTACT, LEAVER, ROLE) is None
