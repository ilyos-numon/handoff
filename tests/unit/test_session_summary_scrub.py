from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from handoff.kb import KnowledgeBase
from handoff.privacy import EMAIL_MASK, PHONE_MASK
from handoff.providers import FakeEmbedder, FakeLLM
from handoff.question_tree import FollowUp
from handoff.session import SessionService
from handoff.store import SessionRow, SessionStore

ROLE = "backend_developer"
FIXED_NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
EMAIL = "ops@example.com"
PHONE = "010-1234-5678"
ANSWER_EMAIL = f"배포가 무서우면 {EMAIL} 으로 먼저 알립니다."
ANSWER_PHONE = f"급하면 {PHONE} 으로 전화합니다."


class NoFollowUpLLM(FakeLLM):
    def follow_up(
        self, question: str, answer: str, candidates: list[FollowUp]
    ) -> FollowUp | None:
        return None


@pytest.fixture
def kb(tmp_path: Path) -> Iterator[KnowledgeBase]:
    instance = KnowledgeBase(tmp_path / "qdrant", FakeEmbedder(), FakeLLM(), 0.25)
    yield instance
    instance.close()


def _assert_scrubbed(summary: list[str]) -> None:
    joined = "\n".join(summary)
    assert EMAIL not in joined
    assert PHONE not in joined
    assert EMAIL_MASK in joined
    assert PHONE_MASK in joined


def test_end_stores_scrubbed_summary(kb: KnowledgeBase) -> None:
    store = SessionStore(":memory:")
    service = SessionService(kb, NoFollowUpLLM(), clock=lambda: FIXED_NOW, store=store)
    session_id, _ = service.start(ROLE)
    service.answer(session_id, ANSWER_EMAIL)
    service.answer(session_id, ANSWER_PHONE)
    service.end(session_id)

    row = store.get_session(session_id)
    assert row is not None
    _assert_scrubbed(row.summary)
    _assert_scrubbed(service.view(session_id).summary)


def test_view_scrubs_raw_summary_already_in_store(kb: KnowledgeBase) -> None:
    store = SessionStore(":memory:")
    service = SessionService(kb, FakeLLM(), clock=lambda: FIXED_NOW, store=store)
    raw = [f"시스템: {ANSWER_EMAIL}", f"사람: {ANSWER_PHONE}", "-"]
    store.insert_session(
        SessionRow(
            session_id="a" * 32,
            leaver="default",
            role=ROLE,
            number=1,
            started_at=FIXED_NOW,
            deadline=FIXED_NOW + timedelta(seconds=900),
            ended_at=FIXED_NOW,
            node_index=1,
            current_json=None,
            summary=raw,
        )
    )

    summary = service.view("a" * 32).summary
    _assert_scrubbed(summary)
    assert summary[2] == "-"
    assert store.get_session("a" * 32) is not None
