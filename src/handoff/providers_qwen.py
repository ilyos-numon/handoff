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
    "4. 한국어 3문장 이내로 답합니다."
)

SUMMARIZE_SYSTEM = (
    "당신은 인수인계 세션 기록을 요약하는 도우미입니다. "
    "주어진 답변 내용만으로 한국어로 정확히 3줄 요약을 씁니다. "
    "줄마다 60자 이내로 쓰고, 번호나 기호를 붙이지 않습니다. 답변에 없는 내용을 더하지 않습니다."
)

_LINE_PREFIX = re.compile(r"^(?:[-•*·]+|\d+[.)])\s*")


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
        if not out or out.startswith("NONE"):
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
