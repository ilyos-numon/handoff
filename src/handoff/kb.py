"""Qdrant-backed knowledge base."""

import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from handoff.privacy import scrub_contacts
from handoff.providers import LLM, Embedder, ProviderError
from handoff.question_tree import Axis

NOT_FOUND_MESSAGE = "이 주제는 인수인계에 없습니다. 팀장에게 확인하세요."
COMPOSE_FAILED_MESSAGE = "답변을 정리하지 못했습니다. 아래 근거 발췌를 확인하세요."
EXCERPT_MAX = 200


class KBEntry(BaseModel):
    session_id: str
    question_id: str
    axis: Axis
    question: str
    answer: str
    created_at: datetime
    session_number: int | None = None


class Citation(BaseModel):
    session_id: str
    question_id: str
    axis: Axis
    excerpt: str
    score: float
    session_number: int | None = None
    created_at: datetime | None = None


class KBAnswer(BaseModel):
    found: bool
    answer: str
    citations: list[Citation]
    claims_removed: int = 0


Verifier = Callable[[str, str, list[Citation]], tuple[str | None, int]]


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
        embed_with_question: bool = False,
        verifier: Verifier | None = None,
    ) -> None:
        self._embedder = embedder
        self._llm = llm
        self._min_score = min_score
        self._collection = collection
        self._embed_with_question = embed_with_question
        self._verifier = verifier
        self._client = QdrantClient(path=str(store_path))
        if self._client.collection_exists(collection):
            vectors = self._client.get_collection(collection).config.params.vectors
            if isinstance(vectors, VectorParams) and vectors.size != embedder.dim:
                self._client.close()
                raise ValueError(
                    f"collection {collection} has dim {vectors.size}, "
                    f"embedder dim is {embedder.dim}"
                )
        else:
            self._client.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(size=embedder.dim, distance=Distance.COSINE),
            )

    def add(self, entries: list[KBEntry], leaver: str = "default") -> int:
        if not entries:
            return 0
        payloads: list[dict[str, object]] = []
        for e in entries:
            payload = e.model_dump(mode="json")
            payload["leaver"] = leaver
            payload["answer"] = scrub_contacts(e.answer)
            payload["question"] = scrub_contacts(e.question)
            payloads.append(payload)
        texts = [
            f"{p['question']}\n{p['answer']}" if self._embed_with_question else str(p["answer"])
            for p in payloads
        ]
        vectors = self._embedder.embed(texts)
        points = [
            PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{e.session_id}:{e.question_id}")),
                vector=vec,
                payload=payload,
            )
            for e, payload, vec in zip(entries, payloads, vectors)
        ]
        self._client.upsert(collection_name=self._collection, points=points)
        return len(points)

    def query(self, question: str, top_k: int = 3, leaver: str | None = None) -> KBAnswer:
        vector = self._embedder.embed([question])[0]
        if not any(vector):
            return _not_found()
        query_filter = (
            Filter(must=[FieldCondition(key="leaver", match=MatchValue(value=leaver))])
            if leaver is not None
            else None
        )
        result = self._client.query_points(
            collection_name=self._collection,
            query=vector,
            query_filter=query_filter,
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
                    excerpt=scrub_contacts(str(payload["answer"]))[:EXCERPT_MAX],
                    score=round(point.score, 4),
                    session_number=payload.get("session_number"),
                    created_at=payload.get("created_at"),
                )
            )
        if not citations:
            return _not_found()
        citations.sort(key=lambda c: c.score, reverse=True)
        try:
            answer = self._llm.compose_answer(question, citations)
        except ProviderError:
            return KBAnswer(found=True, answer=COMPOSE_FAILED_MESSAGE, citations=citations)
        if answer is None:
            return _not_found()
        answer = scrub_contacts(answer)
        if self._verifier is None:
            return KBAnswer(found=True, answer=answer, citations=citations)
        kept, removed = self._verifier(question, answer, citations)
        if kept is None:
            return KBAnswer(
                found=False, answer=NOT_FOUND_MESSAGE, citations=[], claims_removed=removed
            )
        return KBAnswer(
            found=True, answer=scrub_contacts(kept), citations=citations, claims_removed=removed
        )

    def close(self) -> None:
        self._client.close()
