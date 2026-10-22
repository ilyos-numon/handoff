"""Qdrant-backed knowledge base."""

import uuid
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from handoff.providers import LLM, Embedder
from handoff.question_tree import Axis

NOT_FOUND_MESSAGE = "이 주제는 인수인계에 없습니다. 팀장에게 확인하세요."
EXCERPT_MAX = 200


class KBEntry(BaseModel):
    session_id: str
    question_id: str
    axis: Axis
    question: str
    answer: str
    created_at: datetime


class Citation(BaseModel):
    session_id: str
    question_id: str
    axis: Axis
    excerpt: str
    score: float


class KBAnswer(BaseModel):
    found: bool
    answer: str
    citations: list[Citation]


def _not_found() -> KBAnswer:
    return KBAnswer(found=False, answer=NOT_FOUND_MESSAGE, citations=[])


class KnowledgeBase:
    def __init__(
        self,
        store_path: Path,
        embedder: Embedder,
        llm: LLM,
        min_score: float,
        collection: str = "handoff_kb",
    ) -> None:
        self._embedder = embedder
        self._llm = llm
        self._min_score = min_score
        self._collection = collection
        self._client = QdrantClient(path=str(store_path))
        if not self._client.collection_exists(collection):
            self._client.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(size=embedder.dim, distance=Distance.COSINE),
            )

    def add(self, entries: list[KBEntry]) -> int:
        if not entries:
            return 0
        vectors = self._embedder.embed([e.answer for e in entries])
        points = [
            PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{e.session_id}:{e.question_id}")),
                vector=vec,
                payload=e.model_dump(mode="json"),
            )
            for e, vec in zip(entries, vectors)
        ]
        self._client.upsert(collection_name=self._collection, points=points)
        return len(points)

    def query(self, question: str, top_k: int = 3) -> KBAnswer:
        vector = self._embedder.embed([question])[0]
        if not any(vector):
            return _not_found()
        result = self._client.query_points(
            collection_name=self._collection,
            query=vector,
            limit=top_k,
            with_payload=True,
        )
        citations: list[Citation] = []
        for point in result.points:
            if point.score < self._min_score:
                continue
            payload = point.payload or {}
            citations.append(
                Citation(
                    session_id=payload["session_id"],
                    question_id=payload["question_id"],
                    axis=payload["axis"],
                    excerpt=str(payload["answer"])[:EXCERPT_MAX],
                    score=round(point.score, 4),
                )
            )
        if not citations:
            return _not_found()
        citations.sort(key=lambda c: c.score, reverse=True)
        return KBAnswer(
            found=True,
            answer=self._llm.compose_answer(question, citations),
            citations=citations,
        )

    def close(self) -> None:
        self._client.close()
