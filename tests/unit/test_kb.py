import math
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from qdrant_client.models import PointStruct

from handoff.config import Settings
from handoff.kb import (
    COMPOSE_FAILED_MESSAGE,
    NOT_FOUND_MESSAGE,
    Citation,
    KBEntry,
    KnowledgeBase,
)
from handoff.providers import EMBED_DIM, FakeEmbedder, FakeLLM, ProviderError, build_providers

ANSWER_MAIN = "금요일 배포는 피합니다. 작년 3월 금요일 저녁 배포 후 정산 배치가 주말 내내 실패했습니다."
ANSWER_FOLLOW = "복구는 큐를 비우고 정산 배치를 수동으로 재실행했습니다."
QUERY_HIT = "금요일에 배포하면 왜 안 되나요?"
QUERY_MISS = "PG사 장애 나면 누구한테 연락해요?"

MIN_SCORE = 0.25
SESSION_ID = "sess-1"


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _entries() -> list[KBEntry]:
    now = datetime(2026, 10, 23, tzinfo=UTC)
    return [
        KBEntry(
            session_id=SESSION_ID,
            question_id="inc-01",
            axis="incidents",
            question="가장 기억에 남는 장애는?",
            answer=ANSWER_MAIN,
            created_at=now,
        ),
        KBEntry(
            session_id=SESSION_ID,
            question_id="inc-01-f1",
            axis="incidents",
            question="어떻게 복구했나요?",
            answer=ANSWER_FOLLOW,
            created_at=now,
        ),
    ]


@pytest.fixture
def kb(tmp_path: Path) -> Iterator[KnowledgeBase]:
    instance = KnowledgeBase(tmp_path / "qdrant", FakeEmbedder(), FakeLLM(), MIN_SCORE)
    yield instance
    instance.close()


@pytest.fixture
def loaded_kb(kb: KnowledgeBase) -> KnowledgeBase:
    kb.add(_entries())
    return kb


def _count(kb: KnowledgeBase) -> int:
    return kb._client.count(collection_name=kb._collection).count


def test_embed_deterministic() -> None:
    emb = FakeEmbedder()
    assert emb.embed([ANSWER_MAIN]) == emb.embed([ANSWER_MAIN])


def test_embed_dim_and_norm() -> None:
    emb = FakeEmbedder()
    assert emb.dim == EMBED_DIM == 1024
    (vec,) = emb.embed([ANSWER_MAIN])
    assert len(vec) == 1024
    assert math.isclose(math.sqrt(sum(v * v for v in vec)), 1.0, rel_tol=1e-9)


def test_embed_empty_is_zero_vector() -> None:
    (vec,) = FakeEmbedder().embed([""])
    assert len(vec) == 1024
    assert all(v == 0.0 for v in vec)


def test_cosine_hit_above_threshold() -> None:
    hit, main = FakeEmbedder().embed([QUERY_HIT, ANSWER_MAIN])
    assert _cos(hit, main) >= 0.30


def test_cosine_miss_below_bound() -> None:
    miss, main, follow = FakeEmbedder().embed([QUERY_MISS, ANSWER_MAIN, ANSWER_FOLLOW])
    assert _cos(miss, main) <= 0.10
    assert _cos(miss, follow) <= 0.10


def test_add_returns_count(kb: KnowledgeBase) -> None:
    assert kb.add(_entries()) == 2
    assert _count(kb) == 2


def test_query_hit(loaded_kb: KnowledgeBase) -> None:
    result = loaded_kb.query(QUERY_HIT)
    assert result.found is True
    assert result.citations
    first = result.citations[0]
    assert first.question_id == "inc-01"
    assert first.session_id == SESSION_ID
    assert "금요일" in first.excerpt
    assert result.answer == f"인수인계 기록에 따르면: {first.excerpt}"
    scores = [c.score for c in result.citations]
    assert scores == sorted(scores, reverse=True)
    assert all(c.score >= MIN_SCORE for c in result.citations)
    assert all(round(c.score, 4) == c.score for c in result.citations)


def test_query_miss(loaded_kb: KnowledgeBase) -> None:
    result = loaded_kb.query(QUERY_MISS)
    assert result.found is False
    assert result.answer == NOT_FOUND_MESSAGE
    assert result.citations == []


def test_query_empty_kb(kb: KnowledgeBase) -> None:
    result = kb.query(QUERY_HIT)
    assert result.found is False
    assert result.answer == NOT_FOUND_MESSAGE
    assert result.citations == []


def test_query_zero_vector_not_found(loaded_kb: KnowledgeBase) -> None:
    result = loaded_kb.query("")
    assert result.found is False
    assert result.citations == []


def test_query_repeat_identical(loaded_kb: KnowledgeBase) -> None:
    assert loaded_kb.query(QUERY_HIT) == loaded_kb.query(QUERY_HIT)


def test_readd_same_ids_no_growth(kb: KnowledgeBase) -> None:
    kb.add(_entries())
    kb.add(_entries())
    assert _count(kb) == 2


def test_excerpt_truncated(kb: KnowledgeBase) -> None:
    long_answer = "금요일 배포 " * 100
    entry = _entries()[0].model_copy(update={"answer": long_answer})
    kb.add([entry])
    result = kb.query(QUERY_HIT)
    assert result.found is True
    assert len(result.citations[0].excerpt) == 200


def test_build_providers_fake() -> None:
    settings = Settings(provider="fake", data_dir=Path("."), min_score=0.25, collection="c")
    embedder, llm = build_providers(settings)
    assert isinstance(embedder, FakeEmbedder)
    assert isinstance(llm, FakeLLM)


def test_build_providers_unknown_raises() -> None:
    settings = Settings(provider="x", data_dir=Path("."), min_score=0.25, collection="c")
    with pytest.raises(ValueError, match="HANDOFF_PROVIDER"):
        build_providers(settings)


class NoneComposeLLM(FakeLLM):
    def compose_answer(self, question: str, citations: list[Citation]) -> str | None:
        return None


class FailingComposeLLM(FakeLLM):
    def compose_answer(self, question: str, citations: list[Citation]) -> str | None:
        raise ProviderError("compose failed")


class FailingEmbedder(FakeEmbedder):
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise ProviderError("embed failed")


class RecordingEmbedder(FakeEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return super().embed(texts)


class SmallEmbedder(FakeEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.dim = 8


def test_compose_none_downgrades_to_not_found(tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path / "qdrant", FakeEmbedder(), NoneComposeLLM(), MIN_SCORE)
    try:
        kb.add(_entries())
        result = kb.query(QUERY_HIT)
        assert result.found is False
        assert result.answer == NOT_FOUND_MESSAGE
        assert result.citations == []
    finally:
        kb.close()


def test_compose_provider_error_keeps_citations(tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path / "qdrant", FakeEmbedder(), FailingComposeLLM(), MIN_SCORE)
    try:
        kb.add(_entries())
        result = kb.query(QUERY_HIT)
        assert result.found is True
        assert result.answer == COMPOSE_FAILED_MESSAGE
        assert result.citations
        assert result.citations[0].question_id == "inc-01"
    finally:
        kb.close()


def test_embed_provider_error_propagates_on_query(tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path / "qdrant", FailingEmbedder(), FakeLLM(), MIN_SCORE)
    try:
        with pytest.raises(ProviderError):
            kb.query(QUERY_HIT)
    finally:
        kb.close()


def test_embed_provider_error_propagates_on_add(tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path / "qdrant", FailingEmbedder(), FakeLLM(), MIN_SCORE)
    try:
        with pytest.raises(ProviderError):
            kb.add(_entries())
    finally:
        kb.close()


def test_dim_mismatch_raises(tmp_path: Path) -> None:
    first = KnowledgeBase(tmp_path / "qdrant", FakeEmbedder(), FakeLLM(), MIN_SCORE, "c1")
    first.close()
    with pytest.raises(ValueError) as excinfo:
        KnowledgeBase(tmp_path / "qdrant", SmallEmbedder(), FakeLLM(), MIN_SCORE, "c1")
    assert str(excinfo.value) == "collection c1 has dim 1024, embedder dim is 8"


def test_dim_match_reopens(tmp_path: Path) -> None:
    first = KnowledgeBase(tmp_path / "qdrant", FakeEmbedder(), FakeLLM(), MIN_SCORE)
    first.add(_entries())
    first.close()
    second = KnowledgeBase(tmp_path / "qdrant", FakeEmbedder(), FakeLLM(), MIN_SCORE)
    try:
        assert _count(second) == 2
    finally:
        second.close()


def test_embed_input_answer_only_by_default(tmp_path: Path) -> None:
    emb = RecordingEmbedder()
    kb = KnowledgeBase(tmp_path / "qdrant", emb, FakeLLM(), MIN_SCORE)
    try:
        kb.add(_entries())
        assert emb.calls[-1] == [ANSWER_MAIN, ANSWER_FOLLOW]
    finally:
        kb.close()


def test_embed_with_question(tmp_path: Path) -> None:
    emb = RecordingEmbedder()
    kb = KnowledgeBase(
        tmp_path / "qdrant", emb, FakeLLM(), MIN_SCORE, embed_with_question=True
    )
    try:
        entries = _entries()
        kb.add(entries)
        assert emb.calls[-1] == [f"{e.question}\n{e.answer}" for e in entries]
    finally:
        kb.close()


def test_citation_session_number_and_created_at(kb: KnowledgeBase) -> None:
    entries = [e.model_copy(update={"session_number": 2}) for e in _entries()]
    kb.add(entries)
    result = kb.query(QUERY_HIT)
    assert result.found is True
    first = result.citations[0]
    assert first.session_number == 2
    assert first.created_at == datetime(2026, 10, 23, tzinfo=UTC)


def test_citation_session_number_missing_is_none(loaded_kb: KnowledgeBase) -> None:
    result = loaded_kb.query(QUERY_HIT)
    assert result.citations[0].session_number is None


CONTACT_ANSWER = "금요일 배포 장애 때는 010-1234-5678 또는 ops.lead@example.com 으로 연락했습니다."
OTHER_ANSWER = "금요일 배포는 다른 팀 기준으로 오후에만 합니다."


def _payloads(kb: KnowledgeBase) -> list[dict[str, object]]:
    points, _ = kb._client.scroll(collection_name=kb._collection, with_payload=True)
    return [dict(p.payload or {}) for p in points]


def test_add_default_leaver_in_payload(loaded_kb: KnowledgeBase) -> None:
    assert {p["leaver"] for p in _payloads(loaded_kb)} == {"default"}


def test_add_stores_leaver_in_payload(kb: KnowledgeBase) -> None:
    kb.add(_entries(), "kim")
    assert {p["leaver"] for p in _payloads(kb)} == {"kim"}


def _two_leavers(kb: KnowledgeBase) -> None:
    kb.add(_entries(), "kim")
    other = _entries()[0].model_copy(
        update={"session_id": "sess-lee", "question_id": "inc-09", "answer": OTHER_ANSWER}
    )
    kb.add([other], "lee")


def test_query_scoped_excludes_other_leaver(kb: KnowledgeBase) -> None:
    _two_leavers(kb)
    result = kb.query(QUERY_HIT, leaver="lee")
    assert result.found is True
    assert {c.session_id for c in result.citations} == {"sess-lee"}


def test_query_scoped_same_leaver_found(kb: KnowledgeBase) -> None:
    _two_leavers(kb)
    result = kb.query(QUERY_HIT, leaver="kim")
    assert result.found is True
    assert {c.session_id for c in result.citations} == {SESSION_ID}
    assert result.citations[0].question_id == "inc-01"


def test_query_scoped_unknown_leaver_not_found(kb: KnowledgeBase) -> None:
    _two_leavers(kb)
    result = kb.query(QUERY_HIT, leaver="park")
    assert result.found is False
    assert result.answer == NOT_FOUND_MESSAGE
    assert result.citations == []


def test_query_without_leaver_searches_all(kb: KnowledgeBase) -> None:
    _two_leavers(kb)
    result = kb.query(QUERY_HIT, top_k=5)
    assert {c.session_id for c in result.citations} == {SESSION_ID, "sess-lee"}


def test_add_masks_contacts_in_payload(kb: KnowledgeBase) -> None:
    entry = _entries()[0].model_copy(
        update={"question": "급할 때 010-9876-5432 로 전화하나요?", "answer": CONTACT_ANSWER}
    )
    kb.add([entry], "kim")
    (payload,) = _payloads(kb)
    for key in ("answer", "question"):
        text = str(payload[key])
        assert "010-" not in text
        assert "@example.com" not in text
    assert "[전화번호]" in str(payload["answer"])
    assert "[이메일]" in str(payload["answer"])
    assert "[전화번호]" in str(payload["question"])


def test_add_masks_contacts_in_embed_input(tmp_path: Path) -> None:
    emb = RecordingEmbedder()
    kb = KnowledgeBase(
        tmp_path / "qdrant", emb, FakeLLM(), MIN_SCORE, embed_with_question=True
    )
    try:
        entry = _entries()[0].model_copy(
            update={"question": "번호 010-9876-5432?", "answer": CONTACT_ANSWER}
        )
        kb.add([entry])
        (text,) = emb.calls[-1]
        assert "010-" not in text
        assert "ops.lead@example.com" not in text
    finally:
        kb.close()


def test_query_masks_excerpt_and_answer_of_raw_payload(kb: KnowledgeBase) -> None:
    entry = _entries()[0].model_copy(update={"answer": CONTACT_ANSWER})
    vector = FakeEmbedder().embed([CONTACT_ANSWER])[0]
    kb._client.upsert(
        collection_name=kb._collection,
        points=[PointStruct(id=1, vector=vector, payload=entry.model_dump(mode="json"))],
    )
    result = kb.query(CONTACT_ANSWER)
    assert result.found is True
    excerpt = result.citations[0].excerpt
    assert "010-1234-5678" not in excerpt
    assert "ops.lead@example.com" not in excerpt
    assert "[전화번호]" in excerpt and "[이메일]" in excerpt
    assert "010-1234-5678" not in result.answer
    assert "ops.lead@example.com" not in result.answer


class ContactLeakLLM(FakeLLM):
    def compose_answer(self, question: str, citations: list[Citation]) -> str | None:
        return "담당자 02-123-4567, help@example.org 로 문의하세요."


def test_query_masks_composed_answer(tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path / "qdrant", FakeEmbedder(), ContactLeakLLM(), MIN_SCORE)
    try:
        kb.add(_entries(), "kim")
        result = kb.query(QUERY_HIT, leaver="kim")
        assert result.found is True
        assert result.answer == "담당자 [전화번호], [이메일] 로 문의하세요."
    finally:
        kb.close()
