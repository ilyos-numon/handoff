"""Routing unanswered questions to a role (implemented in stage S7)."""

from __future__ import annotations

from handoff.contracts import ChatFn, RouteCandidate, RouteDraft


def fake_route_question(question: str, candidates: list[RouteCandidate]) -> RouteDraft | None:
    raise NotImplementedError("routing: implemented in stage S7")


def qwen_route_question(
    chat: ChatFn, question: str, candidates: list[RouteCandidate]
) -> RouteDraft | None:
    raise NotImplementedError("routing: implemented in stage S7")
