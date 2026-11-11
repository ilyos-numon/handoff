"""Live check for the knowledge-honesty LLM methods: ``python -m handoff.live_check_honesty``."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from handoff.config import load_settings
from handoff.contracts import CardType, Evidence, KnowledgeCard, NewCard, RouteCandidate
from handoff.providers import LLM
from handoff.providers_qwen import QwenLLM
from handoff.signals import TACIT_TARGET_CUES, tacit_cues

# 가상 문장만 쓴다 (eval/fixtures 문장 금지). 도메인: 물류 창고 출고.
LEAVER = "live-check"
ROLE = "backend_developer"
CARDS: list[tuple[CardType, str, str, list[str]]] = [
    (
        "decision",
        "출고 마감 시각 고정",
        "출고 마감을 오후 3시로 고정했습니다. 택배사 집하 차량이 4시에 오기 때문입니다.",
        ["출고 마감"],
    ),
    (
        "pitfall",
        "출고 마감 직전 재고 동기화",
        (
            "출고 마감 직전에 재고 동기화를 돌리면 안 됩니다. 동기화가 늦어지면 "
            "피킹 목록 지연 알림이 먼저 옵니다. 그대로 두면 당일 출고가 밀립니다."
        ),
        ["출고 마감", "재고 동기화"],
    ),
    (
        "procedure",
        "재고 동기화 지연 대응",
        "재고 동기화 대기열을 먼저 확인하고 멈춘 작업을 재실행합니다.",
        ["재고 동기화"],
    ),
]
FENCE_SUBJECT = "출고 마감 시각"
ROUTE_QUESTION = "택배사 집하 차량 시간이 바뀌면 누구에게 물어보나요?"
ROUTE_CANDIDATES = [
    RouteCandidate(
        ref="c1",
        basis="contact_card",
        text="택배사 집하 일정은 물류운영팀 리드가 조율합니다.",
        roles=["물류운영팀 리드"],
    ),
    RouteCandidate(
        ref="a:live-a:sys-01",
        basis="people_answer",
        text="재고 동기화 장애는 창고 시스템 담당자에게 알립니다.",
        roles=["창고 시스템 담당자"],
    ),
]
TACIT_QUESTION = "피킹 인원은 어떻게 배치하나요?"
TACIT_ANSWER = "그날 주문량을 보고 그냥 감으로 인원을 나눕니다."
SIGN_LABEL = "징후:"
RESPONSE_LABEL = "대응:"

T = TypeVar("T")


class CheckFailed(Exception):
    """A live check step returned an unexpected result."""


def _cards(now: datetime) -> list[KnowledgeCard]:
    return [
        KnowledgeCard(
            id=f"c{i}",
            leaver=LEAVER,
            role=ROLE,
            session_id="live-a",
            type=card_type,
            title=title,
            body=body,
            entities=entities,
            confidence=0.8,
            evidence=[
                Evidence(
                    session_id="live-a",
                    question_id=f"sys-0{i}",
                    session_number=1,
                    excerpt=body[:200],
                )
            ],
            created_at=now,
        )
        for i, (card_type, title, body, entities) in enumerate(CARDS, start=1)
    ]


def run(llm: LLM) -> int:
    """Call the 4 honesty methods and the premortem artifact once each; print one result line."""
    latencies: list[float] = []
    step = "explain_fence"

    def timed(fn: Callable[..., T], *args: Any) -> T:
        start = time.perf_counter()
        result = fn(*args)
        latencies.append((time.perf_counter() - start) * 1000)
        return result

    try:
        cards = _cards(datetime.now(UTC))
        card_ids = {c.id for c in cards}

        fence = timed(llm.explain_fence, FENCE_SUBJECT, cards)
        points = fence.reasons + fence.risks
        if not all(set(p.card_ids) <= card_ids for p in points):
            raise CheckFailed

        step = "annotate_invalidation"
        new_cards: list[NewCard] = list(cards)
        conditions = timed(llm.annotate_invalidation, new_cards)
        if len(conditions) != len(cards):
            raise CheckFailed

        step = "route_question"
        route = timed(llm.route_question, ROUTE_QUESTION, ROUTE_CANDIDATES)
        if route is not None and not any(
            route.role in c.roles for c in ROUTE_CANDIDATES if c.ref == route.ref
        ):
            raise CheckFailed

        step = "tacit_follow_up"
        tacit = timed(llm.tacit_follow_up, TACIT_QUESTION, TACIT_ANSWER, tacit_cues(TACIT_ANSWER))
        if not any(cue in tacit for cue in TACIT_TARGET_CUES):
            raise CheckFailed

        step = "write_artifact"
        sections = timed(llm.write_artifact, "premortem", cards)
        if not sections or not all(
            SIGN_LABEL in s.body and RESPONSE_LABEL in s.body and set(s.card_ids) <= card_ids
            for s in sections
        ):
            raise CheckFailed
    except Exception as exc:  # noqa: BLE001 - report class only, never details
        print(f"LIVE HONESTY FAIL {step}: {type(exc).__name__}")
        return 1

    print(
        f"LIVE HONESTY OK fence={len(points)} expiry={sum(len(c) for c in conditions)} "
        f"route={route.role if route is not None else 'none'} tacit=ok "
        f"premortem={len(sections)} latency_ms={round(max(latencies))}"
    )
    return 0


def main() -> int:
    try:
        settings = load_settings()
    except ValueError as exc:
        print(f"LIVE SKIP: {exc}")
        return 0
    if settings.provider != "qwen":
        print(f"LIVE SKIP: HANDOFF_PROVIDER={settings.provider} (live check needs qwen)")
        return 0
    llm = QwenLLM(
        settings.llm_base_url, settings.llm_model, settings.llm_api_key, settings.llm_timeout_s
    )
    return run(llm)


if __name__ == "__main__":
    sys.exit(main())
