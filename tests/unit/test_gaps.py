import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from handoff.contracts import (
    ContradictionDraft,
    EntityDraft,
    Evidence,
    KnowledgeCard,
    NewCard,
)
from handoff.gaps import GapService, unknown_areas
from handoff.kb import KBEntry
from handoff.knowledge_store import KnowledgeStore
from handoff.llm_tasks.gaps import fake_find_contradictions, qwen_find_contradictions
from handoff.providers import FakeLLM, ProviderError
from handoff.routes_gaps import router
from handoff.store import SessionRow, SessionStore

T0 = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
LEAVER = "alice"
ROLE = "backend_developer"


@pytest.fixture
def sessions() -> SessionStore:
    return SessionStore(":memory:")


@pytest.fixture
def ks(sessions: SessionStore) -> KnowledgeStore:
    return KnowledgeStore.from_session_store(sessions)


def _service(ks: KnowledgeStore, sessions: SessionStore, llm: Any = None) -> GapService:
    return GapService(llm or FakeLLM(), ks, sessions, clock=lambda: T0)


def _entity(ks: KnowledgeStore, name: str, type: str = "system", source_id: str = "src1") -> None:
    ks.upsert_entity(LEAVER, EntityDraft.model_validate(
        {"name": name, "type": type, "mention": f"{name} 언급", "source_id": source_id}))


def _new_card(session_id: str, body: str, entities: list[str], type: str = "procedure",
              title: str = "카드") -> NewCard:
    return NewCard.model_validate({
        "leaver": LEAVER, "role": ROLE, "session_id": session_id, "type": type,
        "title": title, "body": body, "entities": entities, "confidence": 0.8,
        "evidence": [Evidence(session_id=session_id, question_id="sys-01", session_number=1,
                              excerpt=body[:200])],
    })


def _ended_session_with_answer(sessions: SessionStore, session_id: str, number: int,
                               answer: str) -> None:
    sessions.insert_session(
        SessionRow(session_id, LEAVER, ROLE, number, T0, T0, T0, 0, None, []))
    sessions.add_answer(KBEntry(session_id=session_id, question_id="sys-01", axis="systems",
                                question="질문", answer=answer, created_at=T0,
                                session_number=number))


def _kinds(report: Any) -> list[str]:
    return [g.kind for g in report.gaps]


# ---- 언급만
def test_mentioned_only_entity_without_cards_is_queued_as_gap(
        ks: KnowledgeStore, sessions: SessionStore) -> None:
    _entity(ks, "수수료 검증 스크립트", "job")
    report = _service(ks, sessions).refresh(LEAVER, ROLE)
    assert _kinds(report) == ["mentioned_only"]
    gap = report.gaps[0]
    assert gap.topic == "수수료 검증 스크립트"
    assert gap.risk == "medium"
    assert gap.detail == "자료·답변에 1번 나왔지만 지식 카드가 없습니다."
    assert gap.entity_ids == [ks.list_entities(LEAVER)[0].id]
    assert gap.queue_item_id is not None
    item = ks.get_queue_item(gap.queue_item_id)
    assert item is not None
    assert item.source == "gap"
    assert item.axis == "routines"
    assert item.reason == "빈 구멍: mentioned_only"
    assert item.text == ("자료에 나온 '수수료 검증 스크립트' 은 무엇이고 어떻게 다루나요? "
                         "문제가 생기면 어떻게 하나요?")
    assert [q.id for q in report.queued] == [item.id]


def test_mentioned_only_high_risk_counts_answer_mentions(
        ks: KnowledgeStore, sessions: SessionStore) -> None:
    _entity(ks, "로그 보관 배치", "job")
    _ended_session_with_answer(sessions, "s1", 1, "로그 보관 배치는 매일 돕니다.")
    _ended_session_with_answer(sessions, "s2", 2, "로그 보관 배치가 실패하면 재실행합니다.")
    report = _service(ks, sessions).refresh(LEAVER, ROLE)
    gap = report.gaps[0]
    assert gap.kind == "mentioned_only"
    assert gap.risk == "high"
    assert "3번" in gap.detail


def test_open_session_answers_are_not_counted(ks: KnowledgeStore, sessions: SessionStore) -> None:
    _entity(ks, "로그 보관 배치", "job")
    sessions.insert_session(SessionRow("s1", LEAVER, ROLE, 1, T0, T0, None, 0, None, []))
    sessions.add_answer(KBEntry(session_id="s1", question_id="sys-01", axis="systems",
                                question="질문", answer="로그 보관 배치 이야기", created_at=T0))
    report = _service(ks, sessions).refresh(LEAVER, ROLE)
    assert "1번" in report.gaps[0].detail


# ---- 모순
def test_contradiction_between_sessions_5s_vs_10s(ks: KnowledgeStore, sessions: SessionStore) -> None:
    _entity(ks, "승인 서버")
    c1 = ks.replace_session_cards("s1", [_new_card("s1", "승인 타임아웃은 5초입니다.", ["승인 서버"])], T0)
    c2 = ks.replace_session_cards("s2", [_new_card("s2", "승인 타임아웃은 10초입니다.", ["승인 서버"])], T0)
    report = _service(ks, sessions).refresh(LEAVER, ROLE)
    contradictions = [g for g in report.gaps if g.kind == "contradiction"]
    assert len(contradictions) == 1
    gap = contradictions[0]
    assert set(gap.card_ids) == {c1[0].id, c2[0].id}
    assert gap.topic == "승인 서버"
    assert gap.risk == "high"
    assert gap.entity_ids == [ks.list_entities(LEAVER)[0].id]
    item = ks.get_queue_item(gap.queue_item_id or "")
    assert item is not None and item.source == "gap" and item.axis == "routines"
    assert item.text.startswith("'승인 서버' 에 대해 세션마다 다르게 기록되어 있습니다.")
    # 두 세션에 근거가 있으므로 버스 팩터 아님
    assert "bus_factor" not in _kinds(report)


def test_contradiction_falls_back_to_fake_on_provider_error(
        ks: KnowledgeStore, sessions: SessionStore) -> None:
    class Failing(FakeLLM):
        def find_contradictions(self, cards: list[KnowledgeCard]) -> list[ContradictionDraft]:
            raise ProviderError("down")

    ks.replace_session_cards("s1", [_new_card("s1", "승인 타임아웃은 5초입니다.", ["승인 서버"])], T0)
    ks.replace_session_cards("s2", [_new_card("s2", "승인 타임아웃은 10초입니다.", ["승인 서버"])], T0)
    report = _service(ks, sessions, Failing()).refresh(LEAVER, ROLE)
    assert _kinds(report) == ["contradiction"]


# ---- 버스 팩터
def test_bus_factor_single_session_without_contact(ks: KnowledgeStore, sessions: SessionStore) -> None:
    _entity(ks, "정산 배치", "job", "src1")
    _entity(ks, "정산 배치", "job", "src2")
    cards = ks.replace_session_cards(
        "s1", [_new_card("s1", "정산 배치는 먼저 로그를 확인합니다.", ["정산 배치"])], T0)
    report = _service(ks, sessions).refresh(LEAVER, ROLE)
    assert _kinds(report) == ["bus_factor"]
    gap = report.gaps[0]
    assert gap.topic == "정산 배치"
    assert gap.risk == "medium"
    assert gap.detail == "기록이 한 세션에만 있고 대신 맡을 역할이 기록되지 않았습니다."
    assert gap.card_ids == [cards[0].id]
    item = ks.get_queue_item(gap.queue_item_id or "")
    assert item is not None and item.source == "gap" and item.axis == "routines"


def test_no_bus_factor_with_contact_card_or_single_mention(
        ks: KnowledgeStore, sessions: SessionStore) -> None:
    _entity(ks, "정산 배치", "job", "src1")
    _entity(ks, "정산 배치", "job", "src2")
    _entity(ks, "메시지 큐", "system", "src1")
    ks.replace_session_cards("s1", [
        _new_card("s1", "정산 배치는 먼저 로그를 확인합니다.", ["정산 배치"]),
        _new_card("s1", "정산 배치 문제는 결제팀 리드에게 연락합니다.", ["정산 배치"], type="contact"),
        _new_card("s1", "메시지 큐는 재시작합니다.", ["메시지 큐"]),
    ], T0)
    report = _service(ks, sessions).refresh(LEAVER, ROLE)
    assert report.gaps == []


def test_all_three_kinds_in_order(ks: KnowledgeStore, sessions: SessionStore) -> None:
    _entity(ks, "정산 배치", "job", "src1")
    _entity(ks, "정산 배치", "job", "src2")
    _entity(ks, "결제 대행사", "external")
    _entity(ks, "승인 서버")
    ks.replace_session_cards("s1", [
        _new_card("s1", "정산 배치는 먼저 로그를 확인합니다.", ["정산 배치"]),
        _new_card("s1", "승인 타임아웃은 5초입니다.", ["승인 서버"]),
    ], T0)
    ks.replace_session_cards("s2", [_new_card("s2", "승인 타임아웃은 10초입니다.", ["승인 서버"])], T0)
    report = _service(ks, sessions).refresh(LEAVER, ROLE)
    assert _kinds(report) == ["mentioned_only", "contradiction", "bus_factor"]
    assert report.gaps[0].topic == "결제 대행사"
    assert len(report.queued) == 3
    assert all(q.source == "gap" for q in report.queued)
    mo_item = ks.get_queue_item(report.gaps[0].queue_item_id or "")
    assert mo_item is not None and mo_item.axis == "people"


# ---- 재갱신
def test_refresh_twice_does_not_duplicate_queue(ks: KnowledgeStore, sessions: SessionStore) -> None:
    _entity(ks, "결제 대행사", "external")
    _entity(ks, "승인 서버")
    service = _service(ks, sessions)
    first = service.refresh(LEAVER, ROLE)
    second = service.refresh(LEAVER, ROLE)
    assert len(first.queued) == 2
    assert second.queued == []
    assert len(ks.list_queue(LEAVER, source="gap")) == 2
    assert [g.queue_item_id for g in second.gaps] == [g.queue_item_id for g in first.gaps]
    assert len(ks.list_gaps(LEAVER)) == 2


def test_refresh_replaces_previous_gaps(ks: KnowledgeStore, sessions: SessionStore) -> None:
    _entity(ks, "승인 서버")
    service = _service(ks, sessions)
    assert _kinds(service.refresh(LEAVER, ROLE)) == ["mentioned_only"]
    ks.replace_session_cards("s1", [_new_card("s1", "승인 서버는 재시작합니다.", ["승인 서버"]),
                                    _new_card("s1", "승인 서버 문의는 운영팀에게 합니다.",
                                              ["승인 서버"], type="contact")], T0)
    report = service.refresh(LEAVER, ROLE)
    assert report.gaps == []
    assert ks.list_gaps(LEAVER) == []


def test_report_and_queue_filters(ks: KnowledgeStore, sessions: SessionStore) -> None:
    _entity(ks, "승인 서버")
    ks.enqueue(LEAVER, ROLE, "successor", "systems", "후임 질문", "후임 미답 질문", None, T0)
    service = _service(ks, sessions)
    refreshed = service.refresh(LEAVER, ROLE)
    report = service.report(LEAVER)
    assert report.gaps == refreshed.gaps
    assert [q.source for q in report.queued] == ["gap"]
    assert [q.source for q in service.queue(LEAVER, None, None)] == ["successor", "gap"]
    assert [q.source for q in service.queue(LEAVER, None, "gap")] == ["gap"]
    assert service.queue(LEAVER, "answered", None) == []


# ---- fake·qwen 모순 탐지
def _kc(id: str, session_id: str, body: str, entities: list[str], type: str = "procedure") -> KnowledgeCard:
    return KnowledgeCard.model_validate({
        **_new_card(session_id, body, entities, type=type).model_dump(), "id": id, "created_at": T0})


def test_fake_find_contradictions_rules() -> None:
    cards = [
        _kc("c10", "s2", "승인 타임아웃은 10초입니다.", ["승인 서버", "결제 API"]),
        _kc("c2", "s1", "승인 타임아웃은 5초입니다.", ["승인 서버", "결제 API"]),
        _kc("c3", "s1", "승인 타임아웃은 7초입니다.", ["승인 서버"]),         # 같은 세션 c2 와는 쌍 아님
        _kc("c4", "s3", "승인 타임아웃은 5초, 재시도 10회입니다.", ["승인 서버"]),  # 숫자 겹침
        _kc("c5", "s3", "숫자 없는 카드입니다.", ["승인 서버"]),
        _kc("c6", "s4", "정산은 3시에 합니다.", ["정산 배치"]),               # 엔티티 교집합 없음
    ]
    drafts = fake_find_contradictions(cards)
    pairs = [d.card_ids for d in drafts]
    assert pairs == [["c2", "c10"], ["c3", "c4"], ["c3", "c10"]]
    assert drafts[0].subject == "결제 API"
    assert drafts[0].explanation == "세션 카드 c2 와 c10 의 숫자가 다릅니다"


def _chat_returning(content: str, calls: list[dict[str, Any]] | None = None) -> Any:
    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        if calls is not None:
            calls.append({"system": system, "user": user, "json_mode": json_mode})
        return content
    return chat


def test_qwen_find_contradictions_parses_and_validates() -> None:
    cards = [_kc("c1", "s1", "승인 타임아웃은 5초입니다.", ["승인 서버"]),
             _kc("c2", "s2", "승인 타임아웃은 10초입니다.", ["승인 서버"]),
             _kc("c3", "s2", "승인 서버 재시작 절차입니다.", ["승인 서버"])]
    content = json.dumps({"contradictions": [
        {"card_ids": ["c2", "c1"], "subject": "승인 서버", "explanation": "타임아웃 값이 다릅니다."},
        {"card_ids": ["c2", "c3"], "subject": "승인 서버", "explanation": "같은 세션"},
        {"card_ids": ["c1", "c99"], "subject": "x", "explanation": "없는 카드"},
        {"card_ids": ["c1"], "subject": "x", "explanation": "하나뿐"},
        {"card_ids": ["c1", "c2"], "subject": "승인 서버", "explanation": "중복"},
    ]}, ensure_ascii=False)
    calls: list[dict[str, Any]] = []
    drafts = qwen_find_contradictions(_chat_returning(content, calls), cards)
    assert [(d.card_ids, d.explanation) for d in drafts] == [(["c1", "c2"], "타임아웃 값이 다릅니다.")]
    assert calls[0]["json_mode"] is True
    assert "JSON" in calls[0]["system"] and "지어내지" in calls[0]["system"]
    assert "역할명" in calls[0]["system"]


def test_qwen_find_contradictions_invalid_json_falls_back_to_fake() -> None:
    cards = [_kc("c1", "s1", "승인 타임아웃은 5초입니다.", ["승인 서버"]),
             _kc("c2", "s2", "승인 타임아웃은 10초입니다.", ["승인 서버"])]
    for bad in ("not json", "[]", json.dumps({"other": []})):
        drafts = qwen_find_contradictions(_chat_returning(bad), cards)
        assert drafts == fake_find_contradictions(cards)


def test_qwen_find_contradictions_propagates_provider_error() -> None:
    cards = [_kc("c1", "s1", "a 5", ["x"]), _kc("c2", "s2", "b 10", ["x"])]

    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        raise ProviderError("down")

    with pytest.raises(ProviderError):
        qwen_find_contradictions(chat, cards)


def test_qwen_find_contradictions_single_session_skips_call() -> None:
    calls: list[dict[str, Any]] = []
    cards = [_kc("c1", "s1", "a 5", ["x"]), _kc("c2", "s1", "b 10", ["x"])]
    assert qwen_find_contradictions(_chat_returning("{}", calls), cards) == []
    assert calls == []


# ---- 라우터
def _client(service: Any, store: Any = None) -> TestClient:
    app = FastAPI()
    app.state.knowledge = SimpleNamespace(gaps=service, store=store)
    app.include_router(router)
    return TestClient(app)


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for v in value.values():
            keys |= _all_keys(v)
        return keys
    if isinstance(value, list):
        out: set[str] = set()
        for v in value:
            out |= _all_keys(v)
        return out
    return set()


def test_routes_refresh_report_queue(ks: KnowledgeStore, sessions: SessionStore) -> None:
    _entity(ks, "결제 대행사", "external")
    _entity(ks, "승인 서버")
    ks.replace_session_cards("s1", [_new_card("s1", "승인 타임아웃은 5초입니다.", ["승인 서버"])], T0)
    ks.replace_session_cards("s2", [_new_card("s2", "승인 타임아웃은 10초입니다.", ["승인 서버"])], T0)
    client = _client(_service(ks, sessions))

    r = client.post(f"/leavers/{LEAVER}/gaps/refresh", params={"role": ROLE})
    assert r.status_code == 200
    body = r.json()
    assert body["leaver"] == LEAVER
    assert {g["kind"] for g in body["gaps"]} == {"mentioned_only", "contradiction"}
    assert not _all_keys(body) & {"score", "person", "owner", "rank", "grade"}

    r = client.get(f"/leavers/{LEAVER}/gaps")
    assert r.status_code == 200
    assert r.json()["gaps"] == body["gaps"]
    assert not _all_keys(r.json()) & {"score", "person", "owner"}

    ks.mark_asked(body["queued"][0]["id"], "s9")
    r = client.get(f"/leavers/{LEAVER}/queue", params={"status": "pending"})
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1 and all(i["status"] == "pending" for i in items)
    r = client.get(f"/leavers/{LEAVER}/queue", params={"source": "gap"})
    ids = [i["id"] for i in r.json()]
    assert ids == sorted(ids, key=lambda s: int(s[1:])) and len(ids) == 2
    assert not _all_keys(r.json()) & {"score", "person", "owner"}


def test_routes_default_role_and_validation(ks: KnowledgeStore, sessions: SessionStore) -> None:
    _entity(ks, "승인 서버")
    client = _client(_service(ks, sessions))
    assert client.post(f"/leavers/{LEAVER}/gaps/refresh").status_code == 200
    assert ks.list_queue(LEAVER)[0].role == "backend_developer"
    assert client.get("/leavers/Bad Name/gaps").status_code == 422
    assert client.post("/leavers/BAD/gaps/refresh").status_code == 422
    assert client.get(f"/leavers/{LEAVER}/queue", params={"status": "done"}).status_code == 422
    assert client.get(f"/leavers/{LEAVER}/queue", params={"source": "x"}).status_code == 422
    empty = client.get("/leavers/nobody/gaps").json()
    assert empty == {"leaver": "nobody", "gaps": [], "queued": [], "unknown_areas": []}


def test_route_refresh_provider_error_is_503() -> None:
    class Down:
        def refresh(self, leaver: str, role: str) -> Any:
            raise ProviderError("down")

    r = _client(Down()).post(f"/leavers/{LEAVER}/gaps/refresh")
    assert r.status_code == 503
    assert r.json() == {"detail": "provider unavailable"}


# ---- 아무도 확실히 모르는 영역
def _uc(id: str, entities: list[str], cues: list[str], title: str = "카드",
        certainty: str = "uncertain") -> KnowledgeCard:
    return KnowledgeCard.model_validate({
        **_new_card("s1", "본문입니다.", entities, title=title).model_dump(), "id": id,
        "created_at": T0, "certainty": certainty, "uncertain_cues": cues})


def _uncertain_new_card(session_id: str, body: str, entities: list[str], cues: list[str],
                        title: str = "카드") -> NewCard:
    return _new_card(session_id, body, entities, title=title).model_copy(
        update={"certainty": "uncertain", "uncertain_cues": cues})


def test_unknown_areas_groups_same_entity() -> None:
    areas = unknown_areas([
        _uc("c3", ["정산 배치"], ["모르겠"]),
        _uc("c1", ["정산 배치", "결제 API"], ["전임자가", "아마"]),
    ])
    assert len(areas) == 1
    area = areas[0]
    assert area.topic == "정산 배치"
    assert area.card_ids == ["c1", "c3"]
    assert area.cues == ["아마", "모르겠", "전임자가"]
    assert area.detail == ("답변에 불확실 표현(아마, 모르겠, 전임자가)이 있어 "
                           "아무도 확실히 모르는 영역입니다.")


def test_unknown_areas_without_entities_uses_title() -> None:
    areas = unknown_areas([_uc("c2", [], ["확실하지 않"], title="야간 재처리 기준")])
    assert [(a.topic, a.card_ids, a.cues) for a in areas] == [
        ("야간 재처리 기준", ["c2"], ["확실하지 않"])]


def test_unknown_areas_empty_without_uncertain_cards() -> None:
    assert unknown_areas([]) == []
    assert unknown_areas([_uc("c1", ["승인 서버"], [], certainty="certain")]) == []


def test_unknown_areas_order_by_first_card_id_numeric() -> None:
    areas = unknown_areas([
        _uc("c10", ["B"], ["아마"]),
        _uc("c2", ["A"], ["아마"]),
        _uc("c9", ["B"], ["기억이 안"]),
        _uc("c5", ["승인 서버"], [], certainty="certain"),
    ])
    assert [(a.topic, a.card_ids) for a in areas] == [("A", ["c2"]), ("B", ["c9", "c10"])]
    assert areas[1].cues == ["아마", "기억이 안"]


def test_refresh_and_report_fill_unknown_areas_without_queue(
        ks: KnowledgeStore, sessions: SessionStore) -> None:
    _entity(ks, "정산 배치", "job")
    saved = ks.replace_session_cards("s1", [
        _uncertain_new_card("s1", "아마 새벽에 돕니다.", ["정산 배치"], ["아마"]),
        _uncertain_new_card("s1", "전임자가 만든 것 같습니다.", ["정산 배치"], ["전임자가"]),
        _new_card("s1", "실패하면 재실행합니다.", ["정산 배치"]),
    ], T0)
    service = _service(ks, sessions)
    report = service.refresh(LEAVER, ROLE)
    assert [(a.topic, a.card_ids, a.cues) for a in report.unknown_areas] == [
        ("정산 배치", [saved[0].id, saved[1].id], ["아마", "전임자가"])]
    assert all(q.reason.startswith("빈 구멍:") for q in ks.list_queue(LEAVER))
    assert service.report(LEAVER).unknown_areas == report.unknown_areas
    assert all(g.kind != "unknown" for g in ks.list_gaps(LEAVER))


def test_routes_unknown_areas_have_no_person_keys(ks: KnowledgeStore, sessions: SessionStore) -> None:
    ks.replace_session_cards("s1", [
        _uncertain_new_card("s1", "확실하지 않습니다.", [], ["확실하지 않"], title="권한 회수 기준")], T0)
    client = _client(_service(ks, sessions))
    body = client.post(f"/leavers/{LEAVER}/gaps/refresh", params={"role": ROLE}).json()
    assert [a["topic"] for a in body["unknown_areas"]] == ["권한 회수 기준"]
    assert body["queued"] == []
    got = client.get(f"/leavers/{LEAVER}/gaps").json()
    assert got["unknown_areas"] == body["unknown_areas"]
    forbidden = {"score", "person", "owner", "rank", "grade"}
    assert not _all_keys(body) & forbidden
    assert not _all_keys(got) & forbidden


# ---- 팀장 우선 질문
def _priority_client(ks: KnowledgeStore, sessions: SessionStore) -> TestClient:
    return _client(_service(ks, sessions), ks)


def test_priority_question_created_201_with_manager_source(
        ks: KnowledgeStore, sessions: SessionStore) -> None:
    client = _priority_client(ks, sessions)
    r = client.post(f"/leavers/{LEAVER}/priority-questions",
                    json={"text": "  배포 승인은 누가 하나요?  ", "axis": "people"})
    assert r.status_code == 201
    body = r.json()
    assert body["source"] == "manager"
    assert body["reason"] == "팀장 우선 질문"
    assert body["text"] == "배포 승인은 누가 하나요?"
    assert body["axis"] == "people"
    assert body["role"] == ROLE
    assert body["status"] == "pending"
    assert body["leaver"] == LEAVER
    assert body["ref_id"] is None
    assert [q.id for q in ks.list_queue(LEAVER, source="manager")] == [body["id"]]


def test_priority_question_duplicate_returns_existing_200(
        ks: KnowledgeStore, sessions: SessionStore) -> None:
    client = _priority_client(ks, sessions)
    first = client.post(f"/leavers/{LEAVER}/priority-questions", json={"text": "같은 질문"})
    again = client.post(f"/leavers/{LEAVER}/priority-questions", json={"text": "같은 질문"})
    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert len(ks.list_queue(LEAVER)) == 1


def test_priority_question_validation_422(ks: KnowledgeStore, sessions: SessionStore) -> None:
    client = _priority_client(ks, sessions)
    assert client.post(f"/leavers/{LEAVER}/priority-questions",
                       json={"text": "   "}).status_code == 422
    assert client.post(f"/leavers/{LEAVER}/priority-questions", json={}).status_code == 422
    assert client.post(f"/leavers/{LEAVER}/priority-questions",
                       json={"text": "x", "axis": "nope"}).status_code == 422
    assert client.post("/leavers/BAD/priority-questions",
                       json={"text": "x"}).status_code == 422
    assert ks.list_queue(LEAVER) == []


def test_priority_question_without_axis_uses_guess_axis(
        ks: KnowledgeStore, sessions: SessionStore) -> None:
    from handoff.verify import guess_axis

    client = _priority_client(ks, sessions)
    texts = ["장애가 나면 누구에게 연락하나요?", "배치 작업은 매일 언제 돌아가나요?", "아무 말"]
    for text in texts:
        r = client.post(f"/leavers/{LEAVER}/priority-questions",
                        json={"text": text, "role": "frontend_developer"})
        assert r.status_code == 201
        assert r.json()["axis"] == guess_axis(text)
        assert r.json()["role"] == "frontend_developer"


def test_next_pending_returns_manager_before_successor(
        ks: KnowledgeStore, sessions: SessionStore) -> None:
    ks.enqueue(LEAVER, ROLE, "successor", "systems", "후임 질문", "후임", None, T0)
    ks.enqueue(LEAVER, ROLE, "gap", "systems", "구멍 질문", "구멍", None, T0)
    r = _priority_client(ks, sessions).post(f"/leavers/{LEAVER}/priority-questions",
                                            json={"text": "팀장 질문"})
    nxt = ks.next_pending(LEAVER, ROLE)
    assert nxt is not None and nxt.id == r.json()["id"] and nxt.source == "manager"
    ks.mark_asked(nxt.id, "sess")
    nxt = ks.next_pending(LEAVER, ROLE)
    assert nxt is not None and nxt.source == "successor"
