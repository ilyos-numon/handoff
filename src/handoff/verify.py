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
    topic_id,
)
from handoff.kb import EXCERPT_MAX, KnowledgeBase
from handoff.knowledge_store import KnowledgeStore
from handoff.privacy import scrub_contacts
from handoff.providers import LLM, ProviderError
from handoff.question_tree import Axis
from handoff.signals import uncertain_cues

if TYPE_CHECKING:
    from handoff.routing import QuestionRouter
    from handoff.store import SessionStore

SUCCESSOR_REASON = "후임 미답 질문"

_AXIS_RULES: tuple[tuple[re.Pattern[str], Axis], ...] = (
    (re.compile(r"연락|누구|담당|문의"), "people"),
    (re.compile(r"장애|사고|오류|실패"), "incidents"),
    (re.compile(r"매주|매일|매달|배치|정산|절차|신청"), "routines"),
)


_SPACES_RE = re.compile(r"\s+")
_TRAILING_RE = re.compile(r"[?.!？\s]+$")


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_question(text: str) -> str:
    """Same-sentence key: collapse whitespace and drop trailing ``?.!？``. Case is kept."""
    return _TRAILING_RE.sub("", _SPACES_RE.sub(" ", text.strip()))


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
        sessions: SessionStore | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._router = router
        self._sessions = sessions

    def linked_citations(
        self, leaver: str, question: str
    ) -> tuple[QueueItem | None, list[Citation]]:
        """Answered successor item with the same sentence, and the leaver's ended answers to it."""
        key = normalize_question(question)
        items = [
            q
            for q in self._store.list_queue(leaver, status="answered", source="successor")
            if normalize_question(q.text) == key
        ]
        items.sort(key=lambda q: int(q.id[1:]), reverse=True)
        if not items or self._sessions is None:
            return (items[0] if items else None), []
        for item in items:
            # answers table text is not scrubbed, so excerpts go through scrub_contacts here
            cits = [
                Citation(
                    session_id=e.session_id,
                    question_id=e.question_id,
                    axis=e.axis,
                    excerpt=scrub_contacts(e.answer)[:EXCERPT_MAX],
                    score=1.0,
                    session_number=e.session_number,
                    created_at=e.created_at,
                )
                for e in self._sessions.leaver_answers(leaver, item.role, ended_only=True)
                if topic_id(e.question_id) == item.id
            ]
            if cits:
                return item, cits
        return items[0], []

    def ask(self, kb: KnowledgeBase, req: AskRequest, role: str = DEFAULT_ROLE) -> AskResponse:
        answered, linked = self.linked_citations(req.leaver, req.question)
        if linked:
            seen = {(c.session_id, c.question_id) for c in linked}
            extra = [
                c
                for c in kb.search(req.question, leaver=req.leaver)
                if (c.session_id, c.question_id) not in seen
            ]
            ans = kb.answer_from(req.question, linked + extra, fallback_excerpt=True)
        else:
            ans = kb.query(req.question, leaver=req.leaver)
        if ans.found:
            uncertain = any(uncertain_cues(c.excerpt) for c in ans.citations)
            return AskResponse(
                answer=ans,
                queued=None,
                route=None,
                certainty="uncertain" if uncertain else "certain",
            )
        if answered is not None:
            # Already answered (session may still be open): do not queue the same sentence again.
            item = answered
        else:
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
