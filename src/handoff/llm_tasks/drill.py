"""Incident drill generation and review (implemented in stage S10)."""

from __future__ import annotations

import json

from pydantic import ValidationError

from handoff.contracts import (
    ChatFn,
    DrillDraft,
    DrillFeedbackDraft,
    DrillScenario,
    KnowledgeCard,
    containment,
    split_sentences,
)

DRILL_CARD_TYPES = ("pitfall", "procedure", "decision")
MAX_EXPECTED_POINTS = 5
MAX_EXTRA_CARDS = 2
COVERED_THRESHOLD = 0.5
DRILL_ASK = "무엇부터 확인하고 어떤 순서로 대응하나요? 어떤 역할에게 알리나요?"

MAKE_DRILL_SYSTEM = (
    "당신은 퇴사자가 남긴 지식 카드로 후임자의 장애 대응 연습 문제를 만드는 도우미입니다. "
    "카드에 적힌 함정·절차·결정만 근거로 장애 상황 시나리오 하나를 만듭니다. "
    "근거에 없는 사실·이름을 지어내지 않습니다. 사람 이름 대신 역할명을 씁니다. "
    "기대 요점은 1~5개, 각 요점은 카드 본문에 근거한 한 문장입니다. "
    "기준 카드가 주어지면 그 카드를 반드시 근거에 넣습니다. "
    '반드시 {"title": "<제목>", "situation": "<상황 설명>", "ask": "<묻는 말>", '
    '"expected_points": ["<요점>", ...], "card_ids": ["<카드 id>", ...]} 형식의 JSON 만 출력합니다.'
)

REVIEW_DRILL_SYSTEM = (
    "당신은 후임자의 장애 대응 연습 답을 읽고 스스로 돌아보게 돕는 도우미입니다. "
    "기대 요점마다 답이 그 요점을 다뤘는지 판단합니다. 점수나 등급을 매기지 않습니다. "
    "근거에 없는 사실·이름을 지어내지 않습니다. 사람 이름 대신 역할명을 씁니다. "
    "covered_points 와 missed_points 에는 기대 요점 문장을 그대로 옮겨 적습니다. "
    '반드시 {"covered_points": ["<요점>", ...], "missed_points": ["<요점>", ...], '
    '"feedback": "<한국어 2문장 이내 피드백>"} 형식의 JSON 만 출력합니다.'
)


def _pick_seed(candidates: list[KnowledgeCard]) -> KnowledgeCard | None:
    pitfalls = [c for c in candidates if c.type == "pitfall"]
    if pitfalls:
        return min(pitfalls, key=lambda c: (-c.confidence, _id_num(c.id)))
    for kind in ("procedure", "decision"):
        found = sorted((c for c in candidates if c.type == kind), key=lambda c: _id_num(c.id))
        if found:
            return found[0]
    return None


def _id_num(card_id: str) -> int:
    digits = card_id[1:]
    return int(digits) if digits.isdigit() else 0


def fake_make_drill(cards: list[KnowledgeCard], seed: KnowledgeCard | None) -> DrillDraft:
    candidates = [c for c in cards if c.type in DRILL_CARD_TYPES]
    base = seed if seed is not None else _pick_seed(candidates)
    if base is None:
        raise ValueError("no drill cards")
    seed_entities = set(base.entities)
    extras = [
        c
        for c in sorted(candidates, key=lambda c: _id_num(c.id))
        if c.id != base.id and seed_entities & set(c.entities)
    ][:MAX_EXTRA_CARDS]
    used = [base, *extras]
    points: list[str] = []
    for card in used:
        sentences = split_sentences(card.body)
        first = sentences[0] if sentences else card.body.strip()
        if first and first not in points:
            points.append(first)
    return DrillDraft(
        title=f"훈련: {base.title}",
        situation=f"다음 상황이 생겼습니다: {base.title}. 지금 당번은 당신입니다.",
        ask=DRILL_ASK,
        expected_points=points[:MAX_EXPECTED_POINTS],
        card_ids=[c.id for c in used],
    )


def _card_lines(cards: list[KnowledgeCard]) -> str:
    return "\n".join(f"[{c.id}] ({c.type}) {c.title}: {c.body}" for c in cards)


def qwen_make_drill(
    chat: ChatFn, cards: list[KnowledgeCard], seed: KnowledgeCard | None
) -> DrillDraft:
    seed_line = f"기준 카드: {seed.id}\n" if seed is not None else ""
    user = f"{seed_line}카드:\n{_card_lines(cards)}"
    content = chat(MAKE_DRILL_SYSTEM, user, 800, json_mode=True)
    try:
        return DrillDraft.model_validate(json.loads(content))
    except (ValueError, ValidationError):
        return fake_make_drill(cards, seed)


def fake_review_drill(
    drill: DrillScenario, cards: list[KnowledgeCard], answer: str
) -> DrillFeedbackDraft:
    covered = [p for p in drill.expected_points if containment(p, answer) >= COVERED_THRESHOLD]
    missed = [p for p in drill.expected_points if p not in covered]
    ids = ", ".join(drill.card_ids)
    feedback = (
        f"다룬 요점 {len(covered)}개, 더 살펴볼 요점 {len(missed)}개입니다. "
        f"근거 카드 {ids} 를 읽어 보세요."
    )
    return DrillFeedbackDraft(covered_points=covered, missed_points=missed, feedback=feedback)


def qwen_review_drill(
    chat: ChatFn, drill: DrillScenario, cards: list[KnowledgeCard], answer: str
) -> DrillFeedbackDraft:
    points = "\n".join(f"- {p}" for p in drill.expected_points)
    user = (
        f"상황: {drill.situation}\n묻는 말: {drill.ask}\n기대 요점:\n{points}\n"
        f"근거 카드:\n{_card_lines(cards)}\n후임 답: {answer}"
    )
    content = chat(REVIEW_DRILL_SYSTEM, user, 600, json_mode=True)
    try:
        draft = DrillFeedbackDraft.model_validate(json.loads(content))
    except (ValueError, ValidationError):
        return fake_review_drill(drill, cards, answer)
    if not draft.feedback.strip():
        return fake_review_drill(drill, cards, answer)
    return draft
