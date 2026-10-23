"""Record/replay LLM providers backed by a JSON cassette."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from handoff.providers import FakeLLM
from handoff.providers_qwen import QwenLLM
from handoff.question_tree import FollowUp

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
        FenceDraft,
        GeneratedQuestion,
        InvalidationCondition,
        KnowledgeCard,
        NewCard,
        RouteCandidate,
        RouteDraft,
        Slot,
        SlotJudgement,
        SourceDoc,
    )
    from handoff.kb import Citation, KBEntry

CASSETTE_VERSION = 1

_ID_PATTERN = re.compile(r"\b[0-9a-f]{32}\b|\bc\d+\b")
_PLACEHOLDER = re.compile(r"⟨[cs]\d+⟩")


class CassetteMiss(Exception):
    """No recorded response for a prompt. Deliberately not a ProviderError."""


def normalize(text: str, ids: dict[str, str]) -> str:
    """Replace card ids and 32-hex session ids with stable placeholders (first-seen order)."""

    def repl(match: re.Match[str]) -> str:
        raw = match.group(0)
        placeholder = ids.get(raw)
        if placeholder is None:
            kind = "s" if _is_hex32(raw) else "c"
            count = sum(1 for p in ids.values() if p.startswith(f"⟨{kind}"))
            placeholder = f"⟨{kind}{count + 1}⟩"
            ids[raw] = placeholder
        return placeholder

    return _ID_PATTERN.sub(repl, text)


def _is_hex32(raw: str) -> bool:
    return len(raw) == 32 and all(ch in "0123456789abcdef" for ch in raw)


def denormalize(text: str, ids: dict[str, str]) -> str:
    """Replace placeholders with the current originals; unknown placeholders stay as-is."""
    reverse = {placeholder: raw for raw, placeholder in ids.items()}
    return _PLACEHOLDER.sub(lambda m: reverse.get(m.group(0), m.group(0)), text)


class Cassette:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._entries: dict[str, str] = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            entries = data.get("entries", {}) if isinstance(data, dict) else {}
            self._entries = {str(k): str(v) for k, v in entries.items()}

    def key(
        self, model: str, system: str, user: str, max_tokens: int, json_mode: bool
    ) -> tuple[str, dict[str, str]]:
        ids: dict[str, str] = {}
        payload = json.dumps(
            [model, normalize(system, ids), normalize(user, ids), max_tokens, json_mode],
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest(), ids

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._entries.get(key)

    def put(self, key: str, normalized_response: str) -> None:
        with self._lock:
            self._entries[key] = normalized_response

    def save(self) -> None:
        with self._lock:
            body = json.dumps(
                {"version": CASSETTE_VERSION, "entries": self._entries},
                sort_keys=True,
                ensure_ascii=False,
                indent=1,
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(body + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def _utc_today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


class DailyBudget:
    """File-backed per-UTC-day counter for live LLM calls."""

    def __init__(self, path: Path, cap: int, today: Callable[[], str] = _utc_today) -> None:
        self.path = path
        self.cap = cap
        self._today = today
        self._lock = threading.Lock()

    def _read(self) -> tuple[str, int]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return str(data["date"]), int(data["count"])
        except (OSError, ValueError, KeyError, TypeError):
            return "", 0

    def try_spend(self) -> bool:
        with self._lock:
            today = self._today()
            date, count = self._read()
            if date != today:
                count = 0
            if count >= self.cap:
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"date": today, "count": count + 1}), encoding="utf-8")
            return True

    def used_today(self) -> int:
        with self._lock:
            date, count = self._read()
            return count if date == self._today() else 0


class RecordingQwenLLM(QwenLLM):
    """Real Qwen calls whose normalized responses are written to the cassette."""

    def __init__(
        self,
        cassette: Cassette,
        base_url: str,
        model: str,
        api_key: str,
        timeout_s: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(base_url, model, api_key, timeout_s, transport)
        self.cassette = cassette

    def _chat(self, system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        content = super()._chat(system, user, max_tokens, json_mode)
        key, ids = self.cassette.key(self._model, system, user, max_tokens, json_mode)
        self.cassette.put(key, normalize(content, ids))
        self.cassette.save()
        return content


class _ReplayQwen(QwenLLM):
    """QwenLLM whose chat only reads the cassette; never touches the network."""

    def __init__(self, cassette: Cassette, model: str, on_hit: Callable[[], None]) -> None:
        super().__init__("http://replay.invalid", model, "", 1.0, transport=_NoNetwork())
        self._cassette = cassette
        self._on_hit = on_hit

    def _chat(self, system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        key, ids = self._cassette.key(self._model, system, user, max_tokens, json_mode)
        recorded = self._cassette.get(key)
        if recorded is None:
            raise CassetteMiss("no recorded response")
        self._on_hit()
        return denormalize(recorded, ids)


class _NoNetwork(httpx.BaseTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise CassetteMiss("replay provider must not use the network")


class ReplayLLM:
    """Cassette replay; misses go to the live LLM within the daily budget, else FakeLLM."""

    def __init__(
        self,
        cassette: Cassette,
        model: str,
        fake: FakeLLM,
        live: QwenLLM | None,
        budget: DailyBudget,
    ) -> None:
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._fake_fallbacks = 0
        self._replay = _ReplayQwen(cassette, model, self._count_hit)
        self._fake = fake
        self._live = live
        self._budget = budget

    def _count_hit(self) -> None:
        with self._lock:
            self._hits += 1

    def stats(self) -> dict[str, int]:
        with self._lock:
            hits, misses, fallbacks = self._hits, self._misses, self._fake_fallbacks
        return {
            "hits": hits,
            "misses": misses,
            "live_calls_today": self._budget.used_today(),
            "live_cap": self._budget.cap,
            "fake_fallbacks": fallbacks,
        }

    def _run(self, name: str, *args: Any) -> Any:
        try:
            return getattr(self._replay, name)(*args)
        except CassetteMiss:
            with self._lock:
                self._misses += 1
        if self._live is not None and self._budget.try_spend():
            return getattr(self._live, name)(*args)
        with self._lock:
            self._fake_fallbacks += 1
        return getattr(self._fake, name)(*args)

    def follow_up(self, question: str, answer: str, candidates: list[FollowUp]) -> FollowUp | None:
        result: FollowUp | None = self._run("follow_up", question, answer, candidates)
        return result

    def compose_answer(self, question: str, citations: list[Citation]) -> str | None:
        result: str | None = self._run("compose_answer", question, citations)
        return result

    def summarize(self, entries: list[KBEntry]) -> list[str]:
        result: list[str] = self._run("summarize", entries)
        return result

    def extract_entities(self, docs: list[SourceDoc]) -> list[EntityDraft]:
        result: list[EntityDraft] = self._run("extract_entities", docs)
        return result

    def generate_questions(self, entities: list[Entity], limit: int) -> list[GeneratedQuestion]:
        result: list[GeneratedQuestion] = self._run("generate_questions", entities, limit)
        return result

    def judge_depth(self, question: str, answer: str) -> SlotJudgement:
        result: SlotJudgement = self._run("judge_depth", question, answer)
        return result

    def slot_follow_up(self, question: str, answer: str, slot: Slot) -> str:
        result: str = self._run("slot_follow_up", question, answer, slot)
        return result

    def extract_cards(self, entries: list[KBEntry], known_entities: list[str]) -> list[CardDraft]:
        result: list[CardDraft] = self._run("extract_cards", entries, known_entities)
        return result

    def find_contradictions(self, cards: list[KnowledgeCard]) -> list[ContradictionDraft]:
        result: list[ContradictionDraft] = self._run("find_contradictions", cards)
        return result

    def verify_claims(
        self, question: str, claims: list[str], citations: list[Citation]
    ) -> list[ClaimVerdict]:
        result: list[ClaimVerdict] = self._run("verify_claims", question, claims, citations)
        return result

    def write_artifact(
        self, kind: ArtifactKind, cards: list[KnowledgeCard]
    ) -> list[ArtifactSection]:
        result: list[ArtifactSection] = self._run("write_artifact", kind, cards)
        return result

    def make_drill(self, cards: list[KnowledgeCard], seed: KnowledgeCard | None) -> DrillDraft:
        result: DrillDraft = self._run("make_drill", cards, seed)
        return result

    def review_drill(
        self, drill: DrillScenario, cards: list[KnowledgeCard], answer: str
    ) -> DrillFeedbackDraft:
        result: DrillFeedbackDraft = self._run("review_drill", drill, cards, answer)
        return result

    def tacit_follow_up(self, question: str, answer: str, cues: list[str]) -> str:
        result: str = self._run("tacit_follow_up", question, answer, cues)
        return result

    def annotate_invalidation(self, cards: list[NewCard]) -> list[list[InvalidationCondition]]:
        result: list[list[InvalidationCondition]] = self._run("annotate_invalidation", cards)
        return result

    def route_question(self, question: str, candidates: list[RouteCandidate]) -> RouteDraft | None:
        result: RouteDraft | None = self._run("route_question", question, candidates)
        return result

    def explain_fence(self, subject: str, cards: list[KnowledgeCard]) -> FenceDraft:
        result: FenceDraft = self._run("explain_fence", subject, cards)
        return result
