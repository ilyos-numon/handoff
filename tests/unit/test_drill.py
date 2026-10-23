import json
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from handoff.contracts import (
    DrillAnswerRequest,
    DrillCreateRequest,
    DrillDraft,
    DrillFeedbackDraft,
    DrillScenario,
    Evidence,
    KnowledgeCard,
    KnowledgeConflict,
    KnowledgeNotFound,
    NewCard,
)
from handoff.drill import DrillService
from handoff.knowledge_store import KnowledgeStore
from handoff.llm_tasks.drill import (
    DRILL_ASK,
    fake_make_drill,
    fake_review_drill,
    qwen_make_drill,
    qwen_review_drill,
)
from handoff.providers import FakeLLM, ProviderError
from handoff.routes_drill import router
from handoff.store import SessionStore

T0 = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
LEAVER = "alice"
WEAK = "잘 모르겠습니다"
SECRET_ANSWER = "정산 배치 재실행 스크립트를 먼저 돌리고 결제팀 리드에게 알립니다 고유답변표식"


@pytest.fixture
def conn_store() -> tuple[SessionStore, KnowledgeStore]:
    sessions = SessionStore(":memory:")
    return sessions, KnowledgeStore.from_session_store(sessions)


@pytest.fixture
def ks(conn_store: tuple[SessionStore, KnowledgeStore]) -> KnowledgeStore:
    return conn_store[1]


def _new_card(type: str, title: str, body: str, entities: list[str], confidence: float = 0.8,
              leaver: str = LEAVER, session_id: str = "s1") -> NewCard:
    return NewCard.model_validate({
        "leaver": leaver, "role": "backend_developer", "session_id": session_id, "type": type,
        "title": title, "body": body, "entities": entities, "confidence": confidence,
        "evidence": [Evidence(session_id=session_id, question_id="sys-01", session_number=1,
                              excerpt=body[:50])],
    })


def _seed_cards(ks: KnowledgeStore) -> list[KnowledgeCard]:
    return ks.replace_session_cards("s1", [
        _new_card("procedure", "정산 배치 재실행",
                  "정산 배치가 실패하면 재실행 스크립트를 먼저 확인합니다. 그다음 결과를 봅니다.",
                  ["정산 배치"], 0.6),
        _new_card("pitfall", "금요일 배포 금지",
                  "금요일 저녁에는 정산 배치 관련 배포를 하지 않습니다. 주말 장애로 이어집니다.",
                  ["정산 배치"], 0.8),
        _new_card("contact", "대행사 연락", "결제 대행사 장애는 대행사 장애 연락망으로 연락합니다.",
                  ["결제 대행사"], 0.6),
        _new_card("decision", "메시지 큐 선택", "메시지 큐는 재처리가 쉬워서 선택했습니다.",
                  ["메시지 큐"], 0.6),
    ], T0)


def _card(id: str, type: str, body: str, entities: list[str],
          confidence: float = 0.6) -> KnowledgeCard:
    new = _new_card(type, f"제목 {id}", body, entities, confidence)
    return KnowledgeCard(**new.model_dump(), id=id, created_at=T0)


def _scenario(points: list[str], card_ids: list[str] | None = None) -> DrillScenario:
    return DrillScenario(id="d1", leaver=LEAVER, title="훈련", situation="상황", ask=DRILL_ASK,
                         expected_points=points, card_ids=card_ids or ["c1"], created_at=T0)


# ---- fake 출제
def test_fake_make_drill_prefers_highest_confidence_pitfall_and_shared_entity_cards() -> None:
    cards = [
        _card("c1", "procedure", "재실행을 확인합니다. 둘째 문장.", ["정산 배치"]),
        _card("c2", "pitfall", "배포하지 않습니다.", ["정산 배치"], 0.7),
        _card("c3", "pitfall", "조심합니다.", ["메시지 큐"], 0.9),
        _card("c4", "decision", "큐를 선택했습니다.", ["메시지 큐"]),
        _card("c5", "contact", "연락합니다.", ["메시지 큐"]),
    ]
    draft = fake_make_drill(cards, None)
    assert draft.card_ids == ["c3", "c4"]
    assert draft.title == "훈련: 제목 c3"
    assert draft.situation == "다음 상황이 생겼습니다: 제목 c3. 지금 당번은 당신입니다."
    assert draft.ask == DRILL_ASK
    assert draft.expected_points == ["조심합니다.", "큐를 선택했습니다."]


def test_fake_make_drill_uses_seed_and_first_sentence() -> None:
    cards = [
        _card("c1", "procedure", "재실행을 확인합니다. 둘째 문장.", ["정산 배치"]),
        _card("c2", "pitfall", "배포하지 않습니다.", ["정산 배치"], 0.9),
    ]
    draft = fake_make_drill(cards, cards[0])
    assert draft.card_ids == ["c1", "c2"]
    assert draft.expected_points == ["재실행을 확인합니다.", "배포하지 않습니다."]


def test_fake_make_drill_falls_back_to_procedure_then_decision() -> None:
    proc = [_card("c2", "decision", "결정.", []), _card("c3", "procedure", "절차.", [])]
    assert fake_make_drill(proc, None).card_ids == ["c3"]
    dec = [_card("c4", "decision", "결정.", []), _card("c5", "contact", "연락.", [])]
    assert fake_make_drill(dec, None).card_ids == ["c4"]


def test_fake_make_drill_caps_extra_cards_at_two() -> None:
    cards = [_card(f"c{i}", "procedure", f"절차 {i}.", ["정산 배치"]) for i in range(1, 6)]
    assert fake_make_drill(cards, None).card_ids == ["c1", "c2", "c3"]


# ---- fake 피드백
def test_fake_review_drill_splits_covered_and_missed() -> None:
    drill = _scenario(["재실행 스크립트를 먼저 확인합니다.", "결제팀 리드에게 알립니다."],
                      ["c1", "c2"])
    fb = fake_review_drill(drill, [], "재실행 스크립트를 먼저 확인합니다")
    assert fb.covered_points == ["재실행 스크립트를 먼저 확인합니다."]
    assert fb.missed_points == ["결제팀 리드에게 알립니다."]
    assert fb.feedback == "다룬 요점 1개, 더 살펴볼 요점 1개입니다. 근거 카드 c1, c2 를 읽어 보세요."


# ---- qwen 출제·피드백 (가짜 chat)
class FakeChat:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[tuple[str, str, int, bool]] = []

    def __call__(self, system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        self.calls.append((system, user, max_tokens, json_mode))
        return self.reply


def test_qwen_make_drill_parses_json() -> None:
    cards = [_card("c1", "pitfall", "배포하지 않습니다.", [])]
    reply = json.dumps({"title": "t", "situation": "s", "ask": "a",
                        "expected_points": ["p1"], "card_ids": ["c1"]})
    chat = FakeChat(reply)
    draft = qwen_make_drill(chat, cards, None)
    assert draft == DrillDraft(title="t", situation="s", ask="a", expected_points=["p1"],
                               card_ids=["c1"])
    system, _, _, json_mode = chat.calls[0]
    assert json_mode is True
    assert "JSON" in system and "지어내지 않" in system and "역할명" in system


@pytest.mark.parametrize("reply", ["not json", "[]", json.dumps({"title": "t"}),
                                   json.dumps({"title": "t", "situation": "s", "ask": "a",
                                               "expected_points": [], "card_ids": ["c1"]})])
def test_qwen_make_drill_falls_back_to_fake(reply: str) -> None:
    cards = [_card("c1", "pitfall", "배포하지 않습니다.", [])]
    assert qwen_make_drill(FakeChat(reply), cards, None) == fake_make_drill(cards, None)


def test_qwen_review_drill_parses_and_falls_back() -> None:
    drill = _scenario(["재실행을 확인합니다."])
    ok = json.dumps({"covered_points": ["재실행을 확인합니다."], "missed_points": [],
                     "feedback": "잘 다뤘습니다."})
    assert qwen_review_drill(FakeChat(ok), drill, [], "x") == DrillFeedbackDraft(
        covered_points=["재실행을 확인합니다."], missed_points=[], feedback="잘 다뤘습니다.")
    assert qwen_review_drill(FakeChat("{bad"), drill, [], WEAK) == fake_review_drill(
        drill, [], WEAK)


def test_qwen_provider_error_propagates() -> None:
    def boom(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        raise ProviderError("llm request failed: status=503")

    with pytest.raises(ProviderError):
        qwen_make_drill(boom, [_card("c1", "pitfall", "x.", [])], None)


# ---- 서비스
def test_service_create_includes_seed(ks: KnowledgeStore) -> None:
    cards = _seed_cards(ks)
    svc = DrillService(FakeLLM(), ks, clock=lambda: T0)
    drill = svc.create(LEAVER, DrillCreateRequest(seed_card_id=cards[0].id))
    assert drill.id.startswith("d")
    assert cards[0].id in drill.card_ids
    assert 1 <= len(drill.expected_points) <= 5
    assert svc.get(drill.id) == drill


def test_service_create_unknown_or_foreign_seed_is_not_found(ks: KnowledgeStore) -> None:
    _seed_cards(ks)
    other = ks.replace_session_cards(
        "s9", [_new_card("pitfall", "남의 카드", "조심합니다.", [], leaver="bob", session_id="s9")],
        T0)
    svc = DrillService(FakeLLM(), ks)
    for seed in ("c999", "zzz", other[0].id):
        with pytest.raises(KnowledgeNotFound, match="card not found"):
            svc.create(LEAVER, DrillCreateRequest(seed_card_id=seed))


def test_service_create_without_usable_cards_conflicts(ks: KnowledgeStore) -> None:
    ks.replace_session_cards("s1", [_new_card("contact", "연락", "연락합니다.", [])], T0)
    svc = DrillService(FakeLLM(), ks)
    with pytest.raises(KnowledgeConflict, match="no cards"):
        svc.create(LEAVER, DrillCreateRequest())
    with pytest.raises(KnowledgeConflict, match="no cards"):
        svc.create("nobody", DrillCreateRequest())


class StubLLM(FakeLLM):
    def __init__(self, draft: DrillDraft | None = None,
                 feedback: DrillFeedbackDraft | None = None) -> None:
        self.draft = draft
        self.feedback = feedback

    def make_drill(self, cards: list[KnowledgeCard], seed: KnowledgeCard | None) -> DrillDraft:
        return self.draft if self.draft is not None else super().make_drill(cards, seed)

    def review_drill(self, drill: DrillScenario, cards: list[KnowledgeCard],
                     answer: str) -> DrillFeedbackDraft:
        if self.feedback is not None:
            return self.feedback
        return super().review_drill(drill, cards, answer)


def test_service_create_drops_unknown_ids_and_falls_back(ks: KnowledgeStore) -> None:
    cards = _seed_cards(ks)
    bogus = DrillDraft(title="t", situation="s", ask="a", expected_points=["p"],
                       card_ids=["c999", "x"])
    drill = DrillService(StubLLM(draft=bogus), ks).create(LEAVER, DrillCreateRequest())
    candidates = [c for c in cards if c.type in ("pitfall", "procedure", "decision")]
    assert drill.card_ids == fake_make_drill(candidates, None).card_ids


def test_service_create_forces_seed_into_card_ids(ks: KnowledgeStore) -> None:
    cards = _seed_cards(ks)
    draft = DrillDraft(title="t", situation="s", ask="a", expected_points=["p"],
                       card_ids=[cards[1].id, "c999"])
    drill = DrillService(StubLLM(draft=draft), ks).create(
        LEAVER, DrillCreateRequest(seed_card_id=cards[3].id))
    assert drill.card_ids == [cards[3].id, cards[1].id]


def test_service_review_keeps_only_expected_points(ks: KnowledgeStore) -> None:
    _seed_cards(ks)
    llm = StubLLM()
    svc = DrillService(llm, ks)
    drill = svc.create(LEAVER, DrillCreateRequest())
    first = drill.expected_points[0]
    llm.feedback = DrillFeedbackDraft(covered_points=["지어낸 요점", first], missed_points=[],
                                      feedback="f")
    fb = svc.review(drill.id, DrillAnswerRequest(text="답"))
    assert fb.covered_points == [first]
    assert fb.missed_points == drill.expected_points[1:]
    assert fb.card_refs == drill.card_ids
    assert fb.drill_id == drill.id


def test_service_get_missing_drill(ks: KnowledgeStore) -> None:
    svc = DrillService(FakeLLM(), ks)
    for drill_id in ("d999", "bad"):
        with pytest.raises(KnowledgeNotFound, match="drill not found"):
            svc.get(drill_id)


# ---- 라우터
@pytest.fixture
def app_parts(
    conn_store: tuple[SessionStore, KnowledgeStore],
) -> tuple[FastAPI, StubLLM, SessionStore, KnowledgeStore]:
    sessions, ks = conn_store
    llm = StubLLM()
    app = FastAPI()
    app.state.knowledge = SimpleNamespace(store=ks, drills=DrillService(llm, ks, clock=lambda: T0))
    app.include_router(router)
    return app, llm, sessions, ks


@pytest.fixture
def client(
    app_parts: tuple[FastAPI, StubLLM, SessionStore, KnowledgeStore],
) -> Iterator[TestClient]:
    with TestClient(app_parts[0]) as c:
        yield c


def _create(client: TestClient, body: dict[str, object] | None = None) -> dict[str, object]:
    res = client.post(f"/leavers/{LEAVER}/drills", json=body or {})
    assert res.status_code == 201, res.text
    data: dict[str, object] = res.json()
    return data


def test_route_create_and_get(
    client: TestClient, app_parts: tuple[FastAPI, StubLLM, SessionStore, KnowledgeStore],
) -> None:
    cards = _seed_cards(app_parts[3])
    seed = cards[0].id
    data = _create(client, {"seed_card_id": seed})
    assert seed in data["card_ids"]  # type: ignore[operator]
    assert set(data["card_ids"]) <= {c.id for c in cards}  # type: ignore[call-overload]
    assert 1 <= len(data["expected_points"]) <= 5  # type: ignore[arg-type]
    assert str(data["id"]).startswith("d")
    got = client.get(f"/drills/{data['id']}")
    assert got.status_code == 200
    assert got.json() == data


def test_route_errors(
    client: TestClient, app_parts: tuple[FastAPI, StubLLM, SessionStore, KnowledgeStore],
) -> None:
    res = client.post(f"/leavers/{LEAVER}/drills", json={})
    assert (res.status_code, res.json()["detail"]) == (409, "no cards")
    _seed_cards(app_parts[3])
    res = client.post(f"/leavers/{LEAVER}/drills", json={"seed_card_id": "c999"})
    assert (res.status_code, res.json()["detail"]) == (404, "card not found")
    res = client.get("/drills/d999")
    assert (res.status_code, res.json()["detail"]) == (404, "drill not found")
    res = client.post("/drills/d999/answers", json={"text": "답"})
    assert (res.status_code, res.json()["detail"]) == (404, "drill not found")
    assert client.post("/leavers/Bad Name!/drills", json={}).status_code == 422
    drill_id = _create(client)["id"]
    assert client.post(f"/drills/{drill_id}/answers", json={"text": "   "}).status_code == 422


def test_route_weak_answer_has_missed_points_and_no_score(client: TestClient,
        app_parts: tuple[FastAPI, StubLLM, SessionStore, KnowledgeStore]) -> None:
    _seed_cards(app_parts[3])
    drill_id = _create(client)["id"]
    res = client.post(f"/drills/{drill_id}/answers", json={"text": WEAK})
    assert res.status_code == 200
    body = res.json()
    assert len(body["missed_points"]) >= 1
    assert body["covered_points"] == []
    for banned in ("score", "rank", "grade"):
        assert banned not in body
    assert set(body) == {"drill_id", "covered_points", "missed_points", "feedback", "card_refs"}


def test_route_first_point_answer_is_covered(
    client: TestClient, app_parts: tuple[FastAPI, StubLLM, SessionStore, KnowledgeStore],
) -> None:
    _seed_cards(app_parts[3])
    drill = _create(client)
    points = drill["expected_points"]
    assert isinstance(points, list)
    res = client.post(f"/drills/{drill['id']}/answers", json={"text": points[0]})
    assert res.status_code == 200
    body = res.json()
    assert points[0] in body["covered_points"]
    assert set(body["covered_points"]) | set(body["missed_points"]) == set(points)
    assert not set(body["covered_points"]) & set(body["missed_points"])


def test_route_provider_error_is_503(
    client: TestClient, app_parts: tuple[FastAPI, StubLLM, SessionStore, KnowledgeStore],
) -> None:
    _, llm, _, ks = app_parts
    _seed_cards(ks)
    drill_id = _create(client)["id"]

    def fail(*args: object) -> DrillDraft:
        raise ProviderError("llm request failed: status=503")

    llm.make_drill = fail  # type: ignore[method-assign,assignment]
    llm.review_drill = fail  # type: ignore[method-assign,assignment]
    res = client.post(f"/leavers/{LEAVER}/drills", json={})
    assert (res.status_code, res.json()["detail"]) == (503, "provider unavailable")
    res = client.post(f"/drills/{drill_id}/answers", json={"text": "답"})
    assert (res.status_code, res.json()["detail"]) == (503, "provider unavailable")


def test_answer_text_is_not_stored_anywhere_outside_drills(
    client: TestClient, app_parts: tuple[FastAPI, StubLLM, SessionStore, KnowledgeStore],
) -> None:
    _, _, sessions, ks = app_parts
    _seed_cards(ks)
    drill_id = _create(client)["id"]
    res = client.post(f"/drills/{drill_id}/answers", json={"text": SECRET_ANSWER})
    assert res.status_code == 200
    conn, _ = sessions.shared()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    assert "k_drills" in tables
    for table in tables:
        if table == "k_drills":
            continue
        for row in conn.execute(f"SELECT * FROM {table}"):
            for value in row:
                assert "고유답변표식" not in str(value), table
