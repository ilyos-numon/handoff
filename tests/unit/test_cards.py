from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from handoff.cards import CardService
from handoff.contracts import (
    SESSION_NOT_FOUND,
    SESSION_OPEN,
    CardDraft,
    EntityDraft,
    KnowledgeConflict,
    KnowledgeNotFound,
)
from handoff.kb import KBEntry
from handoff.knowledge_store import KnowledgeStore
from handoff.llm_tasks.cards import fake_extract_cards, qwen_extract_cards
from handoff.providers import FakeLLM, ProviderError
from handoff.routes_cards import router
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
