import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from handoff.cards import CardService
from handoff.contracts import (
    CHANGE_EVENT_LABELS,
    SESSION_NOT_FOUND,
    SESSION_OPEN,
    CardDraft,
    EntityDraft,
    InvalidationCondition,
    KnowledgeCard,
    KnowledgeConflict,
    KnowledgeNotFound,
    NewCard,
)
from handoff.kb import KBEntry
from handoff.knowledge_store import KnowledgeStore
from handoff.llm_tasks.cards import fake_extract_cards, qwen_extract_cards
from handoff.privacy import find_pii
from handoff.providers import FakeLLM, ProviderError
from handoff.routes_cards import router
from handoff.signals import UNCERTAIN_CONFIDENCE_MAX, fake_invalidation
from handoff.store import SessionRow, SessionStore

T0 = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
ROLE = "backend_developer"
LEAVER = "default"

ANSWER_MAIN = (
    "정산 배치는 매일 새벽 2시에 돕니다. 실패하면 먼저 로그를 확인하고 재실행 스크립트를 실행합니다. "
    "금요일 배포는 하지 않습니다. 대행사 응답이 느리기 때문에 타임아웃을 30초로 정했습니다."
)
ANSWER_FOLLOW = "급하면 ops@example.com 이나 010-1234-5678 로 운영 리드에게 연락합니다."


def _entry(question_id: str, answer: str, session_id: str = "s1") -> KBEntry:
    return KBEntry(
        session_id=session_id,
        question_id=question_id,
        axis="systems",
        question="질문",
        answer=answer,
        created_at=T0,
        session_number=1,
    )


def _session(store: SessionStore, session_id: str = "s1", ended: bool = True,
             number: int = 1, leaver: str = LEAVER) -> None:
    store.insert_session(SessionRow(
        session_id=session_id, leaver=leaver, role=ROLE, number=number, started_at=T0,
        deadline=T0 + timedelta(seconds=900),
        ended_at=T0 + timedelta(seconds=600) if ended else None,
        node_index=0, current_json=None, summary=[],
    ))


class StubLLM(FakeLLM):
    def __init__(self, drafts: list[CardDraft] | None = None, fail: bool = False) -> None:
        self.drafts = drafts
        self.fail = fail
        self.calls: list[tuple[list[KBEntry], list[str]]] = []

    def extract_cards(self, entries: list[KBEntry], known_entities: list[str]) -> list[CardDraft]:
        self.calls.append((entries, known_entities))
        if self.fail:
            raise ProviderError("down")
        if self.drafts is not None:
            return self.drafts
        return super().extract_cards(entries, known_entities)


@pytest.fixture
def sessions() -> SessionStore:
    store = SessionStore(":memory:")
    _session(store)
    store.add_answer(_entry("sys-01", ANSWER_MAIN))
    store.add_answer(_entry("sys-01-f1", ANSWER_FOLLOW))
    return store


@pytest.fixture
def kstore(sessions: SessionStore) -> KnowledgeStore:
    return KnowledgeStore.from_session_store(sessions)


def _service(sessions: SessionStore, kstore: KnowledgeStore, llm: FakeLLM | None = None) -> CardService:
    return CardService(llm or FakeLLM(), kstore, sessions, clock=lambda: T0)


# ---- fake 추출
def test_fake_extract_classifies_sentences_by_type() -> None:
    drafts = fake_extract_cards([_entry("sys-01", ANSWER_MAIN)], ["정산 배치"])
    by_type = {d.type: d for d in drafts}
    assert set(by_type) == {"pitfall", "decision", "entity"}
    pitfall = by_type["pitfall"]
    assert pitfall.title == "실패하면 먼저 로그를 확인하고 재실행 스크립트를 실행합니다."
    assert "금요일 배포는 하지 않습니다." in pitfall.body
    assert pitfall.confidence == 0.8
    assert by_type["decision"].confidence == 0.6
    assert all(d.evidence_question_ids == ["sys-01"] for d in drafts)
    assert by_type["pitfall"].entities == ["정산 배치"]
    entity = by_type["entity"]
    assert entity.title == "정산 배치"
    assert entity.body == "정산 배치는 매일 새벽 2시에 돕니다."
    assert entity.confidence == 0.6


def test_fake_extract_entity_card_once_per_name() -> None:
    entries = [_entry("sys-01", "정산 배치를 먼저 확인합니다."),
               _entry("sys-02", "정산 배치는 조심해야 합니다.")]
    drafts = fake_extract_cards(entries, ["정산 배치"])
    assert [d.title for d in drafts if d.type == "entity"] == ["정산 배치"]


def test_fake_extract_contact_card() -> None:
    drafts = fake_extract_cards([_entry("ppl-01", ANSWER_FOLLOW)], [])
    assert [d.type for d in drafts] == ["contact"]


def test_fake_extract_is_deterministic() -> None:
    entries = [_entry("sys-01", ANSWER_MAIN), _entry("sys-01-f1", ANSWER_FOLLOW)]
    assert fake_extract_cards(entries, ["정산 배치"]) == fake_extract_cards(entries, ["정산 배치"])


# ---- qwen 추출
def test_qwen_broken_json_falls_back_to_fake() -> None:
    entries = [_entry("sys-01", ANSWER_MAIN)]

    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        assert json_mode is True
        return "{not json"

    assert qwen_extract_cards(chat, entries, ["정산 배치"]) == fake_extract_cards(entries, ["정산 배치"])


def test_qwen_wrong_shape_falls_back_to_fake() -> None:
    entries = [_entry("sys-01", ANSWER_MAIN)]

    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        return '{"cards": [{"type": "nope", "title": "", "body": ""}]}'

    assert qwen_extract_cards(chat, entries, []) == fake_extract_cards(entries, [])


def test_qwen_parses_valid_cards() -> None:
    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        assert "JSON" in system
        assert "sys-01" in user
        return (
            '{"cards": [{"type": "pitfall", "title": "금요일 배포 금지", '
            '"body": "금요일에는 배포하지 않는다.", "entities": ["정산 배치"], '
            '"confidence": 0.9, "evidence_question_ids": ["sys-01"]}]}'
        )

    drafts = qwen_extract_cards(chat, [_entry("sys-01", ANSWER_MAIN)], ["정산 배치"])
    assert len(drafts) == 1
    assert drafts[0].type == "pitfall"
    assert drafts[0].confidence == 0.9


RETRY_ANSWER = "승인 요청 재시도 횟수는 함부로 바꾸면 안 됩니다. 재시도를 늘리면 중복 승인 위험이 생깁니다."
DESK_ANSWER = "고객 문의는 고객센터 상담원이 먼저 받고 파트장이 티켓을 만들어 개발 채널에 넘깁니다."


def _qwen_card(title: str, body: str, qids: list[str]) -> str:
    return json.dumps({"cards": [{
        "type": "pitfall", "title": title, "body": body, "entities": [],
        "confidence": 0.8, "evidence_question_ids": qids,
    }]}, ensure_ascii=False)


def test_qwen_prompt_labels_each_answer_id() -> None:
    seen: list[str] = []

    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        seen.append(system + user)
        return '{"cards": []}'

    qwen_extract_cards(chat, [_entry("sys-03", RETRY_ANSWER), _entry("ppl-04", DESK_ANSWER)], [])
    assert "[답변 id=sys-03]" in seen[0]
    assert "[답변 id=ppl-04]" in seen[0]
    assert "직접 말한 답변의 id 만" in seen[0]


def test_qwen_evidence_replaced_by_answer_that_says_it() -> None:
    entries = [_entry("sys-03", RETRY_ANSWER), _entry("ppl-04", DESK_ANSWER)]

    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        return _qwen_card("재시도 횟수 변경 주의", "재시도를 늘리면 중복 승인 위험이 생긴다.", ["ppl-04"])

    drafts = qwen_extract_cards(chat, entries, [])
    assert [d.evidence_question_ids for d in drafts] == [["sys-03"]]


def test_qwen_keeps_correct_evidence_and_drops_unrelated() -> None:
    entries = [_entry("sys-03", RETRY_ANSWER), _entry("ppl-04", DESK_ANSWER)]

    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        return _qwen_card("고객센터 문의 흐름", "고객센터 상담원이 먼저 받고 티켓을 넘긴다.",
                          ["ppl-04", "sys-03"])

    drafts = qwen_extract_cards(chat, entries, [])
    assert [d.evidence_question_ids for d in drafts] == [["ppl-04"]]


def test_qwen_drops_card_unrelated_to_any_answer() -> None:
    entries = [_entry("sys-03", RETRY_ANSWER)]

    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        return _qwen_card("주간 회의", "목요일마다 팀 회의록을 작성한다.", ["sys-03"])

    assert qwen_extract_cards(chat, entries, []) == []


FRONT_ANSWER = "객실 고장 신고는 프런트 직원이 받고 시설 담당에게 넘깁니다. 수리 기록은 장부에 남깁니다."
CLEAN_ANSWER = "청소는 오전 열 시 전에 끝냅니다. 늦어지면 체크인 문의가 들어옵니다."


def _cards_json(cards: list[tuple[str, str, str, list[str]]]) -> str:
    return json.dumps({"cards": [
        {"type": t, "title": title, "body": body, "entities": [], "confidence": 0.8,
         "evidence_question_ids": qids}
        for t, title, body, qids in cards
    ]}, ensure_ascii=False)


def test_qwen_adds_contact_card_for_uncovered_handoff_answer() -> None:
    entries = [_entry("h-01", FRONT_ANSWER), _entry("h-02", CLEAN_ANSWER)]
    calls: list[tuple[str, str]] = []

    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        calls.append((system, user))
        if len(calls) == 1:
            return _cards_json([("procedure", "객실 고장 신고 처리", "수리 기록은 장부에 남깁니다.", ["h-01"])])
        return _cards_json([("contact", "고장 신고 전달", "프런트 직원이 받고 시설 담당에게 넘깁니다.",
                             ["h-02"])])

    drafts = qwen_extract_cards(chat, entries, [])
    assert len(calls) == 2
    assert "[답변 id=h-01]" in calls[1][1]
    assert "[답변 id=h-02]" not in calls[1][1]
    contacts = [d for d in drafts if d.type == "contact"]
    assert [d.evidence_question_ids for d in contacts] == [["h-01"]]
    assert [d.type for d in drafts] == ["procedure", "contact"]


def test_qwen_no_contact_followup_when_answer_already_has_contact_card() -> None:
    entries = [_entry("h-01", FRONT_ANSWER)]
    calls: list[str] = []

    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        calls.append(user)
        return _cards_json([("contact", "고장 신고 전달", "프런트 직원이 받고 시설 담당에게 넘깁니다.",
                             ["h-01"])])

    drafts = qwen_extract_cards(chat, entries, [])
    assert len(calls) == 1
    assert [d.type for d in drafts] == ["contact"]


def test_qwen_contact_followup_keeps_only_contact_cards() -> None:
    entries = [_entry("h-01", FRONT_ANSWER)]
    calls: list[str] = []

    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        calls.append(user)
        return _cards_json([("procedure", "수리 기록", "수리 기록은 장부에 남깁니다.", ["h-01"])])

    drafts = qwen_extract_cards(chat, entries, [])
    assert len(calls) == 2
    assert [d.type for d in drafts] == ["procedure"]


def test_qwen_no_contact_followup_without_recipient() -> None:
    entries = [_entry("h-02", CLEAN_ANSWER)]
    calls: list[str] = []

    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        calls.append(user)
        return '{"cards": []}'

    assert qwen_extract_cards(chat, entries, []) == []
    assert len(calls) == 1


def test_qwen_provider_error_propagates() -> None:
    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        raise ProviderError("down")

    with pytest.raises(ProviderError):
        qwen_extract_cards(chat, [_entry("sys-01", ANSWER_MAIN)], [])


# ---- 서비스
def test_extract_builds_cards_with_evidence(sessions: SessionStore, kstore: KnowledgeStore) -> None:
    cards = _service(sessions, kstore).extract_for_session("s1")
    assert cards
    for card in cards:
        assert card.evidence[0].session_id == "s1"
        assert card.evidence[0].session_number == 1
        assert 0.0 <= card.confidence <= 1.0
        assert card.leaver == LEAVER and card.role == ROLE
        assert card.created_at == T0
    main = next(c for c in cards if c.evidence[0].question_id == "sys-01")
    assert main.evidence[0].excerpt == ANSWER_MAIN[:200]


def test_extract_passes_known_entities(sessions: SessionStore, kstore: KnowledgeStore) -> None:
    kstore.upsert_entity(LEAVER, EntityDraft(name="정산 배치", type="job", mention="m", source_id="src1"))
    llm = StubLLM()
    cards = _service(sessions, kstore, llm).extract_for_session("s1")
    assert llm.calls[0][1] == ["정산 배치"]
    assert len(llm.calls[0][0]) == 2
    assert any(c.type == "entity" and c.title == "정산 배치" for c in cards)


def test_extract_masks_contacts(sessions: SessionStore, kstore: KnowledgeStore) -> None:
    cards = _service(sessions, kstore).extract_for_session("s1")
    assert any(c.type == "contact" for c in cards)
    for card in cards:
        assert "ops@example.com" not in card.body + card.title
        assert "010-1234-5678" not in card.body + card.title


def test_extract_masks_contacts_in_evidence_excerpt(
    sessions: SessionStore, kstore: KnowledgeStore
) -> None:
    cards = _service(sessions, kstore).extract_for_session("s1")
    follow = [e for c in cards for e in c.evidence if e.question_id == "sys-01-f1"]
    assert follow
    for card in cards:
        for ev in card.evidence:
            assert find_pii(ev.excerpt) == []


def test_extract_drops_drafts_without_evidence(sessions: SessionStore, kstore: KnowledgeStore) -> None:
    drafts = [
        CardDraft(type="pitfall", title="근거 없음", body="지어낸 내용", confidence=0.9,
                  evidence_question_ids=["sys-99"]),
        CardDraft(type="procedure", title="근거 있음", body="로그를 확인", confidence=0.7,
                  evidence_question_ids=["sys-01", "sys-77"]),
    ]
    cards = _service(sessions, kstore, StubLLM(drafts)).extract_for_session("s1")
    assert [c.title for c in cards] == ["근거 있음"]
    assert [e.question_id for e in cards[0].evidence] == ["sys-01"]


def test_reextract_is_idempotent(sessions: SessionStore, kstore: KnowledgeStore) -> None:
    service = _service(sessions, kstore)
    first = service.extract_for_session("s1")
    second = service.extract_for_session("s1")
    assert len(first) == len(second)
    assert len(service.list(LEAVER, None, 0.0)) == len(second)


def test_extract_open_session_conflict(sessions: SessionStore, kstore: KnowledgeStore) -> None:
    _session(sessions, "s2", ended=False, number=2)
    with pytest.raises(KnowledgeConflict) as exc:
        _service(sessions, kstore).extract_for_session("s2")
    assert str(exc.value) == SESSION_OPEN


def test_extract_missing_session_not_found(sessions: SessionStore, kstore: KnowledgeStore) -> None:
    with pytest.raises(KnowledgeNotFound) as exc:
        _service(sessions, kstore).extract_for_session("nope")
    assert str(exc.value) == SESSION_NOT_FOUND


def test_list_filters_by_type_and_confidence(sessions: SessionStore, kstore: KnowledgeStore) -> None:
    service = _service(sessions, kstore)
    service.extract_for_session("s1")
    pitfalls = service.list(LEAVER, "pitfall", 0.0)
    assert pitfalls and all(c.type == "pitfall" for c in pitfalls)
    high = service.list(LEAVER, None, 0.7)
    assert high and all(c.confidence >= 0.7 for c in high)
    assert service.list("other", None, 0.0) == []


# ---- 라우터
def _client(service: CardService) -> TestClient:
    app = FastAPI()
    app.state.knowledge = SimpleNamespace(cards=service)
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def client(sessions: SessionStore, kstore: KnowledgeStore) -> Iterator[TestClient]:
    with _client(_service(sessions, kstore)) as c:
        yield c


def test_route_extract_and_list(client: TestClient) -> None:
    r1 = client.post("/sessions/s1/cards/extract")
    r2 = client.post("/sessions/s1/cards/extract")
    assert r1.status_code == 200 and r2.status_code == 200
    assert len(r1.json()) == len(r2.json()) > 0
    listed = client.get(f"/leavers/{LEAVER}/cards")
    assert listed.status_code == 200
    assert len([c for c in listed.json() if c["session_id"] == "s1"]) == len(r2.json())
    ids = [int(c["id"][1:]) for c in listed.json()]
    assert ids == sorted(ids)


def test_route_type_filter(client: TestClient) -> None:
    client.post("/sessions/s1/cards/extract")
    r = client.get(f"/leavers/{LEAVER}/cards", params={"type": "pitfall"})
    assert r.status_code == 200
    assert r.json() and all(c["type"] == "pitfall" for c in r.json())
    r = client.get(f"/leavers/{LEAVER}/cards", params={"min_confidence": 0.7})
    assert all(c["confidence"] >= 0.7 for c in r.json())


def test_route_validation_errors(client: TestClient) -> None:
    assert client.get("/leavers/BAD!/cards").status_code == 422
    assert client.get(f"/leavers/{LEAVER}/cards", params={"type": "x"}).status_code == 422
    assert client.get(f"/leavers/{LEAVER}/cards", params={"min_confidence": 2}).status_code == 422


def test_route_errors(sessions: SessionStore, client: TestClient) -> None:
    _session(sessions, "s2", ended=False, number=2)
    r = client.post("/sessions/s2/cards/extract")
    assert r.status_code == 409 and r.json()["detail"] == "session open"
    r = client.post("/sessions/nope/cards/extract")
    assert r.status_code == 404 and r.json()["detail"] == "session not found"


def test_route_provider_error_503(sessions: SessionStore, kstore: KnowledgeStore) -> None:
    with _client(_service(sessions, kstore, StubLLM(fail=True))) as c:
        r = c.post("/sessions/s1/cards/extract")
    assert r.status_code == 503 and r.json()["detail"] == "provider unavailable"


# ---- S5: 확신도·유통기한·at-risk
UNSURE_ANSWER = "아마 금요일 배포는 하지 않습니다. 전임자가 그렇게 정했던 것 같습니다."


class AnnotateLLM(FakeLLM):
    def __init__(self, result: list[list[InvalidationCondition]] | None = None,
                 fail: bool = False) -> None:
        self.result = result
        self.fail = fail
        self.calls: list[list[NewCard]] = []

    def annotate_invalidation(self, cards: list[NewCard]) -> list[list[InvalidationCondition]]:
        self.calls.append(cards)
        if self.fail:
            raise ProviderError("down")
        assert self.result is not None
        return self.result


def _unsure_session(sessions: SessionStore) -> None:
    _session(sessions, "s3", number=3)
    sessions.add_answer(_entry("sys-02", UNSURE_ANSWER, session_id="s3"))


def test_extract_marks_uncertain_card(sessions: SessionStore, kstore: KnowledgeStore) -> None:
    _unsure_session(sessions)
    cards = _service(sessions, kstore).extract_for_session("s3")
    assert cards
    pitfall = next(c for c in cards if c.type == "pitfall")
    assert pitfall.certainty == "uncertain"
    assert pitfall.uncertain_cues[0] == "아마"
    assert pitfall.confidence <= UNCERTAIN_CONFIDENCE_MAX


def test_extract_certain_answer_stays_certain(sessions: SessionStore, kstore: KnowledgeStore) -> None:
    cards = _service(sessions, kstore).extract_for_session("s1")
    assert cards and all(c.certainty == "certain" and c.uncertain_cues == [] for c in cards)
    assert any(c.confidence > UNCERTAIN_CONFIDENCE_MAX for c in cards)


def test_extract_annotates_with_llm_conditions(sessions: SessionStore, kstore: KnowledgeStore) -> None:
    drafts = [
        CardDraft(type="procedure", title="로그 확인", body="로그를 확인", confidence=0.7,
                  evidence_question_ids=["sys-01"]),
        CardDraft(type="contact", title="운영 리드", body="리드에게 연락", confidence=0.7,
                  evidence_question_ids=["sys-01-f1"]),
    ]
    cond = InvalidationCondition(event="policy_changed", condition="규정이 바뀌면 틀립니다.")
    llm = AnnotateLLM([[cond], []])
    llm_drafts = StubLLM(drafts)
    llm.extract_cards = llm_drafts.extract_cards  # type: ignore[method-assign]
    cards = _service(sessions, kstore, llm).extract_for_session("s1")
    assert [c.title for c in llm.calls[0]] == ["로그 확인", "운영 리드"]
    assert [c.invalidated_when for c in cards] == [[cond], []]
    stored = _service(sessions, kstore).list(LEAVER, None, 0.0)
    assert [c.invalidated_when for c in stored] == [[cond], []]


def _fake_conditions(cards: list[KnowledgeCard]) -> list[list[InvalidationCondition]]:
    return [fake_invalidation(f"{c.title} {c.body}") for c in cards]


def test_extract_provider_error_uses_fake_conditions(
    sessions: SessionStore, kstore: KnowledgeStore
) -> None:
    cards = _service(sessions, kstore, AnnotateLLM(fail=True)).extract_for_session("s1")
    assert cards
    assert [c.invalidated_when for c in cards] == _fake_conditions(cards)
    assert any(c.invalidated_when for c in cards)


def test_extract_length_mismatch_uses_fake_conditions(
    sessions: SessionStore, kstore: KnowledgeStore
) -> None:
    bogus = [[InvalidationCondition(event="policy_changed", condition="x")]]
    cards = _service(sessions, kstore, AnnotateLLM(bogus * 99)).extract_for_session("s1")
    assert [c.invalidated_when for c in cards] == _fake_conditions(cards)


def test_extract_no_cards_skips_annotation(sessions: SessionStore, kstore: KnowledgeStore) -> None:
    llm = AnnotateLLM([])
    llm.extract_cards = StubLLM([]).extract_cards  # type: ignore[method-assign]
    assert _service(sessions, kstore, llm).extract_for_session("s1") == []
    assert llm.calls == []


def test_list_filters_by_certainty(sessions: SessionStore, kstore: KnowledgeStore) -> None:
    _unsure_session(sessions)
    service = _service(sessions, kstore)
    service.extract_for_session("s1")
    service.extract_for_session("s3")
    unsure = service.list(LEAVER, None, 0.0, "uncertain")
    sure = service.list(LEAVER, None, 0.0, "certain")
    assert unsure and all(c.certainty == "uncertain" for c in unsure)
    assert sure and all(c.certainty == "certain" for c in sure)
    assert len(unsure) + len(sure) == len(service.list(LEAVER, None, 0.0))


def _risk_cards(sessions: SessionStore, kstore: KnowledgeStore) -> CardService:
    drafts = [
        CardDraft(type="procedure", title="정산 배치 재실행", body="정산 배치가 실패하면 재실행합니다.",
                  entities=["정산 배치"], confidence=0.8, evidence_question_ids=["sys-01"]),
        CardDraft(type="pitfall", title="대행사 타임아웃", body="대행사 응답이 느립니다.",
                  confidence=0.8, evidence_question_ids=["sys-01"]),
        CardDraft(type="decision", title="배포 요일", body="금요일 배포는 하지 않습니다.",
                  confidence=0.8, evidence_question_ids=["sys-01"]),
    ]
    sys = InvalidationCondition(event="system_replaced", condition="시스템이 바뀌면 틀립니다.")
    vendor = InvalidationCondition(event="vendor_changed", condition="업체가 바뀌면 틀립니다.")
    week = InvalidationCondition(event="schedule_changed", condition="일정이 바뀌면 틀립니다.")
    llm = AnnotateLLM([[sys, week], [vendor, sys], [week]])
    llm.extract_cards = StubLLM(drafts).extract_cards  # type: ignore[method-assign]
    service = _service(sessions, kstore, llm)
    service.extract_for_session("s1")
    return service


def test_at_risk_filters_by_event(sessions: SessionStore, kstore: KnowledgeStore) -> None:
    report = _risk_cards(sessions, kstore).at_risk(LEAVER, "system_replaced")
    assert report.leaver == LEAVER and report.event == "system_replaced"
    assert report.label == CHANGE_EVENT_LABELS["system_replaced"]
    assert report.target is None
    assert [r.card.title for r in report.cards] == ["정산 배치 재실행", "대행사 타임아웃"]
    assert all([c.event for c in r.conditions] == ["system_replaced"] for r in report.cards)
    ids = [int(r.card.id[1:]) for r in report.cards]
    assert ids == sorted(ids)


def test_at_risk_filters_by_target(sessions: SessionStore, kstore: KnowledgeStore) -> None:
    service = _risk_cards(sessions, kstore)
    by_entity = service.at_risk(LEAVER, "system_replaced", "정산 배치")
    assert [r.card.title for r in by_entity.cards] == ["정산 배치 재실행"]
    assert by_entity.target == "정산 배치"
    by_title = service.at_risk(LEAVER, "schedule_changed", "배포 요일")
    assert [r.card.title for r in by_title.cards] == ["배포 요일"]
    by_body = service.at_risk(LEAVER, "vendor_changed", "응답")
    assert [r.card.title for r in by_body.cards] == ["대행사 타임아웃"]
    assert service.at_risk(LEAVER, "system_replaced", "없는 대상").cards == []
    assert service.at_risk(LEAVER, "team_reorganized").cards == []
    assert service.at_risk("other", "system_replaced").cards == []


def test_route_certainty_filter(sessions: SessionStore, kstore: KnowledgeStore) -> None:
    _unsure_session(sessions)
    with _client(_service(sessions, kstore)) as c:
        c.post("/sessions/s1/cards/extract")
        c.post("/sessions/s3/cards/extract")
        r = c.get(f"/leavers/{LEAVER}/cards", params={"certainty": "uncertain"})
        assert r.status_code == 200
        assert r.json() and all(card["certainty"] == "uncertain" for card in r.json())
        assert all(card["confidence"] <= UNCERTAIN_CONFIDENCE_MAX for card in r.json())
        assert c.get(f"/leavers/{LEAVER}/cards", params={"certainty": "maybe"}).status_code == 422


def test_route_at_risk(sessions: SessionStore, kstore: KnowledgeStore) -> None:
    with _client(_risk_cards(sessions, kstore)) as c:
        r = c.get(f"/leavers/{LEAVER}/cards/at-risk",
                  params={"event": "system_replaced", "target": "정산 배치"})
        assert r.status_code == 200
        body = r.json()
        assert body["event"] == "system_replaced" and body["target"] == "정산 배치"
        assert body["label"] == CHANGE_EVENT_LABELS["system_replaced"]
        assert [x["card"]["title"] for x in body["cards"]] == ["정산 배치 재실행"]
        assert body["cards"][0]["conditions"] == [
            {"event": "system_replaced", "condition": "시스템이 바뀌면 틀립니다."}
        ]
        r = c.get(f"/leavers/{LEAVER}/cards/at-risk", params={"event": "vendor_changed"})
        assert r.status_code == 200 and r.json()["target"] is None
        assert [x["card"]["title"] for x in r.json()["cards"]] == ["대행사 타임아웃"]


def test_route_at_risk_validation(client: TestClient) -> None:
    assert client.get(f"/leavers/{LEAVER}/cards/at-risk", params={"event": "weather"}).status_code == 422
    assert client.get(f"/leavers/{LEAVER}/cards/at-risk").status_code == 422
    assert client.get("/leavers/BAD!/cards/at-risk",
                      params={"event": "vendor_changed"}).status_code == 422
