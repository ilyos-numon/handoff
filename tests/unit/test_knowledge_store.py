import threading
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
from handoff.kb import KBEntry
from handoff.knowledge_store import KnowledgeStore
from handoff.store import SessionRow, SessionStore

T0 = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
TABLES = {"k_sources", "k_entities", "k_queue", "k_depth", "k_cards", "k_gaps", "k_artifacts", "k_drills"}


@pytest.fixture
def ks() -> KnowledgeStore:
    return KnowledgeStore(*SessionStore(":memory:").shared())


def _card(session_id: str = "s1", type: str = "procedure", confidence: float = 0.8,
          leaver: str = "alice", title: str = "배포 절차") -> NewCard:
    return NewCard.model_validate({
        "leaver": leaver, "role": "backend_developer", "session_id": session_id, "type": type,
        "title": title, "body": "배포 전에 변경 목록을 확인합니다.", "entities": ["결제 API"],
        "confidence": confidence,
        "evidence": [Evidence(session_id=session_id, question_id="sys-01", session_number=1,
                              excerpt="변경 목록을 확인")],
    })


def _enqueue(ks: KnowledgeStore, text: str, source: str = "generated", leaver: str = "alice",
             role: str = "backend_developer") -> tuple[str, bool]:
    item, created = ks.enqueue(leaver, role, source, "systems", text, "이유", None, T0)  # type: ignore[arg-type]
    return item.id, created


# ---- 연결 공유
def test_shared_returns_session_store_connection_and_lock() -> None:
    store = SessionStore(":memory:")
    conn, lock = store.shared()
    assert conn is store._conn
    assert lock is store._lock
    assert isinstance(lock, type(threading.Lock()))


def test_from_session_store_uses_same_memory_connection() -> None:
    store = SessionStore(":memory:")
    ks = KnowledgeStore.from_session_store(store)
    conn, _ = store.shared()
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names
    assert {"sessions", "answers"} <= names
    ks.add_source("alice", "wiki", "제목", "본문", 0, T0)
    assert conn.execute("SELECT COUNT(*) FROM k_sources").fetchone()[0] == 1
    # session store still works on the same connection
    store.add_answer(KBEntry(session_id="s1", question_id="sys-01", axis="systems", question="질문?",
                             answer="답", created_at=T0, session_number=1))
    assert len(store.answers("s1")) == 1


def test_file_db_persists_and_tables_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "k.sqlite3"
    store = SessionStore(db)
    ks = KnowledgeStore.from_session_store(store)
    ks.add_source("alice", "cron", "크론", "0 3 * * * settle", 1, T0)
    store.close()
    store2 = SessionStore(db)
    ks2 = KnowledgeStore.from_session_store(store2)
    KnowledgeStore.from_session_store(store2)
    assert [s.title for s in ks2.list_sources("alice")] == ["크론"]
    store2.insert_session(SessionRow("s1", "alice", "backend_developer", 1, T0, T0, None, 0, None, []))
    store2.close()


# ---- 자료·엔티티
def test_add_and_list_sources(ks: KnowledgeStore) -> None:
    a = ks.add_source("alice", "wiki", "위키", "본문", 2, T0)
    ks.add_source("bob", "org", "조직", "역할", 0, T0)
    b = ks.add_source("alice", "runbook", "런북", "절차", 0, T0 + timedelta(seconds=1))
    assert a.id == "src1" and b.id == "src3"
    assert a.replaced == 2 and a.created_at == T0 and a.kind == "wiki"
    assert [s.id for s in ks.list_sources("alice")] == ["src1", "src3"]
    assert ks.list_sources("nobody") == []


def test_upsert_entity_merges_mentions_and_sources(ks: KnowledgeStore) -> None:
    e1 = ks.upsert_entity("alice", EntityDraft(name="정산 배치", type="job", mention="m", source_id="src1"))
    assert e1.id == "e1" and e1.mentions == 1 and e1.source_ids == ["src1"]
    e2 = ks.upsert_entity("alice", EntityDraft(name="정산 배치", type="system", mention="m", source_id="src1"))
    assert e2.id == "e1" and e2.mentions == 2 and e2.source_ids == ["src1"] and e2.type == "job"
    e3 = ks.upsert_entity("alice", EntityDraft(name="정산 배치", type="job", mention="m", source_id="src2"))
    assert e3.mentions == 3 and e3.source_ids == ["src1", "src2"]
    ks.upsert_entity("bob", EntityDraft(name="정산 배치", type="job", mention="m", source_id="src9"))
    ks.upsert_entity("alice", EntityDraft(name="결제 API", type="system", mention="m", source_id="src1"))
    assert [e.name for e in ks.list_entities("alice")] == ["정산 배치", "결제 API"]
    assert len(ks.list_entities("bob")) == 1


# ---- 큐
def test_enqueue_duplicate_returns_existing_false(ks: KnowledgeStore) -> None:
    item, created = ks.enqueue("alice", "backend_developer", "successor", "people", "누구에게 묻나요?",
                               "후임 미답", "c3", T0)
    assert created is True
    assert item.id == "q1" and item.status == "pending" and item.ref_id == "c3"
    assert item.asked_session_id is None and item.created_at == T0
    again, created2 = ks.enqueue("alice", "backend_developer", "gap", "systems", "누구에게 묻나요?",
                                 "다른 이유", None, T0)
    assert created2 is False
    assert again == item
    # other leaver is independent
    _, created3 = _enqueue(ks, "누구에게 묻나요?", leaver="bob")
    assert created3 is True


def test_enqueue_duplicate_while_asked_but_not_after_answered(ks: KnowledgeStore) -> None:
    qid, _ = _enqueue(ks, "질문 A")
    ks.mark_asked(qid, "s1")
    assert _enqueue(ks, "질문 A") == (qid, False)
    ks.mark_answered(qid, "s1")
    new_id, created = _enqueue(ks, "질문 A")
    assert created is True and new_id != qid


def test_get_queue_item_bad_ids(ks: KnowledgeStore) -> None:
    qid, _ = _enqueue(ks, "질문")
    assert ks.get_queue_item(qid) is not None
    for bad in ("q", "x1", "q1a", "1", "", "q99"):
        assert ks.get_queue_item(bad) is None


def test_list_queue_filters(ks: KnowledgeStore) -> None:
    a, _ = _enqueue(ks, "A", source="generated")
    b, _ = _enqueue(ks, "B", source="gap")
    c, _ = _enqueue(ks, "C", source="successor")
    ks.mark_asked(b, "s1")
    assert [q.id for q in ks.list_queue("alice")] == [a, b, c]
    assert [q.id for q in ks.list_queue("alice", status="pending")] == [a, c]
    assert [q.id for q in ks.list_queue("alice", source="gap")] == [b]
    assert ks.list_queue("alice", status="asked", source="generated") == []


def test_next_pending_priority_successor_gap_generated(ks: KnowledgeStore) -> None:
    g1, _ = _enqueue(ks, "생성 1", source="generated")
    gap1, _ = _enqueue(ks, "구멍 1", source="gap")
    gap2, _ = _enqueue(ks, "구멍 2", source="gap")
    s1, _ = _enqueue(ks, "후임 1", source="successor")
    _enqueue(ks, "다른 역할", source="successor", role="frontend_developer")
    nxt = ks.next_pending("alice", "backend_developer")
    assert nxt is not None and nxt.id == s1
    ks.mark_asked(s1, "sess")
    nxt = ks.next_pending("alice", "backend_developer")
    assert nxt is not None and nxt.id == gap1
    ks.mark_asked(gap1, "sess")
    nxt = ks.next_pending("alice", "backend_developer")
    assert nxt is not None and nxt.id == gap2
    ks.mark_answered(gap2, "sess")
    nxt = ks.next_pending("alice", "backend_developer")
    assert nxt is not None and nxt.id == g1
    ks.mark_asked(g1, "sess")
    assert ks.next_pending("alice", "backend_developer") is None
    assert ks.next_pending("bob", "backend_developer") is None


def test_mark_asked_answered_and_counts(ks: KnowledgeStore) -> None:
    a, _ = _enqueue(ks, "A")
    b, _ = _enqueue(ks, "B")
    ks.mark_asked(a, "s1")
    ks.mark_asked(b, "s1")
    ks.mark_answered(b, "s1")
    ia, ib = ks.get_queue_item(a), ks.get_queue_item(b)
    assert ia is not None and ia.status == "asked" and ia.asked_session_id == "s1"
    assert ib is not None and ib.status == "answered" and ib.answered_session_id == "s1"
    assert ks.count_asked_in_session("s1") == 2
    assert ks.count_asked_in_session("s2") == 0
    ks.mark_asked("bad", "s1")  # no-op


def test_reset_asked_only_this_session(ks: KnowledgeStore) -> None:
    a, _ = _enqueue(ks, "A")
    b, _ = _enqueue(ks, "B")
    c, _ = _enqueue(ks, "C")
    ks.mark_asked(a, "s1")
    ks.mark_asked(b, "s1")
    ks.mark_answered(b, "s1")
    ks.mark_asked(c, "s2")
    assert ks.reset_asked("s1") == 1
    ia = ks.get_queue_item(a)
    assert ia is not None and ia.status == "pending" and ia.asked_session_id is None
    ib = ks.get_queue_item(b)
    assert ib is not None and ib.status == "answered"
    ic = ks.get_queue_item(c)
    assert ic is not None and ic.status == "asked"
    assert ks.reset_asked("s1") == 0
    assert ks.count_asked_in_session("s1") == 1


# ---- 깊이
def test_depth_roundtrip(ks: KnowledgeStore) -> None:
    j = DepthJudgement(session_id="s1", question_id="sys-01",
                       slots={"why": "filled", "when": "missing", "who": "not_applicable",
                              "how": "filled", "fallback": "missing"},
                       missing=["when", "fallback"], follow_up_id="sys-01-s1", created_at=T0)
    j2 = j.model_copy(update={"question_id": "sys-02", "follow_up_id": None})
    ks.add_depth(j)
    ks.add_depth(j2)
    ks.add_depth(j.model_copy(update={"session_id": "s2"}))
    assert ks.list_depth("s1") == [j, j2]
    assert ks.list_depth("none") == []


# ---- 카드
def test_replace_session_cards_idempotent(ks: KnowledgeStore) -> None:
    other = ks.replace_session_cards("s2", [_card("s2")], T0)
    first = ks.replace_session_cards("s1", [_card(), _card(type="pitfall")], T0)
    assert [c.id for c in first] == ["c2", "c3"]
    assert first[0].evidence[0].excerpt == "변경 목록을 확인"
    assert first[0].entities == ["결제 API"] and first[0].created_at == T0
    second = ks.replace_session_cards("s1", [_card(), _card(type="pitfall")], T0)
    assert len(second) == 2
    assert [(c.type, c.title, c.body) for c in second] == [(c.type, c.title, c.body) for c in first]
    cards = ks.list_cards("alice")
    assert len(cards) == 3
    assert [c.session_id for c in cards].count("s1") == 2
    assert ks.get_card(other[0].id) == other[0]
    assert ks.get_card(first[0].id) is None
    assert ks.replace_session_cards("s1", [], T0) == []
    assert len(ks.list_cards("alice")) == 1


def test_list_cards_filters_and_get_card(ks: KnowledgeStore) -> None:
    cards = ks.replace_session_cards(
        "s1",
        [_card(confidence=0.6), _card(type="pitfall", confidence=0.8), _card(leaver="bob")],
        T0,
    )
    assert [c.id for c in ks.list_cards("alice")] == [cards[0].id, cards[1].id]
    assert [c.id for c in ks.list_cards("alice", type="pitfall")] == [cards[1].id]
    assert [c.id for c in ks.list_cards("alice", min_confidence=0.7)] == [cards[1].id]
    assert ks.list_cards("alice", type="contact") == []
    assert ks.get_card(cards[2].id) == cards[2]
    for bad in ("c", "q1", "c1x", "c999"):
        assert ks.get_card(bad) is None


# ---- 빈 구멍
def test_replace_and_list_gaps(ks: KnowledgeStore) -> None:
    g = NewGap(leaver="alice", kind="mentioned_only", topic="정산 배치", detail="자료에만 나옴",
               risk="high", entity_ids=["e1"], queue_item_id="q1")
    first = ks.replace_gaps("alice", [g, g.model_copy(update={"kind": "bus_factor", "risk": "low"})], T0)
    assert [x.id for x in first] == ["g1", "g2"]
    assert first[0].entity_ids == ["e1"] and first[0].card_ids == [] and first[0].queue_item_id == "q1"
    ks.replace_gaps("bob", [g.model_copy(update={"leaver": "bob"})], T0)
    second = ks.replace_gaps("alice", [g], T0)
    assert len(second) == 1 and second[0].topic == "정산 배치"
    assert ks.list_gaps("alice") == second
    assert len(ks.list_gaps("bob")) == 1


# ---- 산출물·훈련
def test_save_and_latest_artifact(ks: KnowledgeStore) -> None:
    sec = ArtifactSection(heading="배포", body="변경 목록 확인", card_ids=["c1"])
    art = NewArtifact(leaver="alice", kind="runbook", title="운영 런북", sections=[sec],
                      markdown="# 운영 런북", card_ids=["c1"])
    a1 = ks.save_artifact(art, T0)
    a2 = ks.save_artifact(art.model_copy(update={"markdown": "# v2"}), T0 + timedelta(minutes=1))
    ks.save_artifact(art.model_copy(update={"kind": "adr"}), T0)
    assert a1.id == "a1" and a1.sections == [sec] and a1.generated_at == T0
    assert ks.latest_artifact("alice", "runbook") == a2
    assert ks.latest_artifact("alice", "pitfalls_top10") is None
    assert ks.latest_artifact("bob", "runbook") is None


def test_add_and_get_drill(ks: KnowledgeStore) -> None:
    d = ks.add_drill(NewDrill(leaver="alice", title="훈련: 배포", situation="상황", ask="무엇부터?",
                              expected_points=["변경 목록 확인"], card_ids=["c1", "c2"]), T0)
    assert d.id == "d1" and d.card_ids == ["c1", "c2"] and d.expected_points == ["변경 목록 확인"]
    assert ks.get_drill("d1") == d
    for bad in ("d", "a1", "d2", "dx"):
        assert ks.get_drill(bad) is None
