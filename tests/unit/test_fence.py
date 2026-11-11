import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from handoff.contracts import (
    FENCE_QUEUE_TEMPLATE,
    FENCE_REASON,
    PROVIDER_UNAVAILABLE,
    Citation,
    ClaimVerdict,
    Evidence,
    FenceDraft,
    FencePoint,
    FenceRequest,
    InvalidationCondition,
    KnowledgeCard,
    NewCard,
)
from handoff.fence import FenceService
from handoff.knowledge_store import KnowledgeStore
from handoff.llm_tasks.fence import (
    EXPLAIN_FENCE_SYSTEM,
    fake_explain_fence,
    qwen_explain_fence,
)
from handoff.providers import FakeLLM, ProviderError
from handoff.routes_fence import router
from handoff.signals import FENCE_POINTS_MAX, FENCE_TOP_CARDS
from handoff.store import SessionStore

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
LEAVER = "demo"
SUBJECT = "금요일 배포 금지"
DEPLOY_BODY = (
    "정산 배치가 주말에 돌기 때문에 금요일에는 배포하지 않습니다. "
    "금요일 배포 후 배치가 실패하면 주말 내내 정산이 밀립니다."
)
SCHEDULE = InvalidationCondition(
    event="schedule_changed", condition="작업 일정이나 주기가 바뀌면 이 내용이 틀려질 수 있습니다."
)
POLICY = InvalidationCondition(
    event="policy_changed", condition="승인·권한·보관 규정이 바뀌면 이 내용이 틀려질 수 있습니다."
)


def _new(
    title: str,
    body: str,
    type: Any = "decision",
    session_id: str = "sess-1",
    question_id: str = "sys-01",
    **extra: Any,
) -> NewCard:
    return NewCard(
        leaver=LEAVER,
        role="backend_developer",
        session_id=session_id,
        type=type,
        title=title,
        body=body,
        entities=extra.pop("entities", []),
        confidence=0.8,
        evidence=[
            Evidence(session_id=session_id, question_id=question_id, session_number=1, excerpt=body)
        ],
        **extra,
    )


def _card(id: str, title: str, body: str, type: Any = "procedure") -> KnowledgeCard:
    new = _new(title, body, type)
    return KnowledgeCard(**new.model_dump(), id=id, created_at=NOW)


@pytest.fixture
def store() -> KnowledgeStore:
    return KnowledgeStore(*SessionStore(":memory:").shared())


def _service(store: KnowledgeStore, llm: Any = None) -> FenceService:
    return FenceService(llm or FakeLLM(), store, clock=lambda: NOW)


def _seed(store: KnowledgeStore, *cards: NewCard, session_id: str = "sess-1") -> list[KnowledgeCard]:
    return store.replace_session_cards(session_id, list(cards), NOW)


class DraftLLM(FakeLLM):
    def __init__(self, draft: FenceDraft) -> None:
        self.draft = draft

    def explain_fence(self, subject: str, cards: list[KnowledgeCard]) -> FenceDraft:
        return self.draft


class RecordingLLM(FakeLLM):
    def __init__(self) -> None:
        self.seen: list[list[str]] = []

    def explain_fence(self, subject: str, cards: list[KnowledgeCard]) -> FenceDraft:
        self.seen.append([c.id for c in cards])
        return super().explain_fence(subject, cards)


class VerifyDownLLM(FakeLLM):
    def verify_claims(
        self, question: str, claims: list[str], citations: list[Citation]
    ) -> list[ClaimVerdict]:
        raise ProviderError("down")


class VerifyShortLLM(FakeLLM):
    def verify_claims(
        self, question: str, claims: list[str], citations: list[Citation]
    ) -> list[ClaimVerdict]:
        return []


class VerifyNoAnswerLLM(FakeLLM):
    def verify_claims(
        self, question: str, claims: list[str], citations: list[Citation]
    ) -> list[ClaimVerdict]:
        return [ClaimVerdict(claim=c, supported=True, answers_question=False) for c in claims]


class ExplainDownLLM(FakeLLM):
    def explain_fence(self, subject: str, cards: list[KnowledgeCard]) -> FenceDraft:
        raise ProviderError("down")


# ---- fake 설명 규칙
def test_fake_explain_classifies_reason_risk_and_drops_other() -> None:
    proc = _card(
        "c1",
        "백업 절차",
        "새벽에 백업을 돌리는 이유는 트래픽이 적어서입니다. "
        "백업을 놓치면 복구 시점이 하루 밀립니다. 결과는 메일로 옵니다.",
    )
    pit = _card("c2", "디스크 경보", "디스크가 차면 알림이 옵니다.", type="pitfall")
    draft = fake_explain_fence("백업", [proc, pit])
    assert [p.text for p in draft.reasons] == ["새벽에 백업을 돌리는 이유는 트래픽이 적어서입니다."]
    assert [p.text for p in draft.risks] == [
        "백업을 놓치면 복구 시점이 하루 밀립니다.",
        "디스크가 차면 알림이 옵니다.",
    ]
    assert [p.card_ids for p in draft.reasons + draft.risks] == [["c1"], ["c1"], ["c2"]]


def test_fake_explain_decision_card_all_reasons_dedup_and_cap() -> None:
    body = "가. 나. 다. 라."
    first = _card("c1", "결정", body, type="decision")
    again = _card("c2", "결정 반복", "가. 마.", type="decision")
    draft = fake_explain_fence("결정", [first, again])
    assert [p.text for p in draft.reasons] == ["가.", "나.", "다."][:FENCE_POINTS_MAX]
    assert draft.risks == []


# ---- qwen 설명
def test_qwen_explain_parses_json_and_uses_system_prompt() -> None:
    calls: list[tuple[str, str, int, bool]] = []
    payload = {
        "reasons": [{"text": "정산 배치가 주말에 돌기 때문입니다.", "card_ids": ["c1"]}],
        "risks": [{"text": "배치가 실패하면 정산이 밀립니다.", "card_ids": ["c9"]}],
    }

    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        calls.append((system, user, max_tokens, json_mode))
        return json.dumps(payload, ensure_ascii=False)

    card = _card("c1", SUBJECT, DEPLOY_BODY, type="decision")
    draft = qwen_explain_fence(chat, SUBJECT, [card])
    assert draft.reasons == [FencePoint(text="정산 배치가 주말에 돌기 때문입니다.", card_ids=["c1"])]
    assert draft.risks[0].card_ids == ["c9"]  # 후보 밖 id 는 서비스가 버린다
    system, user, _, json_mode = calls[0]
    assert system == EXPLAIN_FENCE_SYSTEM and json_mode is True
    assert "지어내지 않습니다" in system and "역할명" in system
    assert f"[c1] (decision) {SUBJECT}: " in user and f"주제: {SUBJECT}" in user


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        json.dumps({"reasons": [{"text": "이유", "card_ids": []}]}),
        json.dumps({"reasons": [{"text": "", "card_ids": ["c1"]}]}),
        json.dumps({"reasons": "이유"}),
    ],
)
def test_qwen_explain_invalid_falls_back_to_fake(content: str) -> None:
    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        return content

    card = _card("c1", SUBJECT, DEPLOY_BODY, type="decision")
    assert qwen_explain_fence(chat, SUBJECT, [card]) == fake_explain_fence(SUBJECT, [card])


def test_qwen_explain_provider_error_propagates() -> None:
    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        raise ProviderError("down")

    with pytest.raises(ProviderError):
        qwen_explain_fence(chat, SUBJECT, [_card("c1", SUBJECT, DEPLOY_BODY)])


# ---- 서비스
def test_found_with_decision_card(store: KnowledgeStore) -> None:
    (card,) = _seed(
        store,
        _new(SUBJECT, DEPLOY_BODY, entities=["정산 배치"], invalidated_when=[SCHEDULE, POLICY]),
    )
    res = _service(store).ask(FenceRequest(leaver=LEAVER, subject=SUBJECT))
    assert res.found is True
    assert res.subject == SUBJECT
    assert res.queued is None
    assert res.reasons and all(p.card_ids == [card.id] for p in res.reasons + res.risks)
    assert [c.id for c in res.cards] == [card.id]
    assert res.invalidated_when == [SCHEDULE, POLICY]
    assert res.certainty == "certain"
    assert res.claims_removed == 0
    assert store.list_queue(LEAVER) == []


def test_no_cards_queues_successor_question(store: KnowledgeStore) -> None:
    res = _service(store).ask(FenceRequest(leaver=LEAVER, subject=SUBJECT))
    assert res.found is False
    assert (res.reasons, res.risks, res.cards, res.invalidated_when) == ([], [], [], [])
    assert res.certainty is None and res.claims_removed == 0
    assert res.queued is not None
    assert res.queued.source == "successor"
    assert res.queued.reason == FENCE_REASON
    assert res.queued.text == FENCE_QUEUE_TEMPLATE.format(subject=SUBJECT)
    assert res.queued.role == "backend_developer"
    assert res.queued.created_at == NOW


def test_unrelated_cards_are_a_miss(store: KnowledgeStore) -> None:
    _seed(store, _new(SUBJECT, DEPLOY_BODY))
    res = _service(store).ask(FenceRequest(leaver=LEAVER, subject="사무실 화분 물 주기 당번표"))
    assert res.found is False and res.queued is not None and res.cards == []


def test_same_subject_twice_same_queue_item(store: KnowledgeStore) -> None:
    svc = _service(store)
    first = svc.ask(FenceRequest(leaver=LEAVER, subject="방화벽 예외 규칙", role="ops"))
    second = svc.ask(FenceRequest(leaver=LEAVER, subject="방화벽 예외 규칙", role="ops"))
    assert first.queued is not None and second.queued is not None
    assert first.queued.id == second.queued.id
    assert first.queued.role == "ops"
    assert len(store.list_queue(LEAVER, source="successor")) == 1


def test_points_outside_top_cards_dropped(store: KnowledgeStore) -> None:
    (card,) = _seed(store, _new(SUBJECT, DEPLOY_BODY))
    good = "정산 배치가 주말에 돌기 때문에 금요일에는 배포하지 않습니다."
    draft = FenceDraft(
        reasons=[
            FencePoint(text=good, card_ids=[card.id]),
            FencePoint(text=good + " 추가", card_ids=[card.id, "c999"]),
        ],
        risks=[FencePoint(text="금요일 배포 후 배치가 실패하면 주말 내내 정산이 밀립니다.", card_ids=["c999"])],
    )
    res = _service(store, DraftLLM(draft)).ask(FenceRequest(leaver=LEAVER, subject=SUBJECT))
    assert res.found is True
    assert [p.text for p in res.reasons] == [good]
    assert res.risks == []
    assert res.claims_removed == 2


def test_unsupported_points_dropped_and_no_reason_means_miss(store: KnowledgeStore) -> None:
    (card,) = _seed(store, _new(SUBJECT, DEPLOY_BODY))
    draft = FenceDraft(
        reasons=[FencePoint(text="보안팀 리드가 금요일 배포를 영구 금지했습니다.", card_ids=[card.id])],
        risks=[FencePoint(text="금요일 배포 후 배치가 실패하면 주말 내내 정산이 밀립니다.", card_ids=[card.id])],
    )
    res = _service(store, DraftLLM(draft)).ask(FenceRequest(leaver=LEAVER, subject=SUBJECT))
    assert res.found is False
    assert res.reasons == []
    assert [p.text for p in res.risks] == ["금요일 배포 후 배치가 실패하면 주말 내내 정산이 밀립니다."]
    assert res.claims_removed == 1
    assert [c.id for c in res.cards] == [card.id]
    assert res.queued is not None and res.queued.reason == FENCE_REASON


def test_supported_reason_not_answering_is_removed(store: KnowledgeStore) -> None:
    _seed(store, _new(SUBJECT, DEPLOY_BODY))
    res = _service(store, VerifyNoAnswerLLM()).ask(FenceRequest(leaver=LEAVER, subject=SUBJECT))
    assert res.found is False
    assert res.reasons == []
    assert res.claims_removed == 2  # decision 카드 두 문장 모두 reason
    assert res.queued is not None


@pytest.mark.parametrize("llm_cls", [VerifyDownLLM, VerifyShortLLM])
def test_verify_failure_means_not_found(store: KnowledgeStore, llm_cls: type[FakeLLM]) -> None:
    _seed(store, _new(SUBJECT, DEPLOY_BODY))
    res = _service(store, llm_cls()).ask(FenceRequest(leaver=LEAVER, subject=SUBJECT))
    assert res.found is False
    assert res.reasons == [] and res.risks == [] and res.cards == []
    assert res.certainty is None
    assert res.claims_removed == 2
    assert res.queued is not None and res.queued.source == "successor"


def test_explain_provider_error_propagates(store: KnowledgeStore) -> None:
    _seed(store, _new(SUBJECT, DEPLOY_BODY))
    with pytest.raises(ProviderError):
        _service(store, ExplainDownLLM()).ask(FenceRequest(leaver=LEAVER, subject=SUBJECT))
    assert store.list_queue(LEAVER) == []


def test_uncertain_card_cited_marks_uncertain(store: KnowledgeStore) -> None:
    body = "아마 정산 배치가 주말에 돌기 때문에 금요일에는 배포하지 않습니다."
    _seed(store, _new(SUBJECT, body, certainty="uncertain", uncertain_cues=["아마"]))
    res = _service(store).ask(FenceRequest(leaver=LEAVER, subject=SUBJECT))
    assert res.found is True
    assert res.certainty == "uncertain"


def test_invalidated_when_union_dedup_and_cards_in_id_order(store: KnowledgeStore) -> None:
    first = _new(
        SUBJECT, DEPLOY_BODY, invalidated_when=[SCHEDULE], question_id="sys-01"
    )
    second = _new(
        "금요일 배포 금지 예외",
        "금요일 배포 금지는 긴급 장애 수정 때문에 예외를 둡니다.",
        question_id="sys-02",
        invalidated_when=[SCHEDULE, POLICY],
    )
    seeded = _seed(store, first, second)
    res = _service(store).ask(FenceRequest(leaver=LEAVER, subject=SUBJECT))
    assert res.found is True
    assert [c.id for c in res.cards] == [c.id for c in seeded]
    assert res.invalidated_when == [SCHEDULE, POLICY]


def test_top_cards_sorted_and_capped(store: KnowledgeStore) -> None:
    cards = [
        _new(f"{SUBJECT} {i}", DEPLOY_BODY, question_id=f"sys-{i:02d}") for i in range(1, 8)
    ]
    weak = _new("배포", "배포 일정은 화요일입니다.", question_id="sys-09")
    seeded = _seed(store, weak, *cards)
    llm = RecordingLLM()
    _service(store, llm).ask(FenceRequest(leaver=LEAVER, subject=SUBJECT))
    assert llm.seen == [[c.id for c in seeded[1 : 1 + FENCE_TOP_CARDS]]]


def test_other_leaver_cards_ignored(store: KnowledgeStore) -> None:
    other = _new(SUBJECT, DEPLOY_BODY)
    other.leaver = "someone"
    _seed(store, other)
    res = _service(store).ask(FenceRequest(leaver=LEAVER, subject=SUBJECT))
    assert res.found is False and res.queued is not None


# ---- 라우터 (최소 앱)
def _client(service: FenceService) -> TestClient:
    app = FastAPI()
    app.state.knowledge = SimpleNamespace(fence=service)
    app.include_router(router)
    return TestClient(app)


def test_route_fence_found(store: KnowledgeStore) -> None:
    _seed(store, _new(SUBJECT, DEPLOY_BODY))
    resp = _client(_service(store)).post("/kb/fence", json={"leaver": LEAVER, "subject": SUBJECT})
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is True and data["queued"] is None
    assert set(data) == {
        "subject",
        "found",
        "reasons",
        "risks",
        "cards",
        "invalidated_when",
        "certainty",
        "claims_removed",
        "queued",
    }


def test_route_fence_miss_queues(store: KnowledgeStore) -> None:
    resp = _client(_service(store)).post("/kb/fence", json={"leaver": LEAVER, "subject": SUBJECT})
    assert resp.status_code == 200
    assert resp.json()["found"] is False
    assert resp.json()["queued"]["reason"] == FENCE_REASON


def test_route_fence_provider_error_503(store: KnowledgeStore) -> None:
    _seed(store, _new(SUBJECT, DEPLOY_BODY))
    resp = _client(_service(store, ExplainDownLLM())).post(
        "/kb/fence", json={"leaver": LEAVER, "subject": SUBJECT}
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == PROVIDER_UNAVAILABLE


@pytest.mark.parametrize(
    "body",
    [
        {"leaver": LEAVER, "subject": ""},
        {"leaver": LEAVER, "subject": "   "},
        {"leaver": LEAVER},
        {"leaver": "Bad Leaver", "subject": SUBJECT},
        {"leaver": LEAVER, "subject": "가" * 201},
    ],
)
def test_route_fence_422(store: KnowledgeStore, body: dict[str, str]) -> None:
    resp = _client(_service(store)).post("/kb/fence", json=body)
    assert resp.status_code == 422
    assert store.list_queue(LEAVER) == []
