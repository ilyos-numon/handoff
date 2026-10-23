import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from handoff.contracts import (
    FAKE_COMPOSE_PREFIX,
    AskRequest,
    Citation,
    ClaimVerdict,
    Evidence,
    KBAnswer,
    NewCard,
    VerifyOutcome,
    split_sentences,
)
from handoff.kb import NOT_FOUND_MESSAGE, KBEntry, KnowledgeBase
from handoff.knowledge_store import KnowledgeStore
from handoff.llm_tasks.verify import VERIFY_SYSTEM, fake_verify_claims, qwen_verify_claims
from handoff.providers import FakeEmbedder, FakeLLM, ProviderError
from handoff.routes_successor import router
from handoff.routing import QuestionRouter
from handoff.store import SessionStore
from handoff.verify import SUCCESSOR_REASON, ClaimVerifier, SuccessorService, guess_axis

ANSWER_MAIN = "금요일 배포는 피합니다. 작년 3월 금요일 저녁 배포 후 정산 배치가 주말 내내 실패했습니다."
QUERY_HIT = "금요일에 배포하면 왜 안 되나요?"
QUERY_MISS = "사무실 주차 등록은 어떻게 해요?"
MIN_SCORE = 0.25
NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)


def _citation(excerpt: str = ANSWER_MAIN) -> Citation:
    return Citation(
        session_id="sess-1", question_id="inc-01", axis="incidents", excerpt=excerpt, score=0.9
    )


def _entries() -> list[KBEntry]:
    return [
        KBEntry(
            session_id="sess-1",
            question_id="inc-01",
            axis="incidents",
            question="가장 기억에 남는 장애는?",
            answer=ANSWER_MAIN,
            created_at=NOW,
        )
    ]


class FailingVerifyLLM(FakeLLM):
    def verify_claims(
        self, question: str, claims: list[str], citations: list[Citation]
    ) -> list[ClaimVerdict]:
        raise ProviderError("down")


class ShortVerifyLLM(FakeLLM):
    def verify_claims(
        self, question: str, claims: list[str], citations: list[Citation]
    ) -> list[ClaimVerdict]:
        return [ClaimVerdict(claim=claims[0], supported=True, answers_question=True)]


# ---- fake 주장 판정
def test_fake_verify_regression_claims_supported_and_answering() -> None:
    claims = [f"{FAKE_COMPOSE_PREFIX} 금요일 배포는 피합니다.", "작년 3월 금요일 저녁 배포 후 정산 배치가 주말 내내 실패했습니다."]
    verdicts = fake_verify_claims(QUERY_HIT, claims, [_citation()])
    assert [v.supported for v in verdicts] == [True, True]
    assert [v.answers_question for v in verdicts] == [True, True]
    assert [v.citation_index for v in verdicts] == [1, 1]
    assert [v.claim for v in verdicts] == claims


def test_fake_verify_unsupported_claim() -> None:
    verdicts = fake_verify_claims(
        QUERY_HIT, ["금요일 배포는 결제팀 리드에게 승인받습니다."], [_citation()]
    )
    assert verdicts[0].supported is False
    assert verdicts[0].citation_index is None


def test_fake_verify_citation_index_first_match() -> None:
    other = _citation("메시지 큐 지연은 소비자 수를 늘려 해결합니다.")
    verdicts = fake_verify_claims(QUERY_HIT, ["금요일 배포는 피합니다."], [other, _citation()])
    assert verdicts[0].citation_index == 2


def test_fake_verify_stopwords_do_not_count() -> None:
    verdicts = fake_verify_claims("어떻게 하나요?", ["어떻게 하나요 라고 적었습니다."], [])
    assert verdicts[0].answers_question is False


# ---- ClaimVerifier
def test_verifier_keeps_regression_answer_with_fake(tmp_path: Path) -> None:
    llm = FakeLLM()
    kb = KnowledgeBase(
        tmp_path / "qdrant", FakeEmbedder(), llm, MIN_SCORE, verifier=ClaimVerifier(llm)
    )
    try:
        kb.add(_entries())
        result = kb.query(QUERY_HIT)
    finally:
        kb.close()
    assert result.found is True
    assert result.claims_removed == 0
    assert "금요일 배포는 피합니다." in result.answer
    assert "정산 배치가 주말 내내 실패했습니다." in result.answer
    assert result.citations[0].question_id == "inc-01"


def test_verifier_removes_unrelated_claim() -> None:
    answer = f"{ANSWER_MAIN} 주차 등록은 총무 담당에게 메일로 신청합니다."
    outcome = ClaimVerifier(FakeLLM()).verify(QUERY_HIT, answer, [_citation()])
    assert outcome.answer == ANSWER_MAIN
    assert outcome.kept == 2
    assert outcome.removed == 1
    assert len(outcome.claims) == 3
    assert outcome.claims[2].supported is False


def test_verifier_all_removed_returns_none() -> None:
    # 근거에 있는 문장이지만 질문(주차 등록)에 답하지 않는다 → 전부 제거
    answer = f"{FAKE_COMPOSE_PREFIX} {ANSWER_MAIN}"
    outcome = ClaimVerifier(FakeLLM()).verify(QUERY_MISS, answer, [_citation()])
    assert outcome.answer is None
    assert outcome.removed == 2
    assert outcome.kept == 0


def test_verifier_call_returns_tuple() -> None:
    verifier = ClaimVerifier(FakeLLM())
    assert verifier(QUERY_HIT, ANSWER_MAIN, [_citation()]) == (ANSWER_MAIN, 0)
    assert verifier(QUERY_MISS, ANSWER_MAIN, [_citation()]) == (None, 2)


def test_verifier_provider_error_returns_none() -> None:
    outcome = ClaimVerifier(FailingVerifyLLM()).verify(QUERY_HIT, ANSWER_MAIN, [_citation()])
    assert outcome.answer is None
    assert outcome.removed == 2
    assert all(not v.supported and not v.answers_question for v in outcome.claims)


def test_verifier_length_mismatch_returns_none() -> None:
    outcome = ClaimVerifier(ShortVerifyLLM()).verify(QUERY_HIT, ANSWER_MAIN, [_citation()])
    assert outcome.answer is None
    assert outcome.removed == 2


def test_verifier_empty_answer() -> None:
    outcome = ClaimVerifier(FakeLLM()).verify(QUERY_HIT, "  \n ", [_citation()])
    assert outcome == VerifyOutcome(answer=None, claims=[], kept=0, removed=0)


def test_verifier_with_kb_not_found_on_all_removed(tmp_path: Path) -> None:
    llm = FakeLLM()
    kb = KnowledgeBase(
        tmp_path / "qdrant", FakeEmbedder(), llm, MIN_SCORE, verifier=ClaimVerifier(FailingVerifyLLM())
    )
    try:
        kb.add(_entries())
        result = kb.query(QUERY_HIT)
    finally:
        kb.close()
    assert result.found is False
    assert result.answer == NOT_FOUND_MESSAGE
    assert result.claims_removed == 2


# ---- qwen 주장 판정 (가짜 chat)
class FakeChat:
    def __init__(self, content: str | Exception) -> None:
        self.content = content
        self.calls: list[tuple[str, str, int, bool]] = []

    def __call__(self, system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        self.calls.append((system, user, max_tokens, json_mode))
        if isinstance(self.content, Exception):
            raise self.content
        return self.content


def _verdicts_json(*items: tuple[bool, bool, int | None]) -> str:
    return json.dumps(
        {
            "verdicts": [
                {"index": i, "supported": s, "answers_question": a, "citation": c}
                for i, (s, a, c) in enumerate(items, start=1)
            ]
        }
    )


def test_qwen_verify_parses_json() -> None:
    chat = FakeChat(_verdicts_json((True, True, 1), (True, False, 7)))
    claims = ["금요일 배포는 피합니다.", "작년 3월 금요일 저녁 배포 후 정산 배치가 주말 내내 실패했습니다."]
    verdicts = qwen_verify_claims(chat, QUERY_HIT, claims, [_citation()])
    assert [v.claim for v in verdicts] == claims
    assert [(v.supported, v.answers_question) for v in verdicts] == [(True, True), (True, False)]
    assert [v.citation_index for v in verdicts] == [1, None]  # 범위 밖 근거 번호는 버린다
    system, user, _, json_mode = chat.calls[0]
    assert system == VERIFY_SYSTEM
    assert json_mode is True
    assert QUERY_HIT in user and "[근거 1]" in user and "[주장 2]" in user


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"verdicts": "x"}',
        _verdicts_json((True, True, 1)),  # 길이 불일치
        (
            '{"verdicts": [{"supported": "yes", "answers_question": true}, '
            '{"supported": true, "answers_question": true}]}'
        ),
    ],
)
def test_qwen_verify_parse_failure_all_false(content: str) -> None:
    claims = ["금요일 배포는 피합니다.", "정산 배치가 실패했습니다."]
    verdicts = qwen_verify_claims(FakeChat(content), QUERY_HIT, claims, [_citation()])
    assert [v.claim for v in verdicts] == claims
    assert all(not v.supported and not v.answers_question for v in verdicts)


def test_qwen_verify_provider_error_propagates() -> None:
    with pytest.raises(ProviderError):
        qwen_verify_claims(FakeChat(ProviderError("down")), QUERY_HIT, ["a b."], [_citation()])


def test_qwen_prompt_rules_for_related_but_unanswered() -> None:
    assert "answers_question" in VERIFY_SYSTEM
    assert "관련 주제" in VERIFY_SYSTEM
    assert "절차" in VERIFY_SYSTEM and "연락처" in VERIFY_SYSTEM
    assert "지어내지 않습니다" in VERIFY_SYSTEM
    assert "역할명" in VERIFY_SYSTEM
    assert "JSON 만 출력" in VERIFY_SYSTEM


class QwenLikeLLM(FakeLLM):
    def __init__(self, chat: FakeChat) -> None:
        self._chat = chat

    def verify_claims(
        self, question: str, claims: list[str], citations: list[Citation]
    ) -> list[ClaimVerdict]:
        return qwen_verify_claims(self._chat, question, claims, citations)


def test_qwen_q16_style_related_but_not_answering_is_none() -> None:
    # q16: 관련 주제(정산 배치 실패)지만 복구 절차를 근거가 적지 않음
    chat = FakeChat(_verdicts_json((True, False, 1), (True, False, 1)))
    outcome = ClaimVerifier(QwenLikeLLM(chat)).verify(
        "정산 배치가 실패하면 손으로 어떻게 복구하나요?", ANSWER_MAIN, [_citation()]
    )
    assert outcome.answer is None
    assert outcome.removed == 2


Q16 = "정산 배치가 실패하면 손으로 어떻게 복구하나요?"
Q16_ANSWER = (
    "근거에 따르면 정산 배치 실패 시 혼자 다시 돌리지 말고 정산 담당자에게 먼저 알려야 합니다. "
    "손으로 직접 복구하는 절차는 명시되어 있지 않습니다."
)
Q16_EVIDENCE = (
    "매출 집계 배치는 다시 돌려도 안전합니다. "
    "정산 배치 결과가 이상하면 혼자 다시 돌리지 말고 정산 담당자에게 먼저 알리세요."
)
Q17 = "결제 대행사 쪽에 장애가 나면 누구한테 연락해요?"
Q17_ANSWER = (
    "결제 대행사 장애 발생 시 결제팀 리드에게 먼저 문의합니다. "
    "팀 메신저의 결제 개발 채널에 질문을 올리고 리드를 언급하면 됩니다."
)
Q17_EVIDENCE = (
    "막히면 결제팀 리드에게 먼저 묻습니다. 팀 메신저의 결제 개발 채널에 질문을 올리고 "
    "리드를 언급하면 보통 한 시간 안에 답이 옵니다."
)


def test_qwen_q16_live_shape_disclaimer_claim_is_none() -> None:
    # S15 실측 qwen 판정 그대로: "명시되어 있지 않습니다" 주장을 supported+answers 로, 근거 null
    chat = FakeChat(_verdicts_json((True, False, 1), (True, True, None)))
    outcome = ClaimVerifier(QwenLikeLLM(chat)).verify(Q16, Q16_ANSWER, [_citation(Q16_EVIDENCE)])
    assert outcome.answer is None
    assert outcome.removed == 2


def test_qwen_disclaimer_claim_forced_unsupported_even_with_citation() -> None:
    chat = FakeChat(_verdicts_json((True, False, 1), (True, True, 1)))
    verdicts = qwen_verify_claims(
        chat, Q16, split_sentences(Q16_ANSWER), [_citation(Q16_EVIDENCE)]
    )
    assert (verdicts[1].supported, verdicts[1].answers_question) == (False, False)
    assert (verdicts[0].supported, verdicts[0].answers_question) == (True, False)


@pytest.mark.parametrize(
    "claim",
    [
        "복구 절차는 근거에 나와 있지 않습니다.",
        "연락처는 언급되지 않았습니다.",
        "구체적인 방법은 명시되지 않았습니다.",
        "해당 내용은 근거에 없습니다.",
        "자료에는 관련 절차가 없습니다.",
    ],
)
def test_qwen_disclaimer_variants_never_answer(claim: str) -> None:
    chat = FakeChat(_verdicts_json((True, True, 1)))
    verdicts = qwen_verify_claims(chat, Q16, [claim], [_citation(Q16_EVIDENCE)])
    assert (verdicts[0].supported, verdicts[0].answers_question) == (False, False)


def test_qwen_answering_claim_needs_citation() -> None:
    chat = FakeChat(_verdicts_json((True, True, None), (True, True, 9)))
    verdicts = qwen_verify_claims(chat, QUERY_HIT, ["금요일 배포는 피합니다.", "정산 배치."], [_citation()])
    assert [v.answers_question for v in verdicts] == [False, False]
    assert [v.supported for v in verdicts] == [True, True]


def test_qwen_q17_shape_condition_not_in_evidence_is_none() -> None:
    # q17: 근거는 일반 상황("막히면")만 말하는데 주장이 질문의 상황(대행사 장애)을 붙임
    chat = FakeChat(_verdicts_json((False, False, None), (True, False, 1)))
    outcome = ClaimVerifier(QwenLikeLLM(chat)).verify(Q17, Q17_ANSWER, [_citation(Q17_EVIDENCE)])
    assert outcome.answer is None
    assert outcome.removed == 2


def test_qwen_prompt_rules_for_disclaimer_and_borrowed_condition() -> None:
    assert "명시되어 있지 않" in VERIFY_SYSTEM
    assert "일반적인 상황" in VERIFY_SYSTEM and "특정 상황" in VERIFY_SYSTEM
    assert "citation 이 null 이면 answers_question 은 false" in VERIFY_SYSTEM


def _situation_json(situation: object, *items: tuple[bool, bool, object, int | None]) -> str:
    return json.dumps(
        {
            "situation": situation,
            "verdicts": [
                {
                    "index": i,
                    "supported": s,
                    "answers_question": a,
                    "situation_covered": cov,
                    "citation": c,
                }
                for i, (s, a, cov, c) in enumerate(items, start=1)
            ],
        }
    )


def test_qwen_situation_not_covered_blocks_answer() -> None:
    # q17 형태: qwen 이 supported·answers 는 true 로 냈지만 근거가 질문 상황을 다루지 않음
    chat = FakeChat(
        _situation_json("결제 대행사 장애가 났을 때 연락 대상", (True, True, False, 1), (True, True, False, 1))
    )
    outcome = ClaimVerifier(QwenLikeLLM(chat)).verify(Q17, Q17_ANSWER, [_citation(Q17_EVIDENCE)])
    assert outcome.answer is None
    assert outcome.removed == 2
    assert all(not v.answers_question for v in outcome.claims)
    assert all(v.supported for v in outcome.claims)


def test_qwen_situation_covered_keeps_answer() -> None:
    chat = FakeChat(_situation_json("금요일 배포를 피하는 이유", (True, True, True, 1), (True, True, True, 1)))
    outcome = ClaimVerifier(QwenLikeLLM(chat)).verify(QUERY_HIT, ANSWER_MAIN, [_citation()])
    assert outcome.answer == ANSWER_MAIN
    assert outcome.removed == 0


@pytest.mark.parametrize(
    "content",
    [
        _situation_json("상황", (True, True, "yes", 1), (True, True, True, 1)),
        _situation_json("상황", (True, True, None, 1), (True, True, True, 1)),
        _situation_json(["상황"], (True, True, True, 1), (True, True, True, 1)),
        '{"situation": "상황", "verdicts": [{"supported": true}]}',
        '{"situation": "상황"}',
    ],
)
def test_qwen_broken_situation_response_all_false(content: str) -> None:
    claims = split_sentences(ANSWER_MAIN)
    verdicts = qwen_verify_claims(FakeChat(content), QUERY_HIT, claims, [_citation()])
    assert [v.claim for v in verdicts] == claims
    assert all(not v.supported and not v.answers_question for v in verdicts)


def test_qwen_prompt_asks_situation_fields() -> None:
    assert '"situation"' in VERIFY_SYSTEM
    assert '"situation_covered"' in VERIFY_SYSTEM


def test_qwen_parse_failure_falls_back_to_none() -> None:
    outcome = ClaimVerifier(QwenLikeLLM(FakeChat("oops"))).verify(
        QUERY_HIT, ANSWER_MAIN, [_citation()]
    )
    assert outcome.answer is None
    assert outcome.removed == 2


# ---- guess_axis
@pytest.mark.parametrize(
    ("text", "axis"),
    [
        ("결제 대행사 쪽에 장애가 나면 누구한테 연락해요?", "people"),
        ("정산 배치가 실패하면 손으로 어떻게 복구하나요?", "incidents"),
        ("휴가 신청은 어떤 절차로 하나요?", "routines"),
        ("사무실 주차 등록은 어떻게 해요?", "systems"),
    ],
)
def test_guess_axis(text: str, axis: str) -> None:
    assert guess_axis(text) == axis


# ---- SuccessorService
@pytest.fixture
def store() -> KnowledgeStore:
    return KnowledgeStore(*SessionStore(":memory:").shared())


class StubKB:
    def __init__(self, answer: KBAnswer | Exception) -> None:
        self.answer = answer
        self.leavers: list[str | None] = []

    def query(self, question: str, top_k: int = 3, leaver: str | None = None) -> KBAnswer:
        self.leavers.append(leaver)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


NOT_FOUND = KBAnswer(found=False, answer=NOT_FOUND_MESSAGE, citations=[])
FOUND = KBAnswer(found=True, answer=ANSWER_MAIN, citations=[_citation()])


def test_service_queues_unanswered(store: KnowledgeStore) -> None:
    svc = SuccessorService(store, clock=lambda: NOW)
    res = svc.ask(StubKB(NOT_FOUND), AskRequest(question=QUERY_MISS, leaver="loop"))  # type: ignore[arg-type]
    assert res.answer.found is False
    assert res.queued is not None
    assert res.queued.source == "successor"
    assert res.queued.status == "pending"
    assert res.queued.axis == "systems"
    assert res.queued.reason == SUCCESSOR_REASON
    assert res.queued.ref_id is None
    assert res.queued.role == "backend_developer"
    assert res.queued.created_at == NOW
    assert svc.list("loop") == [res.queued]


def test_service_found_not_queued(store: KnowledgeStore) -> None:
    svc = SuccessorService(store)
    res = svc.ask(StubKB(FOUND), AskRequest(question=QUERY_HIT, leaver="loop"))  # type: ignore[arg-type]
    assert res.queued is None
    assert svc.list("loop") == []


def test_service_ask_scopes_query_to_leaver(store: KnowledgeStore) -> None:
    kb = StubKB(FOUND)
    SuccessorService(store).ask(kb, AskRequest(question=QUERY_HIT, leaver="kim"))  # type: ignore[arg-type]
    assert kb.leavers == ["kim"]


# ---- 라우터
@pytest.fixture
def empty_kb(tmp_path: Path) -> Iterator[KnowledgeBase]:
    kb = KnowledgeBase(tmp_path / "qdrant", FakeEmbedder(), FakeLLM(), MIN_SCORE)
    yield kb
    kb.close()


def _client(store: KnowledgeStore, kb: object) -> TestClient:
    app = FastAPI()
    app.state.knowledge = SimpleNamespace(successor=SuccessorService(store, clock=lambda: NOW))
    app.state.services = SimpleNamespace(kb=kb)
    app.include_router(router)
    return TestClient(app)


def test_route_ask_queues_and_dedupes(store: KnowledgeStore, empty_kb: KnowledgeBase) -> None:
    client = _client(store, empty_kb)
    body = {"question": QUERY_MISS, "leaver": "loop"}
    first = client.post("/kb/ask", json=body)
    second = client.post("/kb/ask", json=body)
    assert first.status_code == 200 and second.status_code == 200
    a, b = first.json(), second.json()
    assert a["answer"]["found"] is False
    assert a["queued"]["source"] == "successor"
    assert a["queued"]["status"] == "pending"
    assert a["queued"]["id"] == b["queued"]["id"]
    listed = client.get("/leavers/loop/successor-questions")
    assert listed.status_code == 200
    assert [q["id"] for q in listed.json()] == [a["queued"]["id"]]


def test_route_ask_found_queued_null(store: KnowledgeStore, tmp_path: Path) -> None:
    llm = FakeLLM()
    kb = KnowledgeBase(
        tmp_path / "qdrant", FakeEmbedder(), llm, MIN_SCORE, verifier=ClaimVerifier(llm)
    )
    try:
        kb.add(_entries(), "loop")
        res = _client(store, kb).post("/kb/ask", json={"question": QUERY_HIT, "leaver": "loop"})
    finally:
        kb.close()
    assert res.status_code == 200
    assert res.json()["answer"]["found"] is True
    assert res.json()["queued"] is None


def test_route_ask_other_leaver_answer_not_used(store: KnowledgeStore, tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path / "qdrant", FakeEmbedder(), FakeLLM(), MIN_SCORE)
    try:
        kb.add(_entries(), "other")
        res = _client(store, kb).post("/kb/ask", json={"question": QUERY_HIT, "leaver": "loop"})
    finally:
        kb.close()
    assert res.status_code == 200
    assert res.json()["answer"]["found"] is False
    assert res.json()["queued"] is not None


def test_route_ask_provider_error_503(store: KnowledgeStore) -> None:
    res = _client(store, StubKB(ProviderError("down"))).post(
        "/kb/ask", json={"question": QUERY_MISS, "leaver": "loop"}
    )
    assert res.status_code == 503
    assert res.json()["detail"] == "provider unavailable"


@pytest.mark.parametrize(
    "body",
    [{"question": "  ", "leaver": "loop"}, {"question": "q?", "leaver": "Bad Name"}, {"leaver": "loop"}],
)
def test_route_ask_422(store: KnowledgeStore, empty_kb: KnowledgeBase, body: dict[str, str]) -> None:
    assert _client(store, empty_kb).post("/kb/ask", json=body).status_code == 422


def test_route_successor_questions_bad_leaver_422(
    store: KnowledgeStore, empty_kb: KnowledgeBase
) -> None:
    assert _client(store, empty_kb).get("/leavers/Bad!/successor-questions").status_code == 422


def test_route_successor_questions_only_successor_source(
    store: KnowledgeStore, empty_kb: KnowledgeBase
) -> None:
    store.enqueue("loop", "backend_developer", "gap", "systems", "다른 질문?", "gap", None, NOW)
    assert _client(store, empty_kb).get("/leavers/loop/successor-questions").json() == []


# ---- S7: 라우팅·근거 확신도
def _contact_card() -> NewCard:
    body = "재고 동기화 문제는 물류팀 리드에게 먼저 묻습니다."
    return NewCard(
        leaver="loop",
        role="backend_developer",
        session_id="sess-1",
        type="contact",
        title="재고 동기화 연락처",
        body=body,
        entities=[],
        confidence=0.8,
        evidence=[Evidence(session_id="sess-1", question_id="ppl-01", session_number=1, excerpt=body)],
    )


def _router(store: KnowledgeStore, llm: FakeLLM | None = None) -> QuestionRouter:
    sessions = SessionStore(":memory:")
    return QuestionRouter(llm or FakeLLM(), store, sessions)


def test_service_found_certain_without_route(store: KnowledgeStore) -> None:
    svc = SuccessorService(store, router=_router(store))
    res = svc.ask(StubKB(FOUND), AskRequest(question=QUERY_HIT, leaver="loop"))  # type: ignore[arg-type]
    assert res.route is None
    assert res.certainty == "certain"
    assert res.queued is None


def test_service_found_uncertain_excerpt(store: KnowledgeStore) -> None:
    found = KBAnswer(
        found=True,
        answer=ANSWER_MAIN,
        citations=[_citation(), _citation("아마 정산 배치가 먼저 실패했을 겁니다.")],
    )
    res = SuccessorService(store).ask(StubKB(found), AskRequest(question=QUERY_HIT, leaver="loop"))  # type: ignore[arg-type]
    assert res.certainty == "uncertain"
    assert res.route is None


def test_service_not_found_routes_to_role(store: KnowledgeStore) -> None:
    store.replace_session_cards("sess-1", [_contact_card()], NOW)
    svc = SuccessorService(store, clock=lambda: NOW, router=_router(store))
    req = AskRequest(question="재고 동기화 문제는 누구에게 묻나요?", leaver="loop")  # type: ignore[arg-type]
    res = svc.ask(StubKB(NOT_FOUND), req)
    assert res.queued is not None
    assert res.certainty is None
    assert res.route is not None
    assert res.route.ask_role == "물류팀 리드"
    assert res.route.basis == "contact_card"
    assert res.route.card_ids == ["c1"]


def test_service_not_found_no_candidates_route_none(store: KnowledgeStore) -> None:
    svc = SuccessorService(store, router=_router(store))
    res = svc.ask(StubKB(NOT_FOUND), AskRequest(question=QUERY_MISS, leaver="loop"))  # type: ignore[arg-type]
    assert res.queued is not None
    assert res.route is None and res.certainty is None


def test_service_router_provider_error_route_none(store: KnowledgeStore) -> None:
    class BrokenRouter:
        def route(self, question: str, leaver: str, role: str) -> None:
            raise ProviderError("down")

    svc = SuccessorService(store, router=BrokenRouter())  # type: ignore[arg-type]
    res = svc.ask(StubKB(NOT_FOUND), AskRequest(question=QUERY_MISS, leaver="loop"))  # type: ignore[arg-type]
    assert res.queued is not None
    assert res.route is None


def test_service_without_router_keeps_old_behavior(store: KnowledgeStore) -> None:
    store.replace_session_cards("sess-1", [_contact_card()], NOW)
    svc = SuccessorService(store, lambda: NOW)
    req = AskRequest(question="재고 동기화 문제는 누구에게 묻나요?", leaver="loop")  # type: ignore[arg-type]
    res = svc.ask(StubKB(NOT_FOUND), req)
    assert res.queued is not None and res.queued.created_at == NOW
    assert res.route is None and res.certainty is None
