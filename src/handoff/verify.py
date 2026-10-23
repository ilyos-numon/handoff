"""Claim verification for KB answers and the successor unanswered-question loop."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from handoff.contracts import (
    DEFAULT_ROLE,
    AskRequest,
    AskResponse,
    AskRoute,
    Citation,
    ClaimVerdict,
    QueueItem,
    VerifyOutcome,
    split_sentences,
)
from handoff.kb import KnowledgeBase
from handoff.knowledge_store import KnowledgeStore
from handoff.providers import LLM, ProviderError
from handoff.question_tree import Axis
from handoff.signals import uncertain_cues

if TYPE_CHECKING:
    from handoff.routing import QuestionRouter

SUCCESSOR_REASON = "후임 미답 질문"

_AXIS_RULES: tuple[tuple[re.Pattern[str], Axis], ...] = (
    (re.compile(r"연락|누구|담당|문의"), "people"),
    (re.compile(r"장애|사고|오류|실패"), "incidents"),
    (re.compile(r"매주|매일|매달|배치|정산|절차|신청"), "routines"),
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _unsupported(claims: list[str]) -> list[ClaimVerdict]:
    return [ClaimVerdict(claim=c, supported=False, answers_question=False) for c in claims]


class ClaimVerifier:
    def __init__(self, llm: LLM) -> None:
        self._llm = llm

    def verify(self, question: str, answer: str, citations: list[Citation]) -> VerifyOutcome:
        claims = split_sentences(answer)
        if not claims:
            return VerifyOutcome(answer=None, claims=[], kept=0, removed=0)
        try:
            verdicts = self._llm.verify_claims(question, claims, citations)
        except ProviderError:
            verdicts = _unsupported(claims)
        if len(verdicts) != len(claims):
            verdicts = _unsupported(claims)
        keep = [c for c, v in zip(claims, verdicts) if v.supported]
        if any(v.supported and v.answers_question for v in verdicts):
            return VerifyOutcome(
                answer=" ".join(keep),
                claims=verdicts,
                kept=len(keep),
                removed=len(claims) - len(keep),
            )
        return VerifyOutcome(answer=None, claims=verdicts, kept=0, removed=len(claims))

    def __call__(
        self, question: str, answer: str, citations: list[Citation]
    ) -> tuple[str | None, int]:
        outcome = self.verify(question, answer, citations)
        return outcome.answer, outcome.removed


def guess_axis(text: str) -> Axis:
    for pattern, axis in _AXIS_RULES:
        if pattern.search(text):
            return axis
    return "systems"


class SuccessorService:
    def __init__(
        self,
        store: KnowledgeStore,
        clock: Callable[[], datetime] = utc_now,
        *,
        router: QuestionRouter | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._router = router

    def ask(self, kb: KnowledgeBase, req: AskRequest, role: str = DEFAULT_ROLE) -> AskResponse:
        ans = kb.query(req.question, leaver=req.leaver)
        if ans.found:
            uncertain = any(uncertain_cues(c.excerpt) for c in ans.citations)
            return AskResponse(
                answer=ans,
                queued=None,
                route=None,
                certainty="uncertain" if uncertain else "certain",
            )
        item, _ = self._store.enqueue(
            req.leaver,
            role,
            "successor",
            guess_axis(req.question),
            req.question,
            SUCCESSOR_REASON,
            None,
            self._clock(),
        )
        route: AskRoute | None = None
        if self._router is not None:
            try:
                route = self._router.route(req.question, req.leaver, role)
            except ProviderError:
                route = None
        return AskResponse(answer=ans, queued=item, route=route, certainty=None)

    def list(self, leaver: str) -> list[QueueItem]:
        return self._store.list_queue(leaver, source="successor")
