"""Unanswered-question routing: which role to ask, based on contact cards and answers."""

from __future__ import annotations

from handoff.contracts import AskRoute, RouteCandidate, RouteDraft
from handoff.knowledge_store import KnowledgeStore
from handoff.llm_tasks.routing import fake_route_question
from handoff.providers import LLM, ProviderError
from handoff.signals import extract_roles
from handoff.store import SessionStore

ROUTE_CANDIDATES_MAX = 20


class QuestionRouter:
    def __init__(self, llm: LLM, store: KnowledgeStore, sessions: SessionStore) -> None:
        self._llm = llm
        self._store = store
        self._sessions = sessions

    def candidates(self, leaver: str, role: str) -> list[RouteCandidate]:
        out: list[RouteCandidate] = []
        for card in self._store.list_cards(leaver, type="contact"):
            text = f"{card.title} {card.body}"
            roles = extract_roles(text)
            if roles:
                out.append(
                    RouteCandidate(ref=card.id, basis="contact_card", text=text, roles=roles)
                )
        for entry in self._sessions.leaver_answers(leaver, role, ended_only=True):
            if entry.axis != "people":
                continue
            roles = extract_roles(entry.answer)
            if roles:
                out.append(
                    RouteCandidate(
                        ref=f"a:{entry.session_id}:{entry.question_id}",
                        basis="people_answer",
                        text=entry.answer,
                        roles=roles,
                    )
                )
        return out[:ROUTE_CANDIDATES_MAX]

    def route(self, question: str, leaver: str, role: str) -> AskRoute | None:
        cands = self.candidates(leaver, role)
        if not cands:
            return None
        draft: RouteDraft | None
        try:
            draft = self._llm.route_question(question, cands)
        except ProviderError:
            draft = fake_route_question(question, cands)
        if draft is None:
            return None
        by_ref = {c.ref: c for c in cands}
        cand = by_ref.get(draft.ref)
        if cand is None or draft.role not in cand.roles:
            draft = fake_route_question(question, cands)
            if draft is None:
                return None
            cand = by_ref[draft.ref]
        question_id = cand.ref.rsplit(":", 1)[-1]
        return AskRoute(
            ask_role=draft.role,
            reason=f"'{draft.role}' 이 이 주제의 연락처로 기록되어 있습니다.",
            basis=cand.basis,
            card_ids=[cand.ref] if cand.basis == "contact_card" else [],
            question_ids=[question_id] if cand.basis == "people_answer" else [],
        )
