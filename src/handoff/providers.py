"""Embedding and LLM providers (fake, deterministic mode only)."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from typing import TYPE_CHECKING, Protocol

from handoff.config import Settings
from handoff.question_tree import AXIS_LABELS, FollowUp

if TYPE_CHECKING:
    from handoff.contracts import (
        ArtifactKind,
        ArtifactSection,
        CardDraft,
        ClaimVerdict,
        ContradictionDraft,
        DrillDraft,
        DrillFeedbackDraft,
        DrillScenario,
        Entity,
        EntityDraft,
        GeneratedQuestion,
        KnowledgeCard,
        Slot,
        SlotJudgement,
        SourceDoc,
    )
    from handoff.kb import Citation, KBEntry

EMBED_DIM = 1024

SUMMARY_EMPTY = ["오늘 남긴 답변이 없습니다.", "다음 세션에서 이어서 이야기해 주세요.", "-"]


class ProviderError(RuntimeError):
    """Provider call failed. The message must not contain keys or request bodies."""


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class LLM(Protocol):
    def follow_up(
        self, question: str, answer: str, candidates: list[FollowUp]
    ) -> FollowUp | None: ...

    def compose_answer(self, question: str, citations: list[Citation]) -> str | None: ...

    def summarize(self, entries: list[KBEntry]) -> list[str]: ...

    def extract_entities(self, docs: list[SourceDoc]) -> list[EntityDraft]: ...

    def generate_questions(self, entities: list[Entity], limit: int) -> list[GeneratedQuestion]: ...

    def judge_depth(self, question: str, answer: str) -> SlotJudgement: ...

    def slot_follow_up(self, question: str, answer: str, slot: Slot) -> str: ...

    def extract_cards(
        self, entries: list[KBEntry], known_entities: list[str]
    ) -> list[CardDraft]: ...

    def find_contradictions(self, cards: list[KnowledgeCard]) -> list[ContradictionDraft]: ...

    def verify_claims(
        self, question: str, claims: list[str], citations: list[Citation]
    ) -> list[ClaimVerdict]: ...

    def write_artifact(
        self, kind: ArtifactKind, cards: list[KnowledgeCard]
    ) -> list[ArtifactSection]: ...

    def make_drill(self, cards: list[KnowledgeCard], seed: KnowledgeCard | None) -> DrillDraft: ...

    def review_drill(
        self, drill: DrillScenario, cards: list[KnowledgeCard], answer: str
    ) -> DrillFeedbackDraft: ...


def fake_summary(entries: list[KBEntry]) -> list[str]:
    if not entries:
        return list(SUMMARY_EMPTY)
    main = [e for e in entries if re.fullmatch(r"[a-z]{3}-\d{2}", e.question_id)]
    rest = [e for e in entries if not re.fullmatch(r"[a-z]{3}-\d{2}", e.question_id)]
    lines = [
        f"{AXIS_LABELS[e.axis]}: {' '.join(e.answer.split())[:60]}" for e in (main + rest)[:3]
    ]
    lines.extend(["-"] * (3 - len(lines)))
    return lines


def _features(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).lower()
    features: list[str] = []
    for tok in re.findall(r"\w+", normalized):
        if len(tok) == 1:
            features.append(tok)
        else:
            features.extend(tok[i : i + 2] for i in range(len(tok) - 1))
    return features


class FakeEmbedder:
    """Deterministic hashed character-bigram embedder."""

    def __init__(self) -> None:
        self.dim = EMBED_DIM

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for f in _features(text):
            idx = int.from_bytes(hashlib.sha256(f.encode("utf-8")).digest()[:8], "big") % 1024
            vec[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]


class FakeLLM:
    """Deterministic LLM stand-in."""

    def follow_up(
        self, question: str, answer: str, candidates: list[FollowUp]
    ) -> FollowUp | None:
        return candidates[0] if candidates else None

    def compose_answer(self, question: str, citations: list[Citation]) -> str:
        return f"인수인계 기록에 따르면: {citations[0].excerpt}"

    def summarize(self, entries: list[KBEntry]) -> list[str]:
        return fake_summary(entries)

    def extract_entities(self, docs: list[SourceDoc]) -> list[EntityDraft]:
        from handoff.llm_tasks.sources import fake_extract_entities

        return fake_extract_entities(docs)

    def generate_questions(self, entities: list[Entity], limit: int) -> list[GeneratedQuestion]:
        from handoff.llm_tasks.sources import fake_generate_questions

        return fake_generate_questions(entities, limit)

    def judge_depth(self, question: str, answer: str) -> SlotJudgement:
        from handoff.llm_tasks.depth import fake_judge_depth

        return fake_judge_depth(question, answer)

    def slot_follow_up(self, question: str, answer: str, slot: Slot) -> str:
        from handoff.llm_tasks.depth import fake_slot_follow_up

        return fake_slot_follow_up(question, answer, slot)

    def extract_cards(self, entries: list[KBEntry], known_entities: list[str]) -> list[CardDraft]:
        from handoff.llm_tasks.cards import fake_extract_cards

        return fake_extract_cards(entries, known_entities)

    def find_contradictions(self, cards: list[KnowledgeCard]) -> list[ContradictionDraft]:
        from handoff.llm_tasks.gaps import fake_find_contradictions

        return fake_find_contradictions(cards)

    def verify_claims(
        self, question: str, claims: list[str], citations: list[Citation]
    ) -> list[ClaimVerdict]:
        from handoff.llm_tasks.verify import fake_verify_claims

        return fake_verify_claims(question, claims, citations)

    def write_artifact(
        self, kind: ArtifactKind, cards: list[KnowledgeCard]
    ) -> list[ArtifactSection]:
        from handoff.llm_tasks.artifacts import fake_write_artifact

        return fake_write_artifact(kind, cards)

    def make_drill(self, cards: list[KnowledgeCard], seed: KnowledgeCard | None) -> DrillDraft:
        from handoff.llm_tasks.drill import fake_make_drill

        return fake_make_drill(cards, seed)

    def review_drill(
        self, drill: DrillScenario, cards: list[KnowledgeCard], answer: str
    ) -> DrillFeedbackDraft:
        from handoff.llm_tasks.drill import fake_review_drill

        return fake_review_drill(drill, cards, answer)


def build_providers(settings: Settings) -> tuple[Embedder, LLM]:
    if settings.provider == "fake":
        return FakeEmbedder(), FakeLLM()
    if settings.provider == "qwen":
        from handoff.providers_qwen import QwenEmbedder, QwenLLM

        embedder = QwenEmbedder(
            settings.llm_base_url,
            settings.embed_model,
            settings.llm_api_key,
            settings.embed_dim,
            settings.llm_timeout_s,
        )
        llm = QwenLLM(
            settings.llm_base_url,
            settings.llm_model,
            settings.llm_api_key,
            settings.llm_timeout_s,
        )
        return embedder, llm
    raise ValueError(f"unsupported HANDOFF_PROVIDER: {settings.provider}")
