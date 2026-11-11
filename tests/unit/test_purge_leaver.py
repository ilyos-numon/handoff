from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from handoff.contracts import (
    ArtifactSection,
    DepthJudgement,
    EntityDraft,
    Evidence,
    NewArtifact,
    NewCard,
    NewDrill,
    NewGap,
)
from handoff.kb import KBEntry, KnowledgeBase
from handoff.knowledge_store import KnowledgeStore, PurgeCounts
from handoff.providers import FakeEmbedder, FakeLLM
from handoff.store import SessionRow, SessionStore

T0 = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
ROLE = "backend_developer"
K_TABLES = ("k_sources", "k_entities", "k_queue", "k_depth", "k_cards", "k_gaps", "k_artifacts",
            "k_drills")


@pytest.fixture
def stores() -> tuple[SessionStore, KnowledgeStore]:
    store = SessionStore(":memory:")
    return store, KnowledgeStore.from_session_store(store)


@pytest.fixture
def kb(tmp_path: Path) -> Iterator[KnowledgeBase]:
    instance = KnowledgeBase(tmp_path / "qdrant", FakeEmbedder(), FakeLLM(), 0.0)
    yield instance
    instance.close()


def _entry(session_id: str, question_id: str, answer: str) -> KBEntry:
    return KBEntry(session_id=session_id, question_id=question_id, axis="systems",
                   question="배포는 어떻게 하나요?", answer=answer, created_at=T0, session_number=1)


def _seed(store: SessionStore, ks: KnowledgeStore, leaver: str) -> None:
    sid = f"{leaver}-s1"
    store.insert_session(SessionRow(
        session_id=sid, leaver=leaver, role=ROLE, number=1, started_at=T0,
        deadline=T0 + timedelta(seconds=900), ended_at=T0 + timedelta(seconds=600),
        node_index=0, current_json=None, summary=[],
    ))
    store.add_answer(_entry(sid, "sys-01", f"{leaver} 배포 절차 답변"))
    store.add_answer(_entry(sid, "sys-02", f"{leaver} 롤백 절차 답변"))
    src = ks.add_source(leaver, "wiki", "운영 문서", "본문", 0, T0)
    ks.upsert_entity(leaver, EntityDraft.model_validate(
        {"name": "결제 API", "type": "system", "mention": "결제 API", "source_id": src.id}))
    ks.enqueue(leaver, ROLE, "generated", "systems", "배포 질문", "이유", None, T0)  # type: ignore[arg-type]
    ks.add_depth(DepthJudgement.model_validate(
        {"session_id": sid, "question_id": "sys-01", "slots": {}, "missing": [],
         "follow_up_id": None, "created_at": T0}))
    cards = ks.replace_session_cards(sid, [NewCard.model_validate({
        "leaver": leaver, "role": ROLE, "session_id": sid, "type": "procedure",
        "title": "배포 절차", "body": "변경 목록을 확인합니다.", "entities": ["결제 API"],
        "confidence": 0.8,
        "evidence": [Evidence(session_id=sid, question_id="sys-01", session_number=1,
                              excerpt="변경 목록")],
    })], T0)
    ks.replace_gaps(leaver, [NewGap.model_validate(
        {"leaver": leaver, "kind": "mentioned_only", "topic": "장애", "detail": "없음", "risk": "high",
         "card_ids": [], "entity_ids": [], "queue_item_id": None})], T0)
    ks.save_artifact(NewArtifact.model_validate(
        {"leaver": leaver, "kind": "runbook", "title": "인수인계서",
         "sections": [ArtifactSection.model_validate(
             {"heading": "개요", "body": "본문", "card_ids": [cards[0].id]})],
         "markdown": "# 인수인계서", "card_ids": [cards[0].id]}), T0)
    ks.add_drill(NewDrill.model_validate(
        {"leaver": leaver, "title": "훈련", "situation": "상황", "ask": "질문",
         "expected_points": ["요점"], "card_ids": [cards[0].id]}), T0)


def _counts(ks: KnowledgeStore, store: SessionStore, leaver: str) -> dict[str, int]:
    conn, _ = store.shared()
    sids = [str(r[0]) for r in conn.execute(
        "SELECT session_id FROM sessions WHERE leaver = ?", (leaver,))]
    sid = f"{leaver}-s1"
    out = {
        "sessions": len(sids),
        "answers": int(conn.execute(
            "SELECT COUNT(*) FROM answers WHERE session_id = ?", (sid,)).fetchone()[0]),
        "k_depth": int(conn.execute(
            "SELECT COUNT(*) FROM k_depth WHERE session_id = ?", (sid,)).fetchone()[0]),
    }
    for table in K_TABLES:
        if table == "k_depth":
            continue
        out[table] = int(conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE leaver = ?", (leaver,)).fetchone()[0])
    return out


def _kb_seed(kb: KnowledgeBase, leaver: str) -> None:
    sid = f"{leaver}-s1"
    kb.add([_entry(sid, "sys-01", f"{leaver} 배포 절차 답변"),
            _entry(sid, "sys-02", f"{leaver} 롤백 절차 답변")], leaver=leaver)


def _kb_count(kb: KnowledgeBase) -> int:
    return kb._client.count(collection_name=kb._collection).count


def test_purge_removes_every_row_of_leaver(stores: tuple[SessionStore, KnowledgeStore]) -> None:
    store, ks = stores
    _seed(store, ks, "alice")
    before = _counts(ks, store, "alice")
    assert all(v > 0 for v in before.values()), before

    counts = ks.purge_leaver("alice")

    assert all(v == 0 for v in _counts(ks, store, "alice").values())
    assert counts == PurgeCounts(sessions=1, answers=2, knowledge_rows=sum(
        before[t] for t in K_TABLES))
    assert store.get_session("alice-s1") is None
    assert ks.list_cards("alice") == []
    assert ks.list_gaps("alice") == []
    assert ks.latest_artifact("alice", "runbook") is None


def test_purge_keeps_other_leaver(stores: tuple[SessionStore, KnowledgeStore]) -> None:
    store, ks = stores
    _seed(store, ks, "alice")
    _seed(store, ks, "bob")
    bob_before = _counts(ks, store, "bob")

    ks.purge_leaver("alice")

    assert _counts(ks, store, "bob") == bob_before
    assert store.get_session("bob-s1") is not None
    assert len(store.answers("bob-s1")) == 2
    assert len(ks.list_cards("bob")) == 1
    assert len(ks.list_depth("bob-s1")) == 1


def test_purge_unknown_leaver_is_zero(stores: tuple[SessionStore, KnowledgeStore]) -> None:
    store, ks = stores
    _seed(store, ks, "bob")
    bob_before = _counts(ks, store, "bob")

    assert ks.purge_leaver("nobody") == PurgeCounts(sessions=0, answers=0, knowledge_rows=0)
    assert _counts(ks, store, "bob") == bob_before


def test_purge_is_idempotent(stores: tuple[SessionStore, KnowledgeStore]) -> None:
    store, ks = stores
    _seed(store, ks, "alice")
    ks.purge_leaver("alice")
    assert ks.purge_leaver("alice") == PurgeCounts(sessions=0, answers=0, knowledge_rows=0)


def test_kb_purge_removes_only_leaver_points(kb: KnowledgeBase) -> None:
    _kb_seed(kb, "alice")
    _kb_seed(kb, "bob")
    assert _kb_count(kb) == 4

    kb.purge_leaver("alice")

    assert _kb_count(kb) == 2
    result = kb.query("배포 절차 답변", top_k=10)
    assert result.citations
    assert {c.session_id for c in result.citations} == {"bob-s1"}
    assert kb.query("배포 절차 답변", top_k=10, leaver="alice").found is False


def test_kb_purge_unknown_leaver_keeps_points(kb: KnowledgeBase) -> None:
    _kb_seed(kb, "bob")
    kb.purge_leaver("nobody")
    assert _kb_count(kb) == 2
