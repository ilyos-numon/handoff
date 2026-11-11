"""Premortem: queue "what breaks first after I leave" questions for the leaver."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from handoff.contracts import PREMORTEM_QUESTIONS, PREMORTEM_REASON, QueueItem

if TYPE_CHECKING:
    from handoff.knowledge_store import KnowledgeStore


def utc_now() -> datetime:
    return datetime.now(UTC)


class PremortemService:
    def __init__(self, store: KnowledgeStore, clock: Callable[[], datetime] = utc_now) -> None:
        self._store = store
        self._clock = clock

    def enqueue(self, leaver: str, role: str) -> list[QueueItem]:
        now = self._clock()
        return [
            self._store.enqueue(leaver, role, "premortem", "incidents", text,
                                PREMORTEM_REASON, None, now)[0]
            for text in PREMORTEM_QUESTIONS
        ]
