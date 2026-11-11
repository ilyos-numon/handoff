"""Qwen (OpenAI-compatible) LLM and embedding adapters."""

from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING, Any

import httpx

from handoff.providers import SUMMARY_EMPTY, ProviderError, fake_summary
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

RETRY_WAIT_S = 1.0
EMBED_BATCH = 10
SUMMARY_LINE_MAX = 60

_sleep = time.sleep

FOLLOW_UP_SYSTEM = (
    "당신은 퇴사자 인수인계 인터뷰를 돕는 진행자입니다. "
    "주 질문과 퇴사자의 답변을 읽고, 후임자에게 가장 도움이 될 꼬리 질문 하나를 정합니다. "
    "후보 중 답변에 잘 이어지는 것이 있으면 그 번호를 고릅니다. "
    "맞는 후보가 없으면 답변 내용에 근거한 짧은 한국어 꼬리 질문 한 문장을 새로 씁니다. "
    "답변에 없는 이름이나 사실을 지어내지 않습니다. "
    '반드시 {"pick": <후보 번호 0부터 또는 null>, "question": "<새 질문 또는 빈 문자열>"} '
    "형식의 JSON 만 출력합니다."
)

COMPOSE_SYSTEM = (
    "당신은 인수인계 기록만 보고 후임자의 질문에 답하는 도우미입니다. 다음 규칙을 반드시 지킵니다.\n"
    "1. 근거 발췌에 있는 내용만 씁니다.\n"
    "2. 근거에 없는 날짜·숫자·사람·조직·절차를 더하지 않습니다.\n"
    "3. 근거가 질문에 답하지 못하면 정확히 NONE 만 출력합니다.\n"
    "4. 한국어 3문장 이내로 답합니다.\n"
    "5. 근거가 질문이 묻는 바로 그 대상과 그 종류의 정보(절차·연락처·검증 방법·숫자 등)를 "
    "직접 적고 있을 때만 답합니다. 근거가 관련 주제를 다룰 뿐 그 정보를 직접 적고 있지 않으면 "
    "NONE 만 출력합니다.\n"
    "6. 답할 수 없을 때 이유나 거절 문장을 쓰지 않고 NONE 한 단어만 출력합니다. "
    "답할 때는 답변 문장만 출력하고 NONE 을 붙이지 않습니다."
)

# compose 출력이 아래 중 하나면 답이 없는 것(None)으로 본다:
# 빈 출력, 독립된 토큰 NONE(위치 무관), 서술형 거절 문구.
COMPOSE_NONE_TOKEN = re.compile(r"(?<![A-Za-z0-9_])NONE(?![A-Za-z0-9_])")
COMPOSE_REFUSAL_PHRASES = (
    "답할 수 없",
    "답변할 수 없",
    "정보가 없",
    "포함되어 있지 않",
    "확인할 수 없",
)

SUMMARIZE_SYSTEM = (
    "당신은 인수인계 세션 기록을 요약하는 도우미입니다. "
    "주어진 답변 내용만으로 한국어로 정확히 3줄 요약을 씁니다. "
    "줄마다 60자 이내로 쓰고, 번호나 기호를 붙이지 않습니다. 답변에 없는 내용을 더하지 않습니다."
)

_LINE_PREFIX = re.compile(r"^(?:[-•*·]+|\d+[.)])\s*")


def _is_no_answer(out: str) -> bool:
    if not out or COMPOSE_NONE_TOKEN.search(out):
        return True
    return any(phrase in out for phrase in COMPOSE_REFUSAL_PHRASES)


def _retryable_status(code: int) -> bool:
    return code == 429 or 500 <= code < 600


class _Client:
    """Shared POST helper with one retry for timeouts, transport errors, 429 and 5xx."""

    def __init__(
        self,
        kind: str,
        base_url: str,
        api_key: str,
        timeout_s: float,
        transport: httpx.BaseTransport | None,
    ) -> None:
        self._kind = kind
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout_s,
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    def _fail(self, status: str) -> ProviderError:
        return ProviderError(f"{self._kind} request failed: status={status}")

    def post(self, path: str, body: dict[str, Any]) -> Any:
        status = "timeout"
        for attempt in range(2):
            if attempt:
                _sleep(RETRY_WAIT_S)
            try:
                resp = self._http.post(path, json=body)
            except httpx.TimeoutException:
                status = "timeout"
                continue
            except httpx.TransportError:
                status = "transport"
                continue
            if resp.status_code < 400:
                try:
                    return resp.json()
                except ValueError:
                    raise self._fail(f"{resp.status_code} invalid_json") from None
            status = str(resp.status_code)
            if not _retryable_status(resp.status_code):
                break
        raise self._fail(status)

    def close(self) -> None:
        self._http.close()


class QwenLLM:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout_s: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._model = model
        self._client = _Client("llm", base_url, api_key, timeout_s, transport)
        self.last_reasoning_chars = 0

    def _chat(self, system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": max_tokens,
            "enable_thinking": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        data = self._client.post("/chat/completions", body)
        try:
            message = data["choices"][0]["message"]
            content = message.get("content") or ""
            reasoning = message.get("reasoning_content") or ""
        except (KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError("llm request failed: status=malformed") from None
        self.last_reasoning_chars = len(reasoning) if isinstance(reasoning, str) else 0
        return content if isinstance(content, str) else ""

    def follow_up(
        self, question: str, answer: str, candidates: list[FollowUp]
    ) -> FollowUp | None:
        if not candidates:
            return None
        numbered = "\n".join(f"{i}. {c.text}" for i, c in enumerate(candidates))
        user = f"주 질문: {question}\n답변: {answer}\n후보:\n{numbered}"
        try:
            content = self._chat(FOLLOW_UP_SYSTEM, user, 200, json_mode=True)
            parsed = json.loads(content)
        except (ProviderError, ValueError):
            return candidates[0]
        if not isinstance(parsed, dict):
            return candidates[0]
        pick = parsed.get("pick")
        if isinstance(pick, int) and not isinstance(pick, bool) and 0 <= pick < len(candidates):
            return candidates[pick]
        text = parsed.get("question")
        if isinstance(text, str) and 5 <= len(text.strip()) <= 150:
            base = candidates[0].id.rsplit("-", 1)[0]
            return FollowUp(id=f"{base}-g1", text=text.strip())
        return candidates[0]

    def compose_answer(self, question: str, citations: list[Citation]) -> str | None:
        evidence = "\n".join(
            f"[근거 {i}] ({AXIS_LABELS[c.axis]}) {c.excerpt}"
            for i, c in enumerate(citations, start=1)
        )
        user = f"질문: {question}\n{evidence}"
        out = self._chat(COMPOSE_SYSTEM, user, 300).strip()
        if _is_no_answer(out):
            return None
        return out

    def summarize(self, entries: list[KBEntry]) -> list[str]:
        if not entries:
            return list(SUMMARY_EMPTY)
        fallback = fake_summary(entries)
        user = "\n".join(
            f"[{AXIS_LABELS[e.axis]}] 질문: {e.question}\n답변: {e.answer}" for e in entries
        )
        try:
            content = self._chat(SUMMARIZE_SYSTEM, user, 300)
        except ProviderError:
            return fallback
        lines: list[str] = []
        for raw in content.splitlines():
            line = _LINE_PREFIX.sub("", raw.strip()).strip()
            if line:
                lines.append(line[:SUMMARY_LINE_MAX])
        lines = lines[:3]
        if not lines:
            return fallback
        lines.extend(fallback[len(lines) :])
        return lines

    def extract_entities(self, docs: list[SourceDoc]) -> list[EntityDraft]:
        from handoff.llm_tasks.sources import qwen_extract_entities

        return qwen_extract_entities(self._chat, docs)

    def generate_questions(self, entities: list[Entity], limit: int) -> list[GeneratedQuestion]:
        from handoff.llm_tasks.sources import qwen_generate_questions

        return qwen_generate_questions(self._chat, entities, limit)

    def judge_depth(self, question: str, answer: str) -> SlotJudgement:
        from handoff.llm_tasks.depth import qwen_judge_depth

        return qwen_judge_depth(self._chat, question, answer)

    def slot_follow_up(self, question: str, answer: str, slot: Slot) -> str:
        from handoff.llm_tasks.depth import qwen_slot_follow_up

        return qwen_slot_follow_up(self._chat, question, answer, slot)

    def extract_cards(self, entries: list[KBEntry], known_entities: list[str]) -> list[CardDraft]:
        from handoff.llm_tasks.cards import qwen_extract_cards

        return qwen_extract_cards(self._chat, entries, known_entities)

    def find_contradictions(self, cards: list[KnowledgeCard]) -> list[ContradictionDraft]:
        from handoff.llm_tasks.gaps import qwen_find_contradictions

        return qwen_find_contradictions(self._chat, cards)

    def verify_claims(
        self, question: str, claims: list[str], citations: list[Citation]
    ) -> list[ClaimVerdict]:
        from handoff.llm_tasks.verify import qwen_verify_claims

        return qwen_verify_claims(self._chat, question, claims, citations)

    def write_artifact(
        self, kind: ArtifactKind, cards: list[KnowledgeCard]
    ) -> list[ArtifactSection]:
        from handoff.llm_tasks.artifacts import qwen_write_artifact

        return qwen_write_artifact(self._chat, kind, cards)

    def make_drill(self, cards: list[KnowledgeCard], seed: KnowledgeCard | None) -> DrillDraft:
        from handoff.llm_tasks.drill import qwen_make_drill

        return qwen_make_drill(self._chat, cards, seed)

    def review_drill(
        self, drill: DrillScenario, cards: list[KnowledgeCard], answer: str
    ) -> DrillFeedbackDraft:
        from handoff.llm_tasks.drill import qwen_review_drill

        return qwen_review_drill(self._chat, drill, cards, answer)

    def tacit_follow_up(self, question: str, answer: str, cues: list[str]) -> str:
        from handoff.llm_tasks.tacit import qwen_tacit_follow_up

        return qwen_tacit_follow_up(self._chat, question, answer, cues)

    def annotate_invalidation(self, cards: list[NewCard]) -> list[list[InvalidationCondition]]:
        from handoff.llm_tasks.expiry import qwen_annotate_invalidation

        return qwen_annotate_invalidation(self._chat, cards)

    def route_question(
        self, question: str, candidates: list[RouteCandidate]
    ) -> RouteDraft | None:
        from handoff.llm_tasks.routing import qwen_route_question

        return qwen_route_question(self._chat, question, candidates)

    def explain_fence(self, subject: str, cards: list[KnowledgeCard]) -> FenceDraft:
        from handoff.llm_tasks.fence import qwen_explain_fence

        return qwen_explain_fence(self._chat, subject, cards)


class QwenEmbedder:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        dim: int,
        timeout_s: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.dim = dim
        self._model = model
        self._client = _Client("embed", base_url, api_key, timeout_s, transport)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            batch = texts[start : start + EMBED_BATCH]
            body = {
                "model": self._model,
                "input": batch,
                "dimensions": self.dim,
                "encoding_format": "float",
            }
            data = self._client.post("/embeddings", body)
            try:
                items = sorted(data["data"], key=lambda d: d["index"])
                batch_vecs = [[float(v) for v in item["embedding"]] for item in items]
            except (KeyError, TypeError, ValueError):
                raise ProviderError("embed request failed: status=malformed") from None
            if len(batch_vecs) != len(batch):
                raise ProviderError("embed request failed: status=count_mismatch")
            for vec in batch_vecs:
                if len(vec) != self.dim:
                    raise ProviderError(
                        f"embed dimension mismatch: got {len(vec)}, expected {self.dim}"
                    )
            vectors.extend(batch_vecs)
        return vectors
