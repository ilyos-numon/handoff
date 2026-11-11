"""Live check for the knowledge-loop LLM methods: ``python -m handoff.live_check_loop``."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from handoff.config import load_settings
from handoff.contracts import (
    SLOT_TARGET_CUES,
    SLOTS,
    DrillScenario,
    Entity,
    Evidence,
    KnowledgeCard,
    Slot,
    SourceDoc,
)
from handoff.kb import Citation, KBEntry
from handoff.providers import LLM
from handoff.providers_qwen import QwenLLM

LEAVER = "live-check"
ROLE = "backend_developer"
DOCS = [
    (
        "wiki",
        "정산 개요",
        (
            "[[정산 배치]] 는 [[승인 서버]] 의 승인 내역을 모아 [[메시지 큐]] 로 넘깁니다.\n"
            "[[결제 대행사]] 응답이 늦으면 정산 배치가 밀립니다."
        ),
    ),
    ("cron", "배치 일정", "# 정산 관련 배치\n0 2 * * * 정산 배치 재실행 스크립트"),
]
ENTRIES = [
    (
        "live-a",
        1,
        "sys-01",
        "정산 배치는 어떻게 운영하나요?",
        (
            "정산 배치는 매일 새벽 2시에 실행합니다. 실패하면 먼저 메시지 큐 적체를 확인하고 "
            "재실행 스크립트를 실행합니다. 배포 직후에 정산 배치를 돌리면 안 됩니다."
        ),
    ),
    (
        "live-b",
        2,
        "rtn-01",
        "정산 배치 일정은 언제인가요?",
        "정산 배치는 매일 새벽 4시에 실행합니다. 문제가 생기면 결제팀 리드에게 먼저 연락합니다.",
    ),
]
DEPTH_QUESTION = "정산 배치가 실패하면 어떻게 하나요?"
DEPTH_ANSWER = "메시지 큐 적체를 먼저 확인하고 재실행 스크립트를 실행합니다."
SLOT: Slot = "fallback"
VERIFY_QUESTION = "정산 배치는 언제 도나요?"
VERIFY_GUARD_CLAIM = "정산은 매주 일요일 자정에 담당 임원이 직접 승인합니다."
QUESTION_LIMIT = 3
ARTIFACT_KIND = "onboarding_30d"

T = TypeVar("T")


class CheckFailed(Exception):
    """A live check step returned an unexpected result."""


def _ok(flag: bool) -> str:
    return "ok" if flag else "FAIL"


def run(llm: LLM) -> int:
    """Call the 10 knowledge-loop methods once each and print one result line."""
    latencies: list[float] = []
    step = "extract_entities"

    def timed(fn: Callable[..., T], *args: Any) -> T:
        start = time.perf_counter()
        result = fn(*args)
        latencies.append((time.perf_counter() - start) * 1000)
        return result

    now = datetime.now(UTC)
    try:
        docs = [
            SourceDoc(
                id=f"src{i}",
                leaver=LEAVER,
                kind=kind,  # type: ignore[arg-type]
                title=title,
                text=text,
                replaced=0,
                created_at=now,
            )
            for i, (kind, title, text) in enumerate(DOCS, start=1)
        ]
        drafts = timed(llm.extract_entities, docs)
        if not drafts:
            raise CheckFailed

        step = "generate_questions"
        names: list[str] = []
        for d in drafts:
            if d.name not in names:
                names.append(d.name)
        entities = [
            Entity(
                id=f"e{i}",
                leaver=LEAVER,
                name=name,
                type=next(d.type for d in drafts if d.name == name),
                source_ids=sorted({d.source_id for d in drafts if d.name == name}),
                mentions=sum(1 for d in drafts if d.name == name),
            )
            for i, name in enumerate(names, start=1)
        ]
        questions = timed(llm.generate_questions, entities, QUESTION_LIMIT)
        if not questions or len(questions) > QUESTION_LIMIT:
            raise CheckFailed

        step = "judge_depth"
        judgement = timed(llm.judge_depth, DEPTH_QUESTION, DEPTH_ANSWER)
        depth_ok = set(judgement.slots) == set(SLOTS) and all(s in SLOTS for s in judgement.missing)

        step = "slot_follow_up"
        slot_text = timed(llm.slot_follow_up, DEPTH_QUESTION, DEPTH_ANSWER, SLOT)
        slot_ok = bool(slot_text.strip()) and any(c in slot_text for c in SLOT_TARGET_CUES[SLOT])

        step = "extract_cards"
        entries = [
            KBEntry(
                session_id=sid,
                question_id=qid,
                axis="systems",
                question=question,
                answer=answer,
                created_at=now,
                session_number=number,
            )
            for sid, number, qid, question, answer in ENTRIES
        ]
        by_qid = {e.question_id: e for e in entries}
        card_drafts = timed(llm.extract_cards, entries, names)
        cards: list[KnowledgeCard] = []
        for draft in card_drafts:
            evidence = [
                Evidence(
                    session_id=by_qid[q].session_id,
                    question_id=q,
                    session_number=by_qid[q].session_number,
                    excerpt=by_qid[q].answer[:200],
                )
                for q in draft.evidence_question_ids
                if q in by_qid
            ]
            if not evidence:
                continue
            cards.append(
                KnowledgeCard(
                    id=f"c{len(cards) + 1}",
                    leaver=LEAVER,
                    role=ROLE,
                    session_id=evidence[0].session_id,
                    type=draft.type,
                    title=draft.title,
                    body=draft.body,
                    entities=draft.entities,
                    confidence=draft.confidence,
                    evidence=evidence,
                    created_at=now,
                )
            )
        if not cards:
            raise CheckFailed
        card_ids = {c.id for c in cards}

        step = "find_contradictions"
        contradictions = timed(llm.find_contradictions, cards)

        step = "verify_claims"
        citations = [
            Citation(
                session_id=e.session_id,
                question_id=e.question_id,
                axis=e.axis,
                excerpt=e.answer,
                score=1.0,
                session_number=e.session_number,
                created_at=now,
            )
            for e in entries
        ]
        verdicts = timed(llm.verify_claims, VERIFY_QUESTION, [VERIFY_GUARD_CLAIM], citations)
        verify_ok = len(verdicts) == 1 and not verdicts[0].supported

        step = "write_artifact"
        sections = timed(llm.write_artifact, ARTIFACT_KIND, cards)
        artifact_ok = bool(sections) and all(set(s.card_ids) <= card_ids for s in sections)

        step = "make_drill"
        drill_draft = timed(llm.make_drill, cards, None)
        drill_ok = bool(drill_draft.expected_points) and set(drill_draft.card_ids) <= card_ids

        step = "review_drill"
        drill = DrillScenario(id="d1", leaver=LEAVER, created_at=now, **drill_draft.model_dump())
        feedback = timed(llm.review_drill, drill, cards, drill.expected_points[0])
        review_ok = bool(feedback.covered_points or feedback.missed_points) and bool(
            feedback.feedback.strip()
        )
    except Exception as exc:  # noqa: BLE001 - report class only, never details
        print(f"LIVE LOOP FAIL {step}: {type(exc).__name__}")
        return 1

    flags = [depth_ok, slot_ok, verify_ok, artifact_ok, drill_ok, review_ok]
    print(
        f"LIVE LOOP OK entities={len(drafts)} questions={len(questions)} "
        f"depth={_ok(depth_ok)} slot_q={_ok(slot_ok)} cards={len(cards)} "
        f"contradictions={len(contradictions)} verify_guard={_ok(verify_ok)} "
        f"artifact={_ok(artifact_ok)} drill={_ok(drill_ok)} review={_ok(review_ok)} "
        f"latency_ms={round(max(latencies))}"
    )
    return 0 if all(flags) else 1


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
