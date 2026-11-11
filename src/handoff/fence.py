"""Chesterton's fence: why a setting or procedure exists and what breaks without it."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from handoff.contracts import (
    CARD_TYPE_AXIS,
    FENCE_QUESTION_TEMPLATE,
    FENCE_QUEUE_TEMPLATE,
    FENCE_REASON,
    Certainty,
    Citation,
    ClaimVerdict,
    FenceDraft,
    FencePoint,
    FenceRequest,
    FenceResponse,
    InvalidationCondition,
    KnowledgeCard,
    containment,
)
from handoff.knowledge_store import KnowledgeStore
from handoff.providers import LLM, ProviderError
from handoff.signals import FENCE_MIN_MATCH, FENCE_POINTS_MAX, FENCE_TOP_CARDS
from handoff.verify import guess_axis


def utc_now() -> datetime:
    return datetime.now(UTC)


def _card_num(card: KnowledgeCard) -> int:
    digits = card.id[1:]
    return int(digits) if digits.isdigit() else 0


def _unsupported(claims: list[str]) -> list[ClaimVerdict]:
    return [ClaimVerdict(claim=c, supported=False, answers_question=False) for c in claims]


class FenceService:
    def __init__(
        self, llm: LLM, store: KnowledgeStore, clock: Callable[[], datetime] = utc_now
    ) -> None:
        self._llm = llm
        self._store = store
        self._clock = clock

    def _top(self, req: FenceRequest) -> list[tuple[KnowledgeCard, float]]:
        scored = [
            (c, containment(req.subject, f"{c.title} {c.body} {' '.join(c.entities)}"))
            for c in self._store.list_cards(req.leaver)
        ]
        hits = [(c, s) for c, s in scored if s >= FENCE_MIN_MATCH]
        hits.sort(key=lambda cs: (-cs[1], _card_num(cs[0])))
        return hits[:FENCE_TOP_CARDS]

    def _verdicts(
        self, subject: str, claims: list[str], citations: list[Citation]
    ) -> list[ClaimVerdict]:
        if not claims:
            return []
        question = FENCE_QUESTION_TEMPLATE.format(subject=subject)
        try:
            verdicts = self._llm.verify_claims(question, claims, citations)
        except ProviderError:
            return _unsupported(claims)
        if len(verdicts) != len(claims):
            return _unsupported(claims)
        return verdicts

    def ask(self, req: FenceRequest) -> FenceResponse:
        top = self._top(req)
        if not top:
            return self._response(req, False, [], [], [], 0)
        top_ids = {c.id for c, _ in top}
        draft: FenceDraft = self._llm.explain_fence(req.subject, [c for c, _ in top])
        removed = 0

        def in_top(points: list[FencePoint]) -> list[FencePoint]:
            nonlocal removed
            kept = [p for p in points if set(p.card_ids) <= top_ids]
            removed += len(points) - len(kept)
            return kept[:FENCE_POINTS_MAX]

        reasons = in_top(draft.reasons)
        risks = in_top(draft.risks)

        citations = [
            Citation(
                session_id=c.evidence[0].session_id,
                question_id=c.evidence[0].question_id,
                axis=CARD_TYPE_AXIS[c.type],
                excerpt=c.evidence[0].excerpt,
                score=round(s, 4),
                session_number=c.evidence[0].session_number,
            )
            for c, s in top
        ]
        points = reasons + risks
        verdicts = self._verdicts(req.subject, [p.text for p in points], citations)
        reason_verdicts = verdicts[: len(reasons)]
        risk_verdicts = verdicts[len(reasons) :]
        kept_reasons = [(p, v) for p, v in zip(reasons, reason_verdicts) if v.supported]
        kept_risks = [p for p, v in zip(risks, risk_verdicts) if v.supported]
        removed += len(points) - len(kept_reasons) - len(kept_risks)

        found = any(v.answers_question for _, v in kept_reasons)
        final_reasons = [p for p, _ in kept_reasons]
        if not found:
            removed += len(final_reasons)
            final_reasons = []
        return self._response(req, found, final_reasons, kept_risks, [c for c, _ in top], removed)

    def _response(
        self,
        req: FenceRequest,
        found: bool,
        reasons: list[FencePoint],
        risks: list[FencePoint],
        top: list[KnowledgeCard],
        removed: int,
    ) -> FenceResponse:
        cited_ids = {cid for p in reasons + risks for cid in p.card_ids}
        cards = sorted((c for c in top if c.id in cited_ids), key=_card_num)
        conditions: list[InvalidationCondition] = []
        for card in cards:
            for cond in card.invalidated_when:
                if cond not in conditions:
                    conditions.append(cond)
        certainty: Certainty | None
        if not cards:
            certainty = None
        elif any(c.certainty == "uncertain" for c in cards):
            certainty = "uncertain"
        else:
            certainty = "certain"
        queued = None
        if not found:
            queued, _ = self._store.enqueue(
                req.leaver,
                req.role,
                "successor",
                guess_axis(req.subject),
                FENCE_QUEUE_TEMPLATE.format(subject=req.subject),
                FENCE_REASON,
                None,
                self._clock(),
            )
        return FenceResponse(
            subject=req.subject,
            found=found,
            reasons=reasons,
            risks=risks,
            cards=cards,
            invalidated_when=conditions,
            certainty=certainty,
            claims_removed=removed,
            queued=queued,
        )
