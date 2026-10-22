import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from handoff.kb import KBEntry
from handoff.store import SessionRow, SessionStore

T0 = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)


def _row(session_id: str = "s1", number: int = 1, leaver: str = "alice",
         role: str = "backend_developer", ended: bool = False) -> SessionRow:
    return SessionRow(
        session_id=session_id,
        leaver=leaver,
        role=role,
        number=number,
        started_at=T0,
        deadline=T0 + timedelta(seconds=900),
        ended_at=T0 + timedelta(seconds=600) if ended else None,
        node_index=0,
        current_json='{"id": "q1"}',
        summary=["a", "b"] if ended else [],
    )


def _entry(session_id: str = "s1", question_id: str = "q1", answer: str = "ans",
           number: int | None = 1) -> KBEntry:
    return KBEntry(
        session_id=session_id,
        question_id=question_id,
        axis="systems",
        question="질문?",
        answer=answer,
        created_at=T0,
        session_number=number,
    )


def test_memory_roundtrip() -> None:
    store = SessionStore(":memory:")
    row = _row(ended=True)
    store.insert_session(row)
    assert store.get_session("s1") == row
    assert store.get_session("missing") is None
    store.close()


def test_update_session_replaces_all_fields() -> None:
    store = SessionStore(":memory:")
    store.insert_session(_row())
    updated = _row(ended=True)
    updated.node_index = 3
    updated.current_json = None
    store.update_session(updated)
    assert store.get_session("s1") == updated
    store.close()


def test_file_db_persists_after_reopen(tmp_path: Path) -> None:
    db = tmp_path / "handoff.db"
    store = SessionStore(db)
    store.insert_session(_row(ended=True))
    store.add_answer(_entry())
    store.close()

    reopened = SessionStore(str(db))
    assert reopened.get_session("s1") == _row(ended=True)
    assert reopened.answers("s1") == [_entry()]
    reopened.close()


def test_list_sessions_ordered_by_number() -> None:
    store = SessionStore(":memory:")
    store.insert_session(_row("s3", 3))
    store.insert_session(_row("s1", 1))
    store.insert_session(_row("s2", 2))
    store.insert_session(_row("other", 1, leaver="bob"))
    store.insert_session(_row("otherrole", 1, role="designer"))
    rows = store.list_sessions("alice", "backend_developer")
    assert [r.number for r in rows] == [1, 2, 3]
    assert [r.session_id for r in rows] == ["s1", "s2", "s3"]
    store.close()


def test_answers_in_insertion_order() -> None:
    store = SessionStore(":memory:")
    store.insert_session(_row())
    for qid in ["q3", "q1", "q2"]:
        store.add_answer(_entry(question_id=qid))
    store.add_answer(_entry(session_id="s2", question_id="q9"))
    assert [e.question_id for e in store.answers("s1")] == ["q3", "q1", "q2"]
    assert store.answers("none") == []
    store.close()


def test_leaver_answers_ended_only() -> None:
    store = SessionStore(":memory:")
    store.insert_session(_row("s1", 1, ended=True))
    store.insert_session(_row("s2", 2, ended=False))
    store.insert_session(_row("b1", 1, leaver="bob", ended=True))
    store.add_answer(_entry("s1", "q1", number=1))
    store.add_answer(_entry("s2", "q2", number=2))
    store.add_answer(_entry("b1", "q3", number=1))
    store.add_answer(_entry("s1", "q4", number=1))

    ended = store.leaver_answers("alice", "backend_developer", ended_only=True)
    assert [e.question_id for e in ended] == ["q1", "q4"]
    every = store.leaver_answers("alice", "backend_developer", ended_only=False)
    assert [e.question_id for e in every] == ["q1", "q2", "q4"]
    assert store.leaver_answers("alice", "designer", ended_only=False) == []
    store.close()


def test_timestamps_preserve_timezone() -> None:
    store = SessionStore(":memory:")
    kst = timezone(timedelta(hours=9))
    row = _row(ended=True)
    row.started_at = datetime(2026, 10, 23, 18, 0, tzinfo=kst)
    store.insert_session(row)
    got = store.get_session("s1")
    assert got is not None
    assert got.started_at == row.started_at
    assert got.started_at.utcoffset() == timedelta(hours=9)
    assert got.deadline.tzinfo is not None
    assert got.deadline.utcoffset() == timedelta(0)
    assert got.ended_at is not None and got.ended_at.utcoffset() == timedelta(0)

    entry = _entry()
    store.add_answer(entry)
    [back] = store.answers("s1")
    assert back.created_at == T0
    assert back.created_at.utcoffset() == timedelta(0)
    store.close()


def test_session_number_none_roundtrip() -> None:
    store = SessionStore(":memory:")
    store.add_answer(_entry(number=None))
    assert store.answers("s1")[0].session_number is None
    store.close()


def test_concurrent_add_answer_from_two_threads(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "c.db")
    store.insert_session(_row())
    errors: list[BaseException] = []

    def worker(prefix: str) -> None:
        try:
            for i in range(100):
                store.add_answer(_entry(question_id=f"{prefix}-{i}"))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(p,)) for p in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    got = store.answers("s1")
    assert len(got) == 200
    for prefix in ("a", "b"):
        ids = [e.question_id for e in got if e.question_id.startswith(prefix + "-")]
        assert ids == [f"{prefix}-{i}" for i in range(100)]
    store.close()
