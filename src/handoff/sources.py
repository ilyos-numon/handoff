"""Work sources: anonymized upload, mock sources, entities and tailored questions."""

from __future__ import annotations

import builtins
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import yaml

from handoff.contracts import (
    NO_SOURCES,
    Entity,
    GenerateQuestionsRequest,
    GenerateQuestionsResponse,
    KnowledgeConflict,
    QueueItem,
    SourceDoc,
    SourceUploadRequest,
)
from handoff.knowledge_store import KnowledgeStore
from handoff.privacy import anonymize
from handoff.providers import LLM

MOCK_SOURCES_PATH = Path(__file__).parent / "mock_sources" / "backend_developer.yaml"


def utc_now() -> datetime:
    return datetime.now(UTC)


class SourceService:
    def __init__(
        self, llm: LLM, store: KnowledgeStore, clock: Callable[[], datetime] = utc_now
    ) -> None:
        self._llm = llm
        self._store = store
        self._clock = clock

    def upload(self, leaver: str, req: SourceUploadRequest) -> SourceDoc:
        title = anonymize(req.title, req.role_map)
        text = anonymize(req.text, req.role_map)
        return self._store.add_source(
            leaver, req.kind, title.text, text.text, title.replaced + text.replaced, self._clock()
        )

    def load_mock(self, leaver: str) -> list[SourceDoc]:
        with MOCK_SOURCES_PATH.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        role_map = {str(k): str(v) for k, v in (data.get("role_map") or {}).items()}
        return [
            self.upload(leaver, SourceUploadRequest(
                kind=d["kind"], title=d["title"], text=d["text"], role_map=role_map))
            for d in data["docs"]
        ]

    def list(self, leaver: str) -> list[SourceDoc]:
        return self._store.list_sources(leaver)

    def entities(self, leaver: str) -> builtins.list[Entity]:
        return self._store.list_entities(leaver)

    def generate(self, leaver: str, req: GenerateQuestionsRequest) -> GenerateQuestionsResponse:
        docs = self._store.list_sources(leaver)
        if not docs:
            raise KnowledgeConflict(NO_SOURCES)
        seen: set[tuple[str, str]] = set()
        for draft in self._llm.extract_entities(docs):
            key = (draft.name, draft.source_id)
            if key in seen:
                continue
            seen.add(key)
            self._store.upsert_entity(leaver, draft)
        entities = self._store.list_entities(leaver)
        ids = {e.name: e.id for e in entities}
        queued: builtins.list[QueueItem] = []
        for q in self._llm.generate_questions(entities, req.limit)[: req.limit]:
            ref_id = ids.get(q.entity_names[0]) if q.entity_names else None
            item, created = self._store.enqueue(
                leaver, req.role, "generated", q.axis, q.text,
                f"자료 엔티티: {', '.join(q.entity_names)}", ref_id, self._clock(),
            )
            if created:
                queued.append(item)
        return GenerateQuestionsResponse(entities=self._store.list_entities(leaver), queued=queued)
