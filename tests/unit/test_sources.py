import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from handoff.contracts import (
    ENTITY_TYPE_AXIS,
    NO_SOURCES,
    Entity,
    GenerateQuestionsRequest,
    KnowledgeConflict,
    SourceDoc,
    SourceUploadRequest,
)
from handoff.knowledge_store import KnowledgeStore
from handoff.llm_tasks.sources import (
    fake_extract_entities,
    fake_generate_questions,
    qwen_extract_entities,
    qwen_generate_questions,
)
from handoff.privacy import find_pii
from handoff.providers import FakeLLM, ProviderError
from handoff.routes_sources import router
from handoff.sources import MOCK_SOURCES_PATH, SourceService
from handoff.store import SessionStore

T0 = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
REQUIRED_NAMES = {
    "정산 배치", "결제 대행사", "승인 서버", "메시지 큐", "수수료 검증 스크립트",
    "정산 배치 재실행 스크립트", "대행사 장애 연락망", "로그 보관 배치",
}


@pytest.fixture
def ks() -> KnowledgeStore:
    return KnowledgeStore(*SessionStore(":memory:").shared())


@pytest.fixture
def service(ks: KnowledgeStore) -> SourceService:
    return SourceService(FakeLLM(), ks, clock=lambda: T0)


@pytest.fixture
def client(service: SourceService) -> Iterator[TestClient]:
    app = FastAPI()
    app.state.knowledge = SimpleNamespace(sources=service)
    app.include_router(router)
    with TestClient(app) as c:
        yield c


def _doc(kind: str, text: str, id: str = "src1", title: str = "t") -> SourceDoc:
    return SourceDoc.model_validate({"id": id, "leaver": "demo", "kind": kind, "title": title,
                                     "text": text, "replaced": 0, "created_at": T0})


def _entity(name: str, type: str, mentions: int = 1, id: str = "e1") -> Entity:
    return Entity.model_validate({"id": id, "leaver": "demo", "name": name, "type": type,
                                  "source_ids": ["src1"], "mentions": mentions})


# ---- spec scenarios (HTTP)

def test_upload_replaces_handle_and_email(client: TestClient) -> None:
    resp = client.post("/leavers/demo/sources", json={
        "kind": "runbook", "title": "장애 대응",
        "text": "장애 시 @pay-lead 에게 연락 (pay@example.com)",
        "role_map": {"@pay-lead": "결제팀 리드"},
    })
    assert resp.status_code == 201
    body = resp.json()
    assert "결제팀 리드" in body["text"] and "[이메일]" in body["text"]
    assert "@pay-lead" not in body["text"] and "pay@example.com" not in body["text"]
    assert body["replaced"] == 2
    assert body["id"].startswith("src")


def test_upload_rejects_unknown_kind(client: TestClient) -> None:
    resp = client.post("/leavers/demo/sources", json={"kind": "email", "title": "x", "text": "y"})
    assert resp.status_code == 422


def test_upload_rejects_blank_and_too_long_text(client: TestClient) -> None:
    blank = client.post("/leavers/demo/sources", json={"kind": "wiki", "title": "x", "text": "  "})
    long = client.post("/leavers/demo/sources",
                       json={"kind": "wiki", "title": "x", "text": "가" * 20001})
    assert blank.status_code == 422 and long.status_code == 422


def test_upload_rejects_bad_leaver(client: TestClient) -> None:
    resp = client.post("/leavers/Bad%20Name/sources",
                       json={"kind": "wiki", "title": "x", "text": "y"})
    assert resp.status_code == 422


def test_load_mock_then_list(client: TestClient) -> None:
    loaded = client.post("/leavers/demo/sources/mock")
    listed = client.get("/leavers/demo/sources")
    assert loaded.status_code == 201 and listed.status_code == 200
    for resp in (loaded, listed):
        docs = resp.json()
        assert len(docs) >= 6
        assert {d["kind"] for d in docs} == {"wiki", "runbook", "cron", "incident", "commit", "org"}
    assert len(loaded.json()) == len(listed.json())


def test_generate_from_mock(client: TestClient) -> None:
    client.post("/leavers/demo/sources/mock")
    resp = client.post("/leavers/demo/questions/generate",
                       json={"role": "backend_developer", "limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["entities"]) >= 3
    assert 1 <= len(body["queued"]) <= 5
    for item in body["queued"]:
        assert item["source"] == "generated" and item["status"] == "pending"
        assert item["role"] == "backend_developer"
        assert item["axis"] in {"systems", "routines", "incidents", "people"}
        assert item["reason"].startswith("자료 엔티티: ")
        assert item["ref_id"] is not None and item["ref_id"].startswith("e")
    entities = client.get("/leavers/demo/entities")
    assert entities.status_code == 200
    assert len(entities.json()) == len(body["entities"])


def test_generate_twice_no_duplicates(client: TestClient, ks: KnowledgeStore) -> None:
    client.post("/leavers/demo/sources/mock")
    first = client.post("/leavers/demo/questions/generate", json={"limit": 5})
    second = client.post("/leavers/demo/questions/generate", json={"limit": 5})
    assert first.status_code == 200 and second.status_code == 200
    texts = [q.text for q in ks.list_queue("demo")]
    assert len(texts) == len(set(texts))
    assert len(texts) >= 1


def test_generate_without_sources_is_409(client: TestClient) -> None:
    resp = client.post("/leavers/nobody/questions/generate", json={})
    assert resp.status_code == 409
    assert resp.json()["detail"] == NO_SOURCES


def test_generate_rejects_bad_limit(client: TestClient) -> None:
    resp = client.post("/leavers/demo/questions/generate", json={"limit": 0})
    assert resp.status_code == 422


def test_generate_provider_error_is_503(ks: KnowledgeStore) -> None:
    class Broken(FakeLLM):
        def extract_entities(self, docs: list[SourceDoc]) -> list:  # type: ignore[override,type-arg]
            raise ProviderError("down")

    app = FastAPI()
    svc = SourceService(Broken(), ks, clock=lambda: T0)
    app.state.knowledge = SimpleNamespace(sources=svc)
    app.include_router(router)
    svc.load_mock("demo")
    with TestClient(app) as c:
        resp = c.post("/leavers/demo/questions/generate", json={})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "provider unavailable"


# ---- service

def test_service_generate_no_sources_raises(service: SourceService) -> None:
    with pytest.raises(KnowledgeConflict) as exc:
        service.generate("demo", GenerateQuestionsRequest())
    assert str(exc.value) == NO_SOURCES


def test_service_upload_counts_title_and_text(service: SourceService) -> None:
    doc = service.upload("demo", SourceUploadRequest(
        kind="wiki", title="@ops-lead 메모", text="문의 010-1234-5678, @ops-lead",
        role_map={"@ops-lead": "운영 담당"}))
    assert doc.title == "운영 담당 메모"
    assert doc.text == "문의 [전화번호], 운영 담당"
    assert doc.replaced == 3
    assert doc.created_at == T0


def test_mock_sources_contract() -> None:
    data = yaml.safe_load(MOCK_SOURCES_PATH.read_text(encoding="utf-8"))
    role_map = data["role_map"]
    assert len(role_map) >= 3 and all(k.startswith("@") for k in role_map)
    docs = data["docs"]
    assert len(docs) >= 8
    assert {d["kind"] for d in docs} == {"wiki", "runbook", "cron", "incident", "commit", "org"}
    raw = "\n".join(d["text"] for d in docs)
    assert sum(raw.count(k) for k in role_map) >= 3
    assert "@example.com" in raw
    assert re.search(r"010-\d{4}-\d{4}", raw)
    for name in REQUIRED_NAMES:
        assert name in raw


def test_mock_sources_are_anonymized_and_cover_required_entities(
    service: SourceService, ks: KnowledgeStore
) -> None:
    docs = service.load_mock("demo")
    for d in docs:
        assert find_pii(d.text) == [] and find_pii(d.title) == []
    assert sum(d.replaced for d in docs) >= 5
    service.generate("demo", GenerateQuestionsRequest(limit=3))
    names = {e.name for e in ks.list_entities("demo")}
    assert REQUIRED_NAMES <= names


def test_generate_merges_entities_and_dedupes_within_call(ks: KnowledgeStore) -> None:
    svc = SourceService(FakeLLM(), ks, clock=lambda: T0)
    svc.upload("demo", SourceUploadRequest(kind="wiki", title="a", text="[[승인 서버]] 와 [[승인 서버]]"))
    svc.upload("demo", SourceUploadRequest(kind="runbook", title="b", text="[[승인 서버]] 재시작"))
    resp = svc.generate("demo", GenerateQuestionsRequest(limit=8))
    assert len(resp.entities) == 1
    ent = resp.entities[0]
    assert ent.mentions == 2 and ent.source_ids == ["src1", "src2"]
    assert [q.ref_id for q in resp.queued] == [ent.id]
    assert resp.queued[0].reason == "자료 엔티티: 승인 서버"


# ---- fake functions

def test_fake_extract_entities_rules() -> None:
    docs = [
        _doc("wiki", "[[승인 서버]] 는 [[메시지 큐]] 를 씁니다.\n다시 [[승인 서버]]", id="src1"),
        _doc("incident", "[[정산 지연]] 발생", id="src2"),
        _doc("cron", "# 주석 줄\n0 2 * * * 정산 배치\n\n30 4 * * 0 로그 보관 배치", id="src3"),
        _doc("org", "결제팀 리드: 정산 책임\n설명 없는 줄", id="src4"),
        _doc("commit", "fix: [[수수료 검증 스크립트]] 경계값", id="src5"),
    ]
    got = [(e.name, e.type, e.source_id) for e in fake_extract_entities(docs)]
    assert got == [
        ("승인 서버", "system", "src1"), ("메시지 큐", "system", "src1"),
        ("정산 지연", "incident", "src2"),
        ("정산 배치", "job", "src3"), ("로그 보관 배치", "job", "src3"),
        ("결제팀 리드", "role", "src4"),
        ("수수료 검증 스크립트", "system", "src5"),
    ]
    first = fake_extract_entities(docs)[0]
    assert first.mention == "[[승인 서버]] 는 [[메시지 큐]] 를 씁니다."


def test_fake_extract_entities_mention_truncated() -> None:
    line = "[[승인 서버]] " + "가" * 200
    (e,) = fake_extract_entities([_doc("wiki", line)])
    assert len(e.mention) == 120


def test_fake_generate_questions_order_and_templates() -> None:
    ents = [
        _entity("승인 서버", "system", 1, "e1"),
        _entity("정산 배치", "job", 3, "e2"),
        _entity("결제팀 리드", "role", 3, "e3"),
        _entity("정산 지연", "incident", 2, "e4"),
        _entity("운영 위키", "document", 1, "e5"),
        _entity("결제 대행사", "external", 1, "e6"),
    ]
    qs = fake_generate_questions(ents, 6)
    assert [q.entity_names for q in qs] == [
        ["결제팀 리드"], ["정산 배치"], ["정산 지연"], ["결제 대행사"], ["승인 서버"], ["운영 위키"]]
    texts = {q.entity_names[0]: q.text for q in qs}
    assert texts["승인 서버"] == "승인 서버 에서 가장 조심해야 하는 변경은 무엇이고, 왜 그런가요?"
    assert texts["정산 배치"] == "정산 배치 은 언제 돌고, 실패하면 어떻게 확인하고 복구하나요?"
    assert texts["정산 지연"] == "'정산 지연' 때 무엇이 원인이었고, 다시 나면 무엇부터 하나요?"
    assert texts["결제팀 리드"] == "결제팀 리드 에게는 어떤 일로, 어떤 경로로 연락하나요?"
    assert texts["운영 위키"] == "운영 위키 은 언제 보고, 어디가 실제와 다른가요?"
    assert texts["결제 대행사"] == "결제 대행사 쪽 문제가 생기면 어떤 역할에게 어떤 순서로 연락하나요?"
    for q, e in zip(qs, sorted(ents, key=lambda x: (-x.mentions, x.name)), strict=True):
        assert q.axis == ENTITY_TYPE_AXIS[e.type]
    assert len(fake_generate_questions(ents, 2)) == 2


# ---- qwen functions with a fake chat

class FakeChat:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[tuple[str, str, int, bool]] = []

    def __call__(self, system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        self.calls.append((system, user, max_tokens, json_mode))
        return self.reply


def test_qwen_extract_entities_parses_json() -> None:
    docs = [_doc("wiki", "[[승인 서버]] 설명", id="src1")]
    chat = FakeChat(json.dumps({"entities": [
        {"name": "승인 서버", "type": "system", "mention": "승인 서버 설명", "source_id": "src1"},
        {"name": "없는 자료", "type": "system", "mention": "x", "source_id": "src9"},
        {"name": "잘못된 타입", "type": "person", "mention": "x", "source_id": "src1"},
    ]}, ensure_ascii=False))
    got = qwen_extract_entities(chat, docs)
    assert [(e.name, e.type, e.source_id) for e in got] == [("승인 서버", "system", "src1")]
    system, user, _, json_mode = chat.calls[0]
    assert json_mode is True
    assert "JSON" in system and "역할명" in system and "지어내지" in system
    assert "src1" in user


@pytest.mark.parametrize("reply", ["not json", "[]", '{"entities": "x"}', '{"entities": []}'])
def test_qwen_extract_entities_falls_back_to_fake(reply: str) -> None:
    docs = [_doc("wiki", "[[승인 서버]] 설명", id="src1")]
    assert qwen_extract_entities(FakeChat(reply), docs) == fake_extract_entities(docs)


def test_qwen_extract_entities_propagates_provider_error() -> None:
    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        raise ProviderError("down")

    with pytest.raises(ProviderError):
        qwen_extract_entities(chat, [_doc("wiki", "[[a]]")])


def test_qwen_generate_questions_parses_json() -> None:
    ents = [_entity("승인 서버", "system", 2, "e1"), _entity("정산 배치", "job", 1, "e2")]
    chat = FakeChat(json.dumps({"questions": [
        {"text": "승인 서버 배포 전 무엇을 확인하나요?", "axis": "systems", "entity_names": ["승인 서버"]},
        {"text": "모르는 엔티티 질문", "axis": "systems", "entity_names": ["없는 것"]},
        {"text": "축이 틀림", "axis": "money", "entity_names": ["정산 배치"]},
        {"text": "정산 배치 실패 시 복구는?", "axis": "routines", "entity_names": ["정산 배치"]},
        {"text": "한도 초과", "axis": "routines", "entity_names": ["정산 배치"]},
    ]}, ensure_ascii=False))
    got = qwen_generate_questions(chat, ents, 2)
    assert [(q.text, q.axis, q.entity_names) for q in got] == [
        ("승인 서버 배포 전 무엇을 확인하나요?", "systems", ["승인 서버"]),
        ("정산 배치 실패 시 복구는?", "routines", ["정산 배치"]),
    ]
    assert chat.calls[0][3] is True


@pytest.mark.parametrize("reply", ["{broken", '{"questions": null}', '{"questions": []}'])
def test_qwen_generate_questions_falls_back_to_fake(reply: str) -> None:
    ents = [_entity("승인 서버", "system", 2, "e1")]
    assert qwen_generate_questions(FakeChat(reply), ents, 3) == fake_generate_questions(ents, 3)
