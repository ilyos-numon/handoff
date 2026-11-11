import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from handoff.artifacts import ArtifactService
from handoff.contracts import (
    ARTIFACT_TITLES,
    NO_CARDS,
    NOT_RECORDED_RESPONSE,
    NOT_RECORDED_SIGN,
    PREMORTEM_QUESTIONS,
    PREMORTEM_REASON,
    PREMORTEM_TOP,
    ArtifactKind,
    ArtifactSection,
    Evidence,
    KnowledgeCard,
    KnowledgeConflict,
    NewCard,
)
from handoff.knowledge_store import KnowledgeStore
from handoff.llm_tasks.artifacts import (
    KIND_INSTRUCTIONS,
    fake_write_artifact,
    premortem_ok,
    qwen_write_artifact,
)
from handoff.premortem import PremortemService
from handoff.providers import FakeLLM
from handoff.routes_artifacts import router as artifacts_router
from handoff.routes_premortem import router as premortem_router
from handoff.store import SessionStore

T0 = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)


def _new(type: str, title: str, body: str, confidence: float = 0.6,
         entities: list[str] | None = None, question_id: str = "sys-01",
         leaver: str = "alice") -> NewCard:
    return NewCard.model_validate({
        "leaver": leaver, "role": "backend_developer", "session_id": "s1", "type": type,
        "title": title, "body": body, "entities": entities or [],
        "confidence": confidence,
        "evidence": [Evidence(session_id="s1", question_id=question_id, session_number=1,
                              excerpt=title)],
    })


@pytest.fixture
def ks() -> KnowledgeStore:
    return KnowledgeStore(*SessionStore(":memory:").shared())


def _seed(ks: KnowledgeStore, cards: list[NewCard]) -> list[KnowledgeCard]:
    return ks.replace_session_cards("s1", cards, T0)


class StubLLM(FakeLLM):
    def __init__(self, sections: list[ArtifactSection]) -> None:
        self.sections = sections
        self.calls: list[tuple[str, list[str]]] = []

    def write_artifact(self, kind: ArtifactKind,
                       cards: list[KnowledgeCard]) -> list[ArtifactSection]:
        self.calls.append((kind, [c.id for c in cards]))
        return self.sections


def _ok(heading: str, ids: list[str]) -> ArtifactSection:
    return ArtifactSection(heading=heading, body="징후: 큐가 쌓입니다.\n대응: 재실행합니다.",
                           card_ids=ids)


# ---- PremortemService.enqueue

def test_enqueue_three_questions_in_order(ks: KnowledgeStore) -> None:
    items = PremortemService(ks, clock=lambda: T0).enqueue("alice", "backend_developer")
    assert [q.text for q in items] == list(PREMORTEM_QUESTIONS)
    assert all(q.source == "premortem" and q.axis == "incidents" for q in items)
    assert all(q.reason == PREMORTEM_REASON and q.status == "pending" for q in items)
    assert all(q.ref_id is None and q.created_at == T0 for q in items)
    assert [int(q.id[1:]) for q in items] == sorted(int(q.id[1:]) for q in items)


def test_enqueue_twice_returns_same_ids(ks: KnowledgeStore) -> None:
    svc = PremortemService(ks, clock=lambda: T0)
    first = svc.enqueue("alice", "backend_developer")
    second = svc.enqueue("alice", "backend_developer")
    assert [q.id for q in second] == [q.id for q in first]
    assert len(ks.list_queue("alice", source="premortem")) == 3


def test_next_pending_after_gap_before_generated(ks: KnowledgeStore) -> None:
    ks.enqueue("alice", "backend_developer", "generated", "systems", "생성 질문", "생성",
               None, T0)
    pm = PremortemService(ks, clock=lambda: T0).enqueue("alice", "backend_developer")
    gap, _ = ks.enqueue("alice", "backend_developer", "gap", "systems", "빈틈 질문", "빈틈",
                        None, T0)
    first = ks.next_pending("alice", "backend_developer")
    assert first is not None and first.id == gap.id
    ks.mark_asked(gap.id, "s9")
    second = ks.next_pending("alice", "backend_developer")
    assert second is not None and second.id == pm[0].id


def _client(ks: KnowledgeStore, llm: FakeLLM | None = None) -> TestClient:
    app = FastAPI()
    app.state.knowledge = SimpleNamespace(
        premortem=PremortemService(ks, clock=lambda: T0),
        artifacts=ArtifactService(llm or FakeLLM(), ks, clock=lambda: T0))
    app.include_router(premortem_router)
    app.include_router(artifacts_router)
    return TestClient(app)


def test_route_enqueue_returns_three(ks: KnowledgeStore) -> None:
    r = _client(ks).post("/leavers/alice/premortem/questions?role=ops")
    assert r.status_code == 200
    body = r.json()
    assert [q["text"] for q in body] == list(PREMORTEM_QUESTIONS)
    assert all(q["source"] == "premortem" and q["role"] == "ops" for q in body)


def test_route_default_role(ks: KnowledgeStore) -> None:
    body = _client(ks).post("/leavers/alice/premortem/questions").json()
    assert {q["role"] for q in body} == {"backend_developer"}


def test_route_bad_leaver_422(ks: KnowledgeStore) -> None:
    assert _client(ks).post("/leavers/Alice!/premortem/questions").status_code == 422


# ---- fake_write_artifact("premortem")

def test_fake_sign_and_response_not_recorded(ks: KnowledgeStore) -> None:
    cards = _seed(ks, [_new("pitfall", "인증서 만료", "인증서는 1년마다 갱신합니다.")])
    secs = fake_write_artifact("premortem", cards)
    assert secs == [ArtifactSection(
        heading="1. 인증서 만료",
        body=f"징후: {NOT_RECORDED_SIGN}\n대응: {NOT_RECORDED_RESPONSE}",
        card_ids=[cards[0].id])]
    assert "기록되지 않았습니다" in secs[0].body


def test_fake_sign_and_response_from_pitfall_body(ks: KnowledgeStore) -> None:
    body = "주문 화면이 느려집니다. 캐시 서버 상태를 먼저 확인합니다. 그 외 기록은 없습니다."
    cards = _seed(ks, [_new("pitfall", "캐시 서버 포화", body)])
    secs = fake_write_artifact("premortem", cards)
    assert secs[0].body == "징후: 주문 화면이 느려집니다.\n대응: 캐시 서버 상태를 먼저 확인합니다."
    assert secs[0].card_ids == [cards[0].id]


def test_fake_same_entity_procedure_adds_card(ks: KnowledgeStore) -> None:
    cards = _seed(ks, [
        _new("procedure", "다른 절차", "관계없는 절차입니다.", entities=["메일 서버"]),
        _new("pitfall", "백업 실패", "백업 오류 알림이 옵니다.", entities=["백업 서버"]),
        _new("procedure", "백업 복구", "백업 작업을 수동으로 재실행합니다. 결과를 공유합니다.",
             entities=["백업 서버"]),
    ])
    secs = fake_write_artifact("premortem", cards)
    assert len(secs) == 1
    assert secs[0].card_ids == [cards[1].id, cards[2].id]
    assert secs[0].body == "징후: 백업 오류 알림이 옵니다.\n대응: 백업 작업을 수동으로 재실행합니다."


def test_fake_top5_input_order_numbered(ks: KnowledgeStore) -> None:
    cards = _seed(ks, [_new("pitfall", f"함정 {i}", f"함정 {i} 본문입니다.") for i in range(7)])
    order = list(reversed(cards))
    secs = fake_write_artifact("premortem", order)
    assert len(secs) == PREMORTEM_TOP
    assert [s.card_ids[0] for s in secs] == [c.id for c in order[:PREMORTEM_TOP]]
    assert [s.heading for s in secs] == [f"{i}. {c.title}" for i, c in enumerate(order[:5], 1)]
    assert premortem_ok(secs, {c.id for c in cards})


def test_fake_without_pitfall_empty(ks: KnowledgeStore) -> None:
    cards = _seed(ks, [_new("procedure", "절차", "절차입니다.")])
    assert fake_write_artifact("premortem", cards) == []


# ---- qwen_write_artifact("premortem")

def test_qwen_missing_response_line_falls_back(ks: KnowledgeStore) -> None:
    cards = _seed(ks, [_new("pitfall", "디스크 부족", "로그가 쌓입니다.")])

    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        return json.dumps({"sections": [{"heading": "1. 디스크", "body": "징후: 로그가 쌓입니다.",
                                         "card_ids": [cards[0].id]}]}, ensure_ascii=False)

    assert qwen_write_artifact(chat, "premortem", cards) == fake_write_artifact("premortem", cards)


def test_qwen_valid_kept_and_listing_in_input_order(ks: KnowledgeStore) -> None:
    cards = _seed(ks, [_new("pitfall", "가 함정", "가."), _new("pitfall", "나 함정", "나.")])
    order = [cards[1], cards[0]]
    seen: dict[str, str] = {}

    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        seen["user"] = user
        return json.dumps({"sections": [_ok("1. 나", [cards[1].id]).model_dump()]},
                          ensure_ascii=False)

    assert qwen_write_artifact(chat, "premortem", order) == [_ok("1. 나", [cards[1].id])]
    assert KIND_INSTRUCTIONS["premortem"] in seen["user"]
    assert seen["user"].index(f"[{cards[1].id}]") < seen["user"].index(f"[{cards[0].id}]")


# ---- ArtifactService.generate("premortem")

def test_generate_premortem_answer_pitfall_first(ks: KnowledgeStore) -> None:
    pm = PremortemService(ks, clock=lambda: T0).enqueue("alice", "backend_developer")
    cards = _seed(ks, [
        _new("pitfall", "높은 신뢰 함정", "높은 신뢰 본문입니다.", confidence=0.9),
        _new("pitfall", "부검 답 함정", "부검 답 본문입니다.", confidence=0.4,
             question_id=f"{pm[0].id}-f1"),
        _new("pitfall", "중간 신뢰 함정", "중간 본문입니다.", confidence=0.6),
    ])
    llm = StubLLM([])
    art = ArtifactService(llm, ks, clock=lambda: T0).generate("alice", "premortem")
    assert llm.calls == [("premortem", [cards[1].id, cards[0].id, cards[2].id])]
    assert art.sections[0].heading == "1. 부검 답 함정"
    assert [s.card_ids[0] for s in art.sections] == [cards[1].id, cards[0].id, cards[2].id]
    assert art.kind == "premortem" and art.title == ARTIFACT_TITLES["premortem"]
    assert art.markdown.startswith(f"# {ARTIFACT_TITLES['premortem']}\n\n")


def test_generate_premortem_passes_procedures_after_pitfalls(ks: KnowledgeStore) -> None:
    cards = _seed(ks, [_new("procedure", "절차", "절차입니다."),
                       _new("pitfall", "함정", "함정입니다."),
                       _new("decision", "결정", "결정입니다.")])
    llm = StubLLM([_ok("1. 함정", [cards[1].id, cards[0].id])])
    art = ArtifactService(llm, ks, clock=lambda: T0).generate("alice", "premortem")
    assert llm.calls == [("premortem", [cards[1].id, cards[0].id])]
    assert art.sections == [_ok("1. 함정", [cards[1].id, cards[0].id])]
    assert art.card_ids == [cards[1].id, cards[0].id]


@pytest.mark.parametrize("bad", [
    "missing_response", "missing_sign", "too_many", "unknown_id",
])
def test_generate_premortem_invalid_llm_falls_back(ks: KnowledgeStore, bad: str) -> None:
    cards = _seed(ks, [_new("pitfall", "함정", "큐가 쌓입니다.")])
    cid = cards[0].id
    secs = {
        "missing_response": [ArtifactSection(heading="1. a", body="징후: 큐", card_ids=[cid])],
        "missing_sign": [ArtifactSection(heading="1. a", body="대응: 확인", card_ids=[cid])],
        "too_many": [_ok(f"{i}. a", [cid]) for i in range(1, PREMORTEM_TOP + 2)],
        "unknown_id": [_ok("1. a", [cid]), _ok("2. b", ["c999"])],
    }[bad]
    art = ArtifactService(StubLLM(secs), ks, clock=lambda: T0).generate("alice", "premortem")
    assert art.sections == fake_write_artifact("premortem", cards)


def test_generate_premortem_without_pitfall_conflict(ks: KnowledgeStore) -> None:
    _seed(ks, [_new("procedure", "절차", "절차입니다.")])
    with pytest.raises(KnowledgeConflict) as exc:
        ArtifactService(FakeLLM(), ks, clock=lambda: T0).generate("alice", "premortem")
    assert str(exc.value) == NO_CARDS


def test_route_premortem_artifact_409_and_200(ks: KnowledgeStore) -> None:
    c = _client(ks)
    r = c.post("/leavers/alice/artifacts/premortem")
    assert r.status_code == 409 and r.json()["detail"] == NO_CARDS
    _seed(ks, [_new("pitfall", "함정", "큐가 쌓입니다.")])
    r = c.post("/leavers/alice/artifacts/premortem")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "premortem"
    assert "징후:" in body["sections"][0]["body"] and "대응:" in body["sections"][0]["body"]
    assert c.get("/leavers/alice/artifacts/premortem").json()["id"] == body["id"]
