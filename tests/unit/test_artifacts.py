import json
import math
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from handoff.artifacts import ArtifactService
from handoff.contracts import (
    ARTIFACT_CARD_TYPES,
    ARTIFACT_NOT_FOUND,
    ARTIFACT_TITLES,
    NO_CARDS,
    PROVIDER_UNAVAILABLE,
    ArtifactKind,
    ArtifactSection,
    Evidence,
    KnowledgeCard,
    KnowledgeConflict,
    KnowledgeNotFound,
    NewCard,
)
from handoff.knowledge_store import KnowledgeStore
from handoff.llm_tasks.artifacts import (
    EMPTY_WEEK_BODY,
    fake_write_artifact,
    qwen_write_artifact,
)
from handoff.providers import FakeLLM, ProviderError
from handoff.routes_artifacts import router
from handoff.store import SessionStore

T0 = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
KINDS: tuple[ArtifactKind, ...] = ("runbook", "onboarding_30d", "pitfalls_top10", "adr")


def _new(type: str, title: str, confidence: float = 0.6, body: str | None = None,
         leaver: str = "alice", session_id: str = "s1") -> NewCard:
    return NewCard.model_validate({
        "leaver": leaver, "role": "backend_developer", "session_id": session_id, "type": type,
        "title": title, "body": body or f"{title} 본문입니다.", "entities": ["정산 배치"],
        "confidence": confidence,
        "evidence": [Evidence(session_id=session_id, question_id="sys-01", session_number=1,
                              excerpt=title)],
    })


@pytest.fixture
def ks() -> KnowledgeStore:
    return KnowledgeStore(*SessionStore(":memory:").shared())


def _seed(ks: KnowledgeStore, cards: list[NewCard], session_id: str = "s1") -> list[KnowledgeCard]:
    return ks.replace_session_cards(session_id, cards, T0)


def _full(ks: KnowledgeStore) -> list[KnowledgeCard]:
    return _seed(ks, [
        _new("procedure", "배포 절차", 0.8),
        _new("pitfall", "월말 배치 조심", 0.6),
        _new("decision", "메시지 큐 선택", 0.8),
        _new("contact", "대행사 연락", 0.6),
        _new("entity", "정산 배치", 0.6),
        _new("pitfall", "수수료 검증 누락", 0.8),
    ])


class StubLLM(FakeLLM):
    def __init__(self, sections: list[ArtifactSection] | None = None,
                 error: bool = False) -> None:
        self.sections = sections or []
        self.error = error
        self.calls: list[tuple[str, list[str]]] = []

    def write_artifact(self, kind: ArtifactKind,
                       cards: list[KnowledgeCard]) -> list[ArtifactSection]:
        self.calls.append((kind, [c.id for c in cards]))
        if self.error:
            raise ProviderError("down")
        return self.sections


def _sec(heading: str, ids: list[str]) -> ArtifactSection:
    return ArtifactSection(heading=heading, body="내용", card_ids=ids)


# ---- fake_write_artifact

def test_fake_runbook_uses_procedure_and_pitfall_in_id_order(ks: KnowledgeStore) -> None:
    cards = _full(ks)
    secs = fake_write_artifact("runbook", cards)
    by_id = {c.id: c for c in cards}
    want = [c for c in cards if c.type in ("procedure", "pitfall")]
    assert [s.card_ids for s in secs] == [[c.id] for c in want]
    for s in secs:
        card = by_id[s.card_ids[0]]
        assert s.heading == card.title and s.body == card.body


def test_fake_runbook_caps_at_15(ks: KnowledgeStore) -> None:
    cards = _seed(ks, [_new("procedure", f"절차 {i}") for i in range(20)])
    assert len(fake_write_artifact("runbook", cards)) == 15


def test_fake_pitfalls_top10_sorted_by_confidence(ks: KnowledgeStore) -> None:
    cards = _seed(ks, [_new("pitfall", f"함정 {i}", 0.6 if i % 2 else 0.8) for i in range(12)]
                  + [_new("procedure", "절차")])
    secs = fake_write_artifact("pitfalls_top10", cards)
    assert len(secs) == 10
    pit = [c for c in cards if c.type == "pitfall"]
    ranked = sorted(pit, key=lambda c: (-c.confidence, int(c.id[1:])))[:10]
    assert [s.card_ids[0] for s in secs] == [c.id for c in ranked]
    assert [s.heading for s in secs] == [f"{i}. {c.title}" for i, c in enumerate(ranked, 1)]


def test_fake_adr_headings_and_body(ks: KnowledgeStore) -> None:
    cards = _full(ks)
    secs = fake_write_artifact("adr", cards)
    dec = [c for c in cards if c.type == "decision"]
    assert [s.heading for s in secs] == [f"결정 {i}: {c.title}" for i, c in enumerate(dec, 1)]
    assert secs[0].body == f"배경과 결정: {dec[0].body}"


def test_fake_onboarding_four_weeks_type_order(ks: KnowledgeStore) -> None:
    cards = _full(ks)  # 6 cards -> k=2 -> 2,2,2,0
    secs = fake_write_artifact("onboarding_30d", cards)
    assert [s.heading for s in secs] == ["1주차", "2주차", "3주차", "4주차"]
    order = {"entity": 0, "contact": 1, "procedure": 2, "pitfall": 3, "decision": 4}
    ordered = sorted(cards, key=lambda c: (order[c.type], int(c.id[1:])))
    k = math.ceil(len(ordered) / 4)
    for i, s in enumerate(secs[:3]):
        chunk = ordered[i * k:(i + 1) * k]
        assert s.card_ids == [c.id for c in chunk]
        assert s.body == "\n".join(f"- {c.title}" for c in chunk)
    assert secs[3].card_ids == [ordered[-1].id]
    assert secs[3].body == EMPTY_WEEK_BODY


@pytest.mark.parametrize("n,empty_weeks", [(1, 3), (2, 2), (3, 1)])
def test_fake_onboarding_few_cards_empty_weeks(ks: KnowledgeStore, n: int,
                                               empty_weeks: int) -> None:
    cards = _seed(ks, [_new("procedure", f"절차 {i}") for i in range(n)])
    secs = fake_write_artifact("onboarding_30d", cards)
    assert [s.heading for s in secs] == ["1주차", "2주차", "3주차", "4주차"]
    for i, s in enumerate(secs):
        if i < n:
            assert s.card_ids == [cards[i].id]
            assert s.body == f"- {cards[i].title}"
        else:
            assert s.card_ids == [cards[-1].id]
            assert s.body == EMPTY_WEEK_BODY
    assert sum(1 for s in secs if s.body == EMPTY_WEEK_BODY) == empty_weeks


def test_fake_no_target_cards_returns_empty(ks: KnowledgeStore) -> None:
    cards = _seed(ks, [_new("procedure", "절차")])
    assert fake_write_artifact("adr", cards) == []


# ---- qwen_write_artifact

def test_qwen_parses_sections(ks: KnowledgeStore) -> None:
    cards = _full(ks)
    seen: dict[str, object] = {}

    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        seen.update(system=system, user=user, json_mode=json_mode)
        return json.dumps({"sections": [
            {"heading": "배포", "body": "배포합니다", "card_ids": [cards[0].id]}]},
            ensure_ascii=False)

    secs = qwen_write_artifact(chat, "runbook", cards)
    assert secs == [ArtifactSection(heading="배포", body="배포합니다", card_ids=[cards[0].id])]
    assert seen["json_mode"] is True
    assert "JSON" in str(seen["system"]) and "지어내지" in str(seen["system"])
    assert cards[0].id in str(seen["user"])
    assert cards[2].id not in str(seen["user"])  # decision 카드는 런북 대상이 아니다


@pytest.mark.parametrize("out", ["not json", "[]", '{"sections": "x"}', '{"sections": []}',
                                 '{"sections": [{"heading": "a"}]}'])
def test_qwen_parse_failure_falls_back_to_fake(ks: KnowledgeStore, out: str) -> None:
    cards = _full(ks)

    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        return out

    assert qwen_write_artifact(chat, "adr", cards) == fake_write_artifact("adr", cards)


def test_qwen_provider_error_propagates(ks: KnowledgeStore) -> None:
    cards = _full(ks)

    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        raise ProviderError("down")

    with pytest.raises(ProviderError):
        qwen_write_artifact(chat, "runbook", cards)


# ---- ArtifactService

@pytest.mark.parametrize("kind", KINDS)
def test_generate_all_kinds_with_fake(ks: KnowledgeStore, kind: ArtifactKind) -> None:
    cards = _full(ks)
    svc = ArtifactService(FakeLLM(), ks, clock=lambda: T0)
    art = svc.generate("alice", kind)
    ids = {c.id for c in cards if c.type in ARTIFACT_CARD_TYPES[kind]}
    assert art.id.startswith("a") and art.kind == kind and art.leaver == "alice"
    assert art.title == ARTIFACT_TITLES[kind]
    assert art.generated_at == T0
    assert art.sections
    assert all(set(s.card_ids) <= ids for s in art.sections)
    assert set(art.card_ids) == {i for s in art.sections for i in s.card_ids}
    assert art.markdown.startswith(f"# {ARTIFACT_TITLES[kind]}\n\n")
    s0 = art.sections[0]
    assert f"## {s0.heading}\n\n{s0.body}\n\n근거 카드: {', '.join(s0.card_ids)}\n\n" in art.markdown
    assert svc.latest("alice", kind) == art


def test_generate_passes_only_target_type_cards(ks: KnowledgeStore) -> None:
    cards = _full(ks)
    llm = StubLLM([_sec("결정", [cards[2].id])])
    ArtifactService(llm, ks, clock=lambda: T0).generate("alice", "adr")
    assert llm.calls == [("adr", [cards[2].id])]


def test_generate_drops_sections_with_unknown_card_ids(ks: KnowledgeStore) -> None:
    cards = _full(ks)
    pid = cards[0].id
    llm = StubLLM([_sec("좋음", [pid]), _sec("없는 카드", [pid, "c999"]),
                   _sec("다른 종류", [cards[2].id])])
    art = ArtifactService(llm, ks, clock=lambda: T0).generate("alice", "runbook")
    assert [s.heading for s in art.sections] == ["좋음"]
    assert art.card_ids == [pid]
    assert "없는 카드" not in art.markdown


def test_generate_drops_other_leavers_card_ids(ks: KnowledgeStore) -> None:
    cards = _full(ks)
    other = _seed(ks, [_new("procedure", "남의 절차", leaver="bob")], session_id="s2")
    llm = StubLLM([_sec("남의 것", [other[0].id]), _sec("내 것", [cards[0].id])])
    art = ArtifactService(llm, ks, clock=lambda: T0).generate("alice", "runbook")
    assert [s.heading for s in art.sections] == ["내 것"]


def test_generate_all_sections_dropped_falls_back_to_fake(ks: KnowledgeStore) -> None:
    cards = _full(ks)
    llm = StubLLM([_sec("없음", ["c999"])])
    art = ArtifactService(llm, ks, clock=lambda: T0).generate("alice", "runbook")
    assert art.sections == fake_write_artifact("runbook", cards)


def test_generate_pitfalls_truncated_to_10(ks: KnowledgeStore) -> None:
    cards = _seed(ks, [_new("pitfall", f"함정 {i}") for i in range(3)])
    llm = StubLLM([_sec(f"{i}. 함정", [cards[i % 3].id]) for i in range(1, 13)])
    art = ArtifactService(llm, ks, clock=lambda: T0).generate("alice", "pitfalls_top10")
    assert len(art.sections) == 10


def test_generate_onboarding_wrong_structure_falls_back(ks: KnowledgeStore) -> None:
    cards = _full(ks)
    llm = StubLLM([_sec("1주차 시스템", [cards[4].id]), _sec("2주차", [cards[3].id]),
                   _sec("3주차", [cards[0].id])])
    art = ArtifactService(llm, ks, clock=lambda: T0).generate("alice", "onboarding_30d")
    assert art.sections == fake_write_artifact("onboarding_30d", cards)


def test_generate_onboarding_valid_structure_kept(ks: KnowledgeStore) -> None:
    cards = _full(ks)
    secs = [_sec(f"{i}주차: 익히기", [cards[i].id]) for i in range(1, 5)]
    art = ArtifactService(StubLLM(secs), ks, clock=lambda: T0).generate("alice", "onboarding_30d")
    assert art.sections == secs


def test_generate_onboarding_out_of_order_falls_back(ks: KnowledgeStore) -> None:
    cards = _full(ks)
    secs = [_sec(f"{i}주차", [cards[0].id]) for i in (2, 1, 3, 4)]
    art = ArtifactService(StubLLM(secs), ks, clock=lambda: T0).generate("alice", "onboarding_30d")
    assert [s.heading for s in art.sections] == ["1주차", "2주차", "3주차", "4주차"]
    assert art.sections == fake_write_artifact("onboarding_30d", cards)


def test_generate_no_cards_conflict(ks: KnowledgeStore) -> None:
    svc = ArtifactService(FakeLLM(), ks, clock=lambda: T0)
    with pytest.raises(KnowledgeConflict) as exc:
        svc.generate("alice", "runbook")
    assert str(exc.value) == NO_CARDS


def test_generate_no_cards_of_kind_conflict(ks: KnowledgeStore) -> None:
    _seed(ks, [_new("procedure", "절차")])
    svc = ArtifactService(FakeLLM(), ks, clock=lambda: T0)
    with pytest.raises(KnowledgeConflict):
        svc.generate("alice", "adr")


def test_generate_provider_error_propagates(ks: KnowledgeStore) -> None:
    _full(ks)
    svc = ArtifactService(StubLLM(error=True), ks, clock=lambda: T0)
    with pytest.raises(ProviderError):
        svc.generate("alice", "runbook")


def test_latest_missing_not_found(ks: KnowledgeStore) -> None:
    svc = ArtifactService(FakeLLM(), ks, clock=lambda: T0)
    with pytest.raises(KnowledgeNotFound) as exc:
        svc.latest("alice", "runbook")
    assert str(exc.value) == ARTIFACT_NOT_FOUND


def test_latest_returns_most_recent(ks: KnowledgeStore) -> None:
    _full(ks)
    svc = ArtifactService(FakeLLM(), ks, clock=lambda: T0)
    svc.generate("alice", "runbook")
    second = svc.generate("alice", "runbook")
    assert svc.latest("alice", "runbook").id == second.id


# ---- routes

def _client(ks: KnowledgeStore, llm: FakeLLM | None = None) -> TestClient:
    app = FastAPI()
    app.state.knowledge = SimpleNamespace(
        artifacts=ArtifactService(llm or FakeLLM(), ks, clock=lambda: T0))
    app.include_router(router)
    return TestClient(app)


def test_route_generate_runbook(ks: KnowledgeStore) -> None:
    cards = _full(ks)
    r = _client(ks).post("/leavers/alice/artifacts/runbook")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "runbook" and body["markdown"].startswith("# ")
    ids = {c.id for c in cards}
    assert body["sections"] and all(set(s["card_ids"]) <= ids for s in body["sections"])
    assert not {"score", "rank", "grade"} & set(body)


def test_route_onboarding_weeks(ks: KnowledgeStore) -> None:
    _full(ks)
    r = _client(ks).post("/leavers/alice/artifacts/onboarding_30d")
    assert r.status_code == 200
    heads = [s["heading"] for s in r.json()["sections"]]
    assert [h[:3] for h in heads] == ["1주차", "2주차", "3주차", "4주차"]


def test_route_get_latest(ks: KnowledgeStore) -> None:
    _full(ks)
    c = _client(ks)
    made = c.post("/leavers/alice/artifacts/adr").json()
    r = c.get("/leavers/alice/artifacts/adr")
    assert r.status_code == 200 and r.json()["id"] == made["id"]


def test_route_no_cards_409(ks: KnowledgeStore) -> None:
    r = _client(ks).post("/leavers/alice/artifacts/runbook")
    assert r.status_code == 409 and r.json()["detail"] == NO_CARDS


def test_route_missing_artifact_404(ks: KnowledgeStore) -> None:
    r = _client(ks).get("/leavers/alice/artifacts/runbook")
    assert r.status_code == 404 and r.json()["detail"] == ARTIFACT_NOT_FOUND


@pytest.mark.parametrize("method", ["post", "get"])
def test_route_bad_kind_422(ks: KnowledgeStore, method: str) -> None:
    _full(ks)
    r = getattr(_client(ks), method)("/leavers/alice/artifacts/handbook")
    assert r.status_code == 422


@pytest.mark.parametrize("method", ["post", "get"])
def test_route_bad_leaver_422(ks: KnowledgeStore, method: str) -> None:
    r = getattr(_client(ks), method)("/leavers/Alice!/artifacts/runbook")
    assert r.status_code == 422


def test_route_provider_error_503(ks: KnowledgeStore) -> None:
    _full(ks)
    r = _client(ks, StubLLM(error=True)).post("/leavers/alice/artifacts/runbook")
    assert r.status_code == 503 and r.json()["detail"] == PROVIDER_UNAVAILABLE
