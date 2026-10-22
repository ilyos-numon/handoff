import math
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from handoff.config import Settings
from handoff.kb import NOT_FOUND_MESSAGE, KBEntry, KnowledgeBase
from handoff.providers import EMBED_DIM, FakeEmbedder, FakeLLM, build_providers

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
