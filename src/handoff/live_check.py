"""Live connectivity check for the qwen provider: ``python -m handoff.live_check``."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from handoff.config import load_settings
from handoff.kb import Citation, KBEntry
from handoff.question_tree import load_tree

ANSWER_MAIN = "금요일 배포는 피합니다. 작년 3월 금요일 저녁 배포 후 정산 배치가 주말 내내 실패했습니다."
ANSWER_FOLLOW = "복구는 큐를 비우고 정산 배치를 수동으로 재실행했습니다."
QUERY_HIT = "금요일에 배포하면 왜 안 되나요?"
QUERY_MISS = "PG사 장애 나면 누구한테 연락해요?"
FOLLOW_UP_ANSWER = "정산 배치요. 배포 직후 큐가 밀리면 새벽 배치가 타임아웃 납니다."

T = TypeVar("T")


class CheckFailed(Exception):
    """A live check step returned an unexpected result."""


def main() -> int:
    from handoff.providers_qwen import QwenEmbedder, QwenLLM

    try:
        settings = load_settings()
    except ValueError as exc:
        print(f"LIVE SKIP: {exc}")
        return 0
    if settings.provider != "qwen":
        print(f"LIVE SKIP: HANDOFF_PROVIDER={settings.provider} (live check needs qwen)")
        return 0

    embedder = QwenEmbedder(
        settings.llm_base_url,
        settings.embed_model,
        settings.llm_api_key,
        settings.embed_dim,
        settings.llm_timeout_s,
    )
    llm = QwenLLM(
        settings.llm_base_url, settings.llm_model, settings.llm_api_key, settings.llm_timeout_s
    )
    latencies: list[float] = []
    reasoning: list[int] = []
    step = "embed"

    def timed(fn: Callable[..., T], *args: Any) -> T:
        start = time.perf_counter()
        result = fn(*args)
        latencies.append((time.perf_counter() - start) * 1000)
        return result

    def chat(fn: Callable[..., T], *args: Any) -> T:
        result = timed(fn, *args)
        reasoning.append(llm.last_reasoning_chars)
        return result

    try:
        vectors = timed(embedder.embed, [ANSWER_MAIN, ANSWER_FOLLOW])
        if len(vectors) != 2:
            raise CheckFailed
        embed_dim = len(vectors[0])
        if embed_dim != settings.embed_dim:
            raise CheckFailed

        step = "follow_up"
        node = next(n for n in load_tree("backend_developer").nodes if n.id == "sys-01")
        picked = chat(llm.follow_up, node.text, FOLLOW_UP_ANSWER, node.follow_ups)
        if picked is None:
            raise CheckFailed
        follow_up_id = picked.id

        now = datetime.now(UTC)
        citation = Citation(
            session_id="live-check",
            question_id="sys-01",
            axis="systems",
            excerpt=ANSWER_MAIN,
            score=1.0,
            session_number=1,
            created_at=now,
        )
        step = "compose"
        composed = chat(llm.compose_answer, QUERY_HIT, [citation])
        if not isinstance(composed, str):
            raise CheckFailed

        step = "none_guard"
        guarded = chat(llm.compose_answer, QUERY_MISS, [citation])
        none_guard_ok = guarded is None

        step = "summarize"
        entries = [
            KBEntry(
                session_id="live-check",
                question_id=qid,
                axis="systems",
                question=question,
                answer=answer,
                created_at=now,
                session_number=1,
            )
            for qid, question, answer in [
                ("sys-01", node.text, ANSWER_MAIN),
                ("sys-01-f1", node.follow_ups[0].text, ANSWER_FOLLOW),
            ]
        ]
        summary = chat(llm.summarize, entries)
        if len(summary) != 3:
            raise CheckFailed
    except Exception as exc:  # noqa: BLE001 - report class only, never details
        print(f"LIVE FAIL {step}: {type(exc).__name__}")
        return 1

    thinking_off = all(r == 0 for r in reasoning)
    print(
        f"LIVE OK embed_dim={embed_dim} follow_up={follow_up_id} compose=ok "
        f"none_guard={'ok' if none_guard_ok else 'FAIL'} summary_lines={len(summary)} "
        f"thinking={'off' if thinking_off else 'on'} latency_ms={round(max(latencies))}"
    )
    return 0 if none_guard_ok and thinking_off else 1


if __name__ == "__main__":
    sys.exit(main())
