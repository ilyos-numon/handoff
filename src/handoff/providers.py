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


def build_providers(settings: Settings) -> tuple[Embedder, LLM]:
    if settings.provider != "fake":
        raise ValueError(f"unsupported HANDOFF_PROVIDER: {settings.provider}")
    return FakeEmbedder(), FakeLLM()
