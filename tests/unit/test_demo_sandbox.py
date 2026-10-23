"""Demo sandboxes: registry, scenario seed (steps 1-10) and expiry purge (fake provider)."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from handoff.config import Settings
from handoff.demo.sandbox import (
    SEED_ANSWERS,
    SEED_PRIORITY_QUESTION,
    SEED_ROLE,
    DemoSandboxes,
    Sandbox,
    SandboxLimitError,
    purge_all,
    seed_scenario,
)
from handoff.kb import KnowledgeBase
from handoff.knowledge_app import KnowledgeServices, build_knowledge
from handoff.providers import FakeEmbedder, FakeLLM
from handoff.session import SessionService
from handoff.store import SessionStore

T0 = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
TTL_S = 24 * 3600


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@dataclass
class World:
    sessions: SessionService
    knowledge: KnowledgeServices
    kb: KnowledgeBase
    store: SessionStore

    def seed(self, leaver: str) -> None:
        seed_scenario(self.sessions, self.knowledge, leaver)

    def purge(self, leaver: str) -> None:
        purge_all(self.kb, self.knowledge, leaver)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    settings = Settings(provider="fake", data_dir=tmp_path, min_score=0.25, collection="handoff_kb")
    llm = FakeLLM()
    store = SessionStore(":memory:")
    knowledge = build_knowledge(settings, llm, store)
    kb = KnowledgeBase(tmp_path / "qdrant", FakeEmbedder(), llm, settings.min_score,
                       settings.collection, verifier=knowledge.verifier)
    sessions = SessionService(kb, llm, store=store, hooks=knowledge.hooks)
    yield World(sessions=sessions, knowledge=knowledge, kb=kb, store=store)
    kb.close()
    store.close()


@pytest.fixture
def clock() -> Clock:
    return Clock(T0)


def _sandboxes(tmp_path: Path, clock: Clock, max_live: int = 5) -> DemoSandboxes:
    return DemoSandboxes(tmp_path / "demo.db", TTL_S, max_live, clock=clock)


def _has_data(world: World, leaver: str) -> bool:
    ks = world.knowledge.store
    return bool(
        ks.list_queue(leaver)
        or ks.list_sources(leaver)
        or world.store.list_sessions(leaver, SEED_ROLE)
        or world.kb.query("결제 대행사 장애가 나면 무엇을 먼저 확인하나요?", leaver=leaver).found
    )


def test_seed_answers_follow_scenario_steps_8_and_9() -> None:
    assert len(SEED_ANSWERS) == 3
    assert "010-1234-5678" in SEED_ANSWERS[0]
    assert SEED_ANSWERS[1].startswith("정산 배치는 매일 새벽 2시에")
    assert SEED_ANSWERS[2].startswith("금요일 배포는 피합니다.")
    assert SEED_PRIORITY_QUESTION == "결제 대행사 장애가 나면 가장 먼저 무엇을 확인하나요?"


def test_create_seeds_steps_1_to_10(tmp_path: Path, world: World, clock: Clock) -> None:
    box = _sandboxes(tmp_path, clock).create(seed=world.seed, purge=world.purge)
    assert isinstance(box, Sandbox)
    assert box.leaver.startswith("demo-")
    assert box.created_at == T0
    assert box.expires_at == T0 + timedelta(seconds=TTL_S)

    ks = world.knowledge.store
    manager = ks.list_queue(box.leaver, source="manager")
    assert [(q.text, q.status) for q in manager] == [(SEED_PRIORITY_QUESTION, "answered")]
    assert ks.list_queue(box.leaver, source="premortem")
    assert ks.list_queue(box.leaver, source="generated")
    assert ks.list_sources(box.leaver)

    coverage = world.sessions.coverage(box.leaver, SEED_ROLE)
    assert coverage.items_total == 3
    rows = world.store.list_sessions(box.leaver, SEED_ROLE)
    assert len(rows) == 1
    assert rows[0].ended_at is not None


def test_two_sandboxes_have_distinct_ids_and_separate_data(
    tmp_path: Path, world: World, clock: Clock
) -> None:
    boxes = _sandboxes(tmp_path, clock)
    a = boxes.create(seed=world.seed, purge=world.purge)
    b = boxes.create(seed=world.seed, purge=world.purge)
    assert a.leaver != b.leaver
    assert boxes.live_count() == 2
    assert all(q.leaver == a.leaver for q in world.knowledge.store.list_queue(a.leaver))

    boxes.delete(a.leaver, world.purge)
    assert boxes.get(a.leaver) is None
    assert not _has_data(world, a.leaver)
    assert _has_data(world, b.leaver)
    assert world.sessions.coverage(b.leaver, SEED_ROLE).items_total == 3


def test_get_returns_none_after_expiry(tmp_path: Path, world: World, clock: Clock) -> None:
    boxes = _sandboxes(tmp_path, clock)
    box = boxes.create(seed=lambda _lv: None, purge=world.purge)
    assert boxes.get(box.leaver) == box
    clock.now = box.expires_at - timedelta(seconds=1)
    assert boxes.get(box.leaver) is not None
    clock.now = box.expires_at
    assert boxes.get(box.leaver) is None
    assert boxes.get("demo-missing") is None


def test_purge_expired_removes_rows_and_data(tmp_path: Path, world: World, clock: Clock) -> None:
    boxes = _sandboxes(tmp_path, clock)
    old = boxes.create(seed=world.seed, purge=world.purge)
    clock.now = T0 + timedelta(hours=12)
    young = boxes.create(seed=lambda _lv: None, purge=world.purge)

    clock.now = T0 + timedelta(seconds=TTL_S)
    assert boxes.purge_expired(world.purge) == 1
    assert boxes.get(old.leaver) is None
    assert not _has_data(world, old.leaver)
    assert boxes.live_count() == 1

    clock.now = young.expires_at
    assert boxes.purge_expired(world.purge) == 1
    assert boxes.live_count() == 0
    assert boxes.purge_expired(world.purge) == 0


def test_create_purges_expired_before_limit_check(
    tmp_path: Path, world: World, clock: Clock
) -> None:
    purged: list[str] = []
    boxes = _sandboxes(tmp_path, clock, max_live=1)
    first = boxes.create(seed=lambda _lv: None, purge=purged.append)
    clock.now = first.expires_at
    second = boxes.create(seed=lambda _lv: None, purge=purged.append)
    assert purged == [first.leaver]
    assert boxes.live_count() == 1
    assert boxes.get(second.leaver) is not None


def test_max_live_raises_limit_error(tmp_path: Path, clock: Clock) -> None:
    seeded: list[str] = []
    boxes = _sandboxes(tmp_path, clock, max_live=2)
    boxes.create(seed=seeded.append, purge=lambda _lv: None)
    boxes.create(seed=seeded.append, purge=lambda _lv: None)
    with pytest.raises(SandboxLimitError):
        boxes.create(seed=seeded.append, purge=lambda _lv: None)
    assert len(seeded) == 2
    assert boxes.live_count() == 2


def test_seed_failure_purges_partial_data(tmp_path: Path, world: World, clock: Clock) -> None:
    started: list[str] = []

    def broken_seed(leaver: str) -> None:
        started.append(leaver)
        world.knowledge.sources.load_mock(leaver)
        world.knowledge.premortem.enqueue(leaver, SEED_ROLE)
        raise RuntimeError("seed exploded")

    boxes = _sandboxes(tmp_path, clock)
    with pytest.raises(RuntimeError, match="seed exploded"):
        boxes.create(seed=broken_seed, purge=world.purge)
    assert len(started) == 1
    assert boxes.live_count() == 0
    assert boxes.get(started[0]) is None
    assert not _has_data(world, started[0])


def test_registry_persists_without_visitor_address(tmp_path: Path, clock: Clock) -> None:
    boxes = _sandboxes(tmp_path, clock)
    box = boxes.create(seed=lambda _lv: None, purge=lambda _lv: None)
    reopened = _sandboxes(tmp_path, clock)
    assert reopened.get(box.leaver) == box
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "demo.db"))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(demo_sandboxes)").fetchall()]
    conn.close()
    assert cols == ["leaver", "created_at", "expires_at"]
