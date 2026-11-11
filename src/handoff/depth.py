"""Answer depth service: slot judgement, slot follow-ups and session fill rate."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime

from handoff.contracts import (
    SESSION_NOT_FOUND,
    SLOT_FOLLOW_UP_TEMPLATES,
    DepthJudgement,
    KnowledgeNotFound,
    SessionDepthReport,
    Slot,
    SlotFollowUpResponse,
    SlotJudgement,
    TacitFollowUpResponse,
    judge_slots_by_cues,
    topic_id,
)
from handoff.knowledge_store import KnowledgeStore
from handoff.providers import LLM, ProviderError
from handoff.question_tree import FollowUp
from handoff.session import Question
from handoff.signals import TACIT_FOLLOW_UP_TEMPLATE, tacit_cues
from handoff.store import SessionStore

_TACIT_ID = re.compile(r"-t\d+$")


def utc_now() -> datetime:
    return datetime.now(UTC)


class DepthService:
    def __init__(
        self,
        llm: LLM,
        store: KnowledgeStore,
        sessions: SessionStore,
        max_followups: int,
        clock: Callable[[], datetime] = utc_now,
        *,
        tacit_followups: bool = True,
    ) -> None:
        self._llm = llm
        self._store = store
        self._sessions = sessions
        self._max_followups = max_followups
        self._clock = clock
        self._tacit_followups = tacit_followups

    def judge(self, question: str, answer: str) -> SlotJudgement:
        try:
            return self._llm.judge_depth(question, answer)
        except ProviderError:
            return judge_slots_by_cues(answer)

    def _slot_text(self, question: str, answer: str, slot: Slot) -> str:
        try:
            text = self._llm.slot_follow_up(question, answer, slot).strip()
        except ProviderError:
            text = ""
        return text or SLOT_FOLLOW_UP_TEMPLATES[slot]

    def suggest(self, question: str, answer: str) -> SlotFollowUpResponse:
        j = self.judge(question, answer)
        if not j.missing:
            return SlotFollowUpResponse(slot=None, text=None)
        slot = j.missing[0]
        return SlotFollowUpResponse(slot=slot, text=self._slot_text(question, answer, slot))

    def _tacit_text(self, question: str, answer: str, cues: list[str]) -> str:
        try:
            text = self._llm.tacit_follow_up(question, answer, cues).strip()
        except ProviderError:
            text = ""
        return text or TACIT_FOLLOW_UP_TEMPLATE.format(cue=cues[0])

    def tacit(self, question: str, answer: str) -> TacitFollowUpResponse:
        cues = tacit_cues(answer)
        if not cues:
            return TacitFollowUpResponse(cues=[], text=None)
        return TacitFollowUpResponse(cues=cues, text=self._tacit_text(question, answer, cues))

    def _tree_follow_up(
        self, question: str, text: str, candidates: list[FollowUp]
    ) -> FollowUp | None:
        try:
            return self._llm.follow_up(question, text, candidates)
        except ProviderError:
            return candidates[0]

    def decide_follow_up(
        self, session_id: str, answered: Question, text: str, candidates: list[FollowUp]
    ) -> Question | None:
        topic = topic_id(answered.id)
        thread = [
            a for a in self._sessions.answers(session_id) if topic_id(a.question_id) == topic
        ]
        if thread:
            rounds_done = len(thread) - 1
            root_question = thread[0].question
            full_answer = " ".join(a.answer for a in thread)
        else:
            rounds_done = 0
            root_question = answered.text
            full_answer = text
        j = self.judge(root_question, full_answer)

        tacit_asked = any(_TACIT_ID.search(a.question_id) for a in thread)
        cues = tacit_cues(text) if self._tacit_followups and not tacit_asked else []

        nxt: Question | None = None
        if rounds_done < self._max_followups:
            if cues:
                nxt = Question(
                    id=f"{topic}-t1",
                    axis=answered.axis,
                    text=self._tacit_text(root_question, text, cues),
                    kind="follow_up",
                )
            elif j.missing:
                slot = j.missing[0]
                nxt = Question(
                    id=f"{topic}-s{rounds_done + 1}",
                    axis=answered.axis,
                    text=self._slot_text(root_question, full_answer, slot),
                    kind="follow_up",
                )
            elif candidates and rounds_done == 0:
                f = self._tree_follow_up(root_question, text, candidates)
                if f is not None:
                    nxt = Question(id=f.id, axis=answered.axis, text=f.text, kind="follow_up")

        self._store.add_depth(
            DepthJudgement(
                session_id=session_id,
                question_id=answered.id,
                slots=j.slots,
                missing=j.missing,
                follow_up_id=nxt.id if nxt is not None else None,
                created_at=self._clock(),
            )
        )
        return nxt

    def report(self, session_id: str) -> SessionDepthReport:
        if self._sessions.get_session(session_id) is None:
            raise KnowledgeNotFound(SESSION_NOT_FOUND)
        judgements = self._store.list_depth(session_id)
        filled = sum(1 for j in judgements for s in j.slots.values() if s == "filled")
        applicable = filled + sum(len(j.missing) for j in judgements)
        rate = round(filled / applicable, 3) if applicable else 0.0
        return SessionDepthReport(
            session_id=session_id,
            judgements=judgements,
            filled=filled,
            applicable=applicable,
            slot_fill_rate=rate,
        )
