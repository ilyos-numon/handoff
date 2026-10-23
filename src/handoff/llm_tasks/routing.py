"""Routing unanswered questions to a role recorded in cards or answers (stage S7)."""

from __future__ import annotations

import json

from handoff.contracts import ChatFn, RouteBasis, RouteCandidate, RouteDraft, containment
from handoff.signals import ROUTE_MIN_MATCH

ROUTE_MAX_TOKENS = 200
CANDIDATE_TEXT_MAX = 300
_BASIS_ORDER: dict[RouteBasis, int] = {"contact_card": 0, "people_answer": 1}

ROUTE_SYSTEM = (
    "당신은 인수인계 지식에서 답을 찾지 못한 후임의 질문을 누구에게 물어야 하는지 안내하는 도우미입니다. "
    "아래 후보는 퇴사자가 남긴 연락처 카드와 담당 답변이며, 후보마다 기록된 역할이 있습니다. "
    "질문 주제와 가장 관련 있는 후보 하나를 고르고, 그 후보에 적힌 역할 중 하나를 그대로 씁니다. "
    "후보에 없는 역할을 만들지 않습니다. 근거에 없는 사실·이름을 지어내지 않습니다. "
    "사람 이름 대신 역할명을 씁니다. 질문과 관련 있는 후보가 없으면 ref 를 null 로 둡니다. "
    '반드시 {"ref": "<후보 ref 또는 null>", "role": "<역할>"} 형식의 JSON 만 출력합니다.'
)


def fake_route_question(question: str, candidates: list[RouteCandidate]) -> RouteDraft | None:
    scored = [
        (containment(question, cand.text), _BASIS_ORDER[cand.basis], index, cand)
        for index, cand in enumerate(candidates)
    ]
    hits = [s for s in scored if s[0] >= ROUTE_MIN_MATCH]
    if not hits:
        return None
    hits.sort(key=lambda s: (-s[0], s[1], s[2]))
    best = hits[0][3]
    return RouteDraft(ref=best.ref, role=best.roles[0])


def _candidate_line(cand: RouteCandidate) -> str:
    return f"[{cand.ref}] ({cand.basis}) {cand.text[:CANDIDATE_TEXT_MAX]} · 역할: {', '.join(cand.roles)}"


class _Fallback(Exception):
    pass


def _parse_route(content: str, candidates: list[RouteCandidate]) -> RouteDraft | None:
    try:
        parsed = json.loads(content)
    except ValueError:
        raise _Fallback from None
    if not isinstance(parsed, dict) or "ref" not in parsed:
        raise _Fallback
    ref = parsed.get("ref")
    if ref is None:
        return None
    role = parsed.get("role")
    if not isinstance(ref, str) or not isinstance(role, str):
        raise _Fallback
    by_ref = {c.ref: c for c in candidates}
    cand = by_ref.get(ref.strip())
    if cand is None or role.strip() not in cand.roles:
        raise _Fallback
    return RouteDraft(ref=cand.ref, role=role.strip())


def qwen_route_question(
    chat: ChatFn, question: str, candidates: list[RouteCandidate]
) -> RouteDraft | None:
    if not candidates:
        return None
    user = (
        f"질문: {question}\n후보 목록 (한 줄에 후보 하나):\n"
        + "\n".join(_candidate_line(c) for c in candidates)
    )
    content = chat(ROUTE_SYSTEM, user, ROUTE_MAX_TOKENS, json_mode=True)
    try:
        return _parse_route(content, candidates)
    except _Fallback:
        return fake_route_question(question, candidates)
