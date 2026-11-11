"""LLM knowledge loop evaluation runner (``python -m handoff.evaluation_loop``).

Runs after ``handoff.evaluation`` on the same server and computes the loop
metrics (depth judgement, slot follow-ups, cards, gaps, successor loop,
artifacts, drills, privacy) over HTTP only, then writes md/json reports.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from handoff.contracts import (
    DEFAULT_ROLE,
    SLOT_TARGET_CUES,
    SLOTS,
    ArtifactKind,
    CardType,
    Slot,
    SlotState,
    topic_id,
)
from handoff.privacy import find_pii

ARTIFACT_KINDS: tuple[ArtifactKind, ...] = ("runbook", "onboarding_30d", "pitfalls_top10", "adr")
NO_MORE_ANSWER = "추가로 기억나는 것은 없습니다."
MAX_LOOP_ANSWERS = 20
NO_CARDS_DETAIL = "no cards"
FORBIDDEN_KEYS = ("score", "rank", "grade")
FLOAT_TARGETS: dict[str, float] = {
    "depth_agree": 0.8,
    "slot_target": 0.8,
    "slot_fill": 0.7,
    "card_recall": 0.7,
    "card_evidence": 0.9,
    "gap_recall": 0.6,
    "artifact_cited": 1.0,
}
TARGET_TEXT = (
    "depth_agree≥0.8 slot_target≥0.8 slot_fill≥0.7 card_recall≥0.7 card_evidence≥0.9 "
    "gap_recall≥0.6 loop_closed=m/m artifact_cited=1.0 drill_ok=m/m pii_leaks=0"
)


class LoopError(Exception):
    """HTTP or fixture failure; reported as ``LOOP ERROR <step> status=<code>``."""

    def __init__(self, step: str, status: int, detail: Any = None) -> None:
        super().__init__(f"LOOP ERROR {step} status={status}")
        self.step = step
        self.status = status
        self.detail = detail


# ---- fixture ------------------------------------------------------------------


class DepthCase(BaseModel):
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    gold: dict[Slot, SlotState]
    supplement: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> "DepthCase":
        if set(self.gold) != set(SLOTS):
            raise ValueError("gold must have all five slots")
        if self.target_slot() is None:
            raise ValueError("gold must have a missing slot")
        return self

    def target_slot(self) -> Slot | None:
        return next((s for s in SLOTS if self.gold[s] == "missing"), None)


class ExpectedCard(BaseModel):
    type: CardType
    node: str
    terms: list[str] = Field(min_length=1, max_length=2)


class LoopCase(BaseModel):
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)


class DrillAnswers(BaseModel):
    weak: str = Field(min_length=1)


class LoopFixture(BaseModel):
    depth_cases: list[DepthCase] = Field(min_length=1)
    expected_cards: list[ExpectedCard] = Field(min_length=1)
    expected_gaps: list[str] = Field(min_length=1)
    loop_cases: list[LoopCase] = Field(min_length=1)
    drill_answers: DrillAnswers


def load_fixture(path: Path) -> LoopFixture:
    return LoopFixture.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


# ---- result -------------------------------------------------------------------


class Ratio(BaseModel):
    n: int
    m: int

    def text(self) -> str:
        return f"{self.n}/{self.m}"


class LoopMetrics(BaseModel):
    depth_agree: float
    slot_target: float
    slot_fill: float
    card_recall: float
    card_evidence: float
    gap_recall: float
    loop_closed: Ratio
    artifact_cited: float
    drill_ok: Ratio
    pii_leaks: int


class DepthRow(BaseModel):
    question: str
    agreed: int
    target: Slot
    follow_slot: Slot | None
    follow_text: str | None
    target_ok: bool
    fill_ok: bool


class CardRow(BaseModel):
    type: CardType
    node: str
    terms: list[str]
    card_ids: list[str]
    found: bool
    evidence_ok: bool


class GapRow(BaseModel):
    topic: str
    found: bool


class LoopRow(BaseModel):
    question: str
    queue_item_id: str | None
    closed: bool


class ArtifactRow(BaseModel):
    kind: ArtifactKind
    sections: int
    cited: int
    generated: bool = True  # False: server answered 409 "no cards" (spec-normal)


class DrillRow(BaseModel):
    label: str
    expected: int
    covered: int
    missed: int
    ok: bool


class LoopResult(BaseModel):
    name: str
    leaver: str
    generated_at: datetime
    metrics: LoopMetrics
    unmet: list[str]
    depth: list[DepthRow]
    cards: list[CardRow]
    gaps: list[GapRow]
    loop: list[LoopRow]
    artifacts: list[ArtifactRow]
    drill: list[DrillRow]
    pii_by_source: dict[str, int]


# ---- http ---------------------------------------------------------------------


def _call(client: httpx.Client, step: str, method: str, url: str, **kwargs: Any) -> Any:
    try:
        resp = client.request(method, url, **kwargs)
    except httpx.HTTPError:
        raise LoopError(step, 0) from None
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        raise LoopError(step, resp.status_code, detail)
    try:
        return resp.json()
    except ValueError:
        raise LoopError(step, resp.status_code) from None


def _rate(n: int, m: int) -> float:
    return round(n / m, 3) if m else 0.0


# ---- steps --------------------------------------------------------------------


def run_depth(client: httpx.Client, cases: list[DepthCase]) -> list[DepthRow]:
    rows: list[DepthRow] = []
    for case in cases:
        target = case.target_slot()
        assert target is not None  # guaranteed by DepthCase validation
        body = {"question": case.question, "answer": case.answer}
        judged = _call(client, "depth_judge", "POST", "/depth/judge", json=body)
        slots = judged.get("slots") or {}
        agreed = sum(1 for s in SLOTS if slots.get(s) == case.gold[s])
        follow = _call(client, "depth_follow_up", "POST", "/depth/follow-up", json=body)
        follow_slot = follow.get("slot")
        follow_text = follow.get("text")
        target_ok = (
            follow_slot == target
            and isinstance(follow_text, str)
            and any(cue in follow_text for cue in SLOT_TARGET_CUES[target])
        )
        refill = {"question": case.question, "answer": f"{case.answer} {case.supplement}"}
        again = _call(client, "depth_refill", "POST", "/depth/judge", json=refill)
        fill_ok = (again.get("slots") or {}).get(target) == "filled"
        rows.append(
            DepthRow(
                question=case.question,
                agreed=agreed,
                target=target,
                follow_slot=follow_slot if follow_slot in SLOTS else None,
                follow_text=follow_text if isinstance(follow_text, str) else None,
                target_ok=target_ok,
                fill_ok=fill_ok,
            )
        )
    return rows


def _evidence_nodes(card: dict[str, Any]) -> set[str]:
    return {topic_id(str(e.get("question_id", ""))) for e in card.get("evidence") or []}


def judge_cards(expected: list[ExpectedCard], cards: list[dict[str, Any]]) -> list[CardRow]:
    rows: list[CardRow] = []
    for exp in expected:
        hits = [
            c
            for c in cards
            if c.get("type") == exp.type
            and all(t in f"{c.get('title', '')} {c.get('body', '')}" for t in exp.terms)
        ]
        rows.append(
            CardRow(
                type=exp.type,
                node=exp.node,
                terms=list(exp.terms),
                card_ids=[str(c.get("id", "")) for c in hits],
                found=bool(hits),
                evidence_ok=any(topic_id(exp.node) in _evidence_nodes(c) for c in hits),
            )
        )
    return rows


def judge_gaps(expected: list[str], gaps: list[dict[str, Any]]) -> list[GapRow]:
    texts = [f"{g.get('topic', '')} {g.get('detail', '')}" for g in gaps]
    return [GapRow(topic=t, found=any(t in text for text in texts)) for t in expected]


def run_loop(
    client: httpx.Client, cases: list[LoopCase], leaver: str, role: str
) -> list[LoopRow]:
    loop_leaver = f"{leaver}-loop"
    item_ids: list[str | None] = []
    answers: dict[str, str] = {}
    for case in cases:
        asked = _call(
            client, "kb_ask", "POST", "/kb/ask", json={"question": case.question, "leaver": loop_leaver}
        )
        queued = asked.get("queued")
        item_id = str(queued["id"]) if isinstance(queued, dict) and queued.get("id") else None
        item_ids.append(item_id)
        if item_id is not None:
            answers[item_id] = case.answer
    if answers:
        start = _call(
            client, "loop_start", "POST", "/sessions", json={"role": role, "leaver": loop_leaver}
        )
        session_id = str(start["session_id"])
        question: dict[str, Any] | None = start.get("question")
        for _ in range(MAX_LOOP_ANSWERS):
            if question is None or question.get("kind") not in ("queued", "follow_up"):
                break
            if question["kind"] == "queued":
                text = answers.get(str(question.get("id")), NO_MORE_ANSWER)
            else:
                text = NO_MORE_ANSWER
            body = _call(
                client, "loop_answer", "POST", f"/sessions/{session_id}/answers", json={"text": text}
            )
            question = body.get("next_question")
        _call(client, "loop_end", "POST", f"/sessions/{session_id}/end")
    rows: list[LoopRow] = []
    for case, item_id in zip(cases, item_ids, strict=True):
        closed = False
        if item_id is not None:
            ans = _call(client, "loop_query", "POST", "/kb/query", json={"question": case.question})
            closed = bool(ans.get("found")) and any(
                topic_id(str(c.get("question_id", ""))) == item_id
                for c in ans.get("citations") or []
            )
        rows.append(LoopRow(question=case.question, queue_item_id=item_id, closed=closed))
    return rows


def judge_artifact(kind: ArtifactKind, artifact: dict[str, Any], card_ids: set[str]) -> ArtifactRow:
    sections: list[dict[str, Any]] = list(artifact.get("sections") or [])
    cited = sum(
        1
        for s in sections
        if s.get("card_ids") and all(str(cid) in card_ids for cid in s["card_ids"])
    )
    return ArtifactRow(kind=kind, sections=len(sections), cited=cited)


def _has_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            any(word in str(k).lower() for word in FORBIDDEN_KEYS) or _has_forbidden_key(v)
            for k, v in value.items()
        )
    if isinstance(value, list):
        return any(_has_forbidden_key(v) for v in value)
    return False


def judge_drill(label: str, drill: dict[str, Any], feedback: dict[str, Any]) -> DrillRow:
    expected = [str(p) for p in drill.get("expected_points") or []]
    covered = [str(p) for p in feedback.get("covered_points") or []]
    missed = [str(p) for p in feedback.get("missed_points") or []]
    ok = (
        1 <= len(expected) <= 5
        and not set(covered) & set(missed)
        and sorted(covered + missed) == sorted(expected)
        and not _has_forbidden_key(drill)
        and not _has_forbidden_key(feedback)
    )
    return DrillRow(
        label=label, expected=len(expected), covered=len(covered), missed=len(missed), ok=ok
    )


def count_pii(payloads: dict[str, Any]) -> dict[str, int]:
    return {
        source: len(find_pii(json.dumps(data, ensure_ascii=False)))
        for source, data in payloads.items()
    }


def unmet_targets(m: LoopMetrics) -> list[str]:
    unmet = [name for name, target in FLOAT_TARGETS.items() if getattr(m, name) < target]
    if m.loop_closed.m == 0 or m.loop_closed.n < m.loop_closed.m:
        unmet.append("loop_closed")
    if m.drill_ok.m == 0 or m.drill_ok.n < m.drill_ok.m:
        unmet.append("drill_ok")
    if m.pii_leaks > 0:
        unmet.append("pii_leaks")
    return unmet


def run_loop_eval(
    client: httpx.Client, fixture: LoopFixture, leaver: str = "eval", role: str = DEFAULT_ROLE
) -> LoopResult:
    # (1) depth
    depth = run_depth(client, fixture.depth_cases)
    # (2) cards
    cards: list[dict[str, Any]] = _call(client, "cards", "GET", f"/leavers/{leaver}/cards")
    card_rows = judge_cards(fixture.expected_cards, cards)
    # (3) sources → questions → gaps
    _call(client, "sources_mock", "POST", f"/leavers/{leaver}/sources/mock")
    _call(client, "questions_generate", "POST", f"/leavers/{leaver}/questions/generate", json={"role": role})
    gap_report = _call(
        client, "gaps_refresh", "POST", f"/leavers/{leaver}/gaps/refresh", params={"role": role}
    )
    gap_rows = judge_gaps(fixture.expected_gaps, list(gap_report.get("gaps") or []))
    # (4) successor loop
    loop_rows = run_loop(client, fixture.loop_cases, leaver, role)
    # (5) artifacts
    card_ids = {str(c.get("id")) for c in cards}
    artifacts: dict[str, Any] = {}
    artifact_rows: list[ArtifactRow] = []
    for kind in ARTIFACT_KINDS:
        try:
            art = _call(client, f"artifact_{kind}", "POST", f"/leavers/{leaver}/artifacts/{kind}")
        except LoopError as exc:
            if exc.status != 409 or exc.detail != NO_CARDS_DETAIL:
                raise
            artifact_rows.append(ArtifactRow(kind=kind, sections=0, cited=0, generated=False))
            continue
        artifacts[kind] = art
        artifact_rows.append(judge_artifact(kind, art, card_ids))
    # (6) drill
    drill = _call(client, "drill_create", "POST", f"/leavers/{leaver}/drills", json={})
    drill_id = str(drill.get("id", ""))
    points = [str(p) for p in drill.get("expected_points") or []]
    attempts = [("weak", fixture.drill_answers.weak), ("first_point", points[0] if points else "-")]
    drill_rows = [
        judge_drill(
            label,
            drill,
            _call(client, "drill_answer", "POST", f"/drills/{drill_id}/answers", json={"text": text}),
        )
        for label, text in attempts
    ]
    # (7) privacy
    queue = _call(client, "queue", "GET", f"/leavers/{leaver}/queue")
    loop_queue = _call(client, "queue", "GET", f"/leavers/{leaver}-loop/queue")
    pii = count_pii(
        {"cards": cards, "gaps": gap_report, "artifacts": artifacts, "queue": [queue, loop_queue]}
    )
    sections = sum(r.sections for r in artifact_rows)
    found_cards = [r for r in card_rows if r.found]
    metrics = LoopMetrics(
        depth_agree=_rate(sum(r.agreed for r in depth), len(depth) * len(SLOTS)),
        slot_target=_rate(sum(r.target_ok for r in depth), len(depth)),
        slot_fill=_rate(sum(r.fill_ok for r in depth), len(depth)),
        card_recall=_rate(len(found_cards), len(card_rows)),
        card_evidence=_rate(sum(r.evidence_ok for r in found_cards), len(found_cards)),
        gap_recall=_rate(sum(r.found for r in gap_rows), len(gap_rows)),
        loop_closed=Ratio(n=sum(r.closed for r in loop_rows), m=len(loop_rows)),
        artifact_cited=_rate(sum(r.cited for r in artifact_rows), sections),
        drill_ok=Ratio(n=sum(r.ok for r in drill_rows), m=len(drill_rows)),
        pii_leaks=sum(pii.values()),
    )
    return LoopResult(
        name=leaver,
        leaver=leaver,
        generated_at=datetime.now(UTC),
        metrics=metrics,
        unmet=unmet_targets(metrics),
        depth=depth,
        cards=card_rows,
        gaps=gap_rows,
        loop=loop_rows,
        artifacts=artifact_rows,
        drill=drill_rows,
        pii_by_source=pii,
    )


# ---- reports ------------------------------------------------------------------


def summary_line(result: LoopResult) -> str:
    m = result.metrics
    return (
        f"LOOP depth_agree={m.depth_agree:.3f} slot_target={m.slot_target:.3f} "
        f"slot_fill={m.slot_fill:.3f} card_recall={m.card_recall:.3f} "
        f"card_evidence={m.card_evidence:.3f} gap_recall={m.gap_recall:.3f} "
        f"loop_closed={m.loop_closed.text()} artifact_cited={m.artifact_cited:.3f} "
        f"drill_ok={m.drill_ok.text()} pii_leaks={m.pii_leaks}"
    )


def _mark(value: bool) -> str:
    return "○" if value else "×"


def _cell(text: str | None) -> str:
    return " ".join((text or "-").split()).replace("|", "\\|")


def render_markdown(result: LoopResult) -> str:
    m = result.metrics
    metric_rows = [
        ("depth_agree", f"{m.depth_agree:.3f}", "≥ 0.8"),
        ("slot_target", f"{m.slot_target:.3f}", "≥ 0.8"),
        ("slot_fill", f"{m.slot_fill:.3f}", "≥ 0.7"),
        ("card_recall", f"{m.card_recall:.3f}", "≥ 0.7"),
        ("card_evidence", f"{m.card_evidence:.3f}", "≥ 0.9"),
        ("gap_recall", f"{m.gap_recall:.3f}", "≥ 0.6"),
        ("loop_closed", m.loop_closed.text(), "전부"),
        ("artifact_cited", f"{m.artifact_cited:.3f}", "= 1.0"),
        ("drill_ok", m.drill_ok.text(), "전부"),
        ("pii_leaks", str(m.pii_leaks), "= 0"),
    ]
    lines = [
        f"# LLM 지식 루프 평가 — {result.name}",
        "",
        f"- 퇴사자: {result.leaver} · 생성: {result.generated_at.isoformat()}",
        f"- 목표 미달: {', '.join(result.unmet) if result.unmet else '없음'}",
        "",
        "## 지표",
        "",
        "| 지표 | 값 | 목표 | 달성 |",
        "|------|----|------|------|",
    ]
    for name, value, target in metric_rows:
        lines.append(f"| {name} | {value} | {target} | {_mark(name not in result.unmet)} |")
    lines += [
        "",
        "## 답변 깊이",
        "",
        "| 질문 | 슬롯 일치 | 겨냥 슬롯 | 꼬리 질문 | 겨냥 성공 | 보충 후 채움 |",
        "|------|----------|----------|----------|----------|-------------|",
    ]
    for r in result.depth:
        lines.append(
            f"| {_cell(r.question)} | {r.agreed}/{len(SLOTS)} | {r.target} | {_cell(r.follow_text)} "
            f"| {_mark(r.target_ok)} | {_mark(r.fill_ok)} |"
        )
    lines += ["", "## 지식 카드", "", "| 타입 | 노드 | 핵심어 | 카드 | 있음 | 근거 맞음 |",
              "|------|------|--------|------|------|----------|"]
    for c in result.cards:
        lines.append(
            f"| {c.type} | {c.node} | {_cell(', '.join(c.terms))} | {_cell(', '.join(c.card_ids))} "
            f"| {_mark(c.found)} | {_mark(c.evidence_ok)} |"
        )
    lines += ["", "## 빈 구멍", "", "| 주제어 | 발견 |", "|--------|------|"]
    lines += [f"| {_cell(g.topic)} | {_mark(g.found)} |" for g in result.gaps]
    lines += ["", "## 후임 질문 루프", "", "| 질문 | 큐 항목 | 닫힘 |", "|------|--------|------|"]
    lines += [
        f"| {_cell(r.question)} | {r.queue_item_id or '-'} | {_mark(r.closed)} |" for r in result.loop
    ]
    lines += ["", "## 산출물", "", "| 종류 | 절 | 근거 맞음 | 비고 |",
              "|------|----|----------|------|"]
    lines += [
        f"| {a.kind} | {a.sections} | {a.cited} | - |" if a.generated
        else f"| {a.kind} | - | - | 생성 불가({NO_CARDS_DETAIL}) |"
        for a in result.artifacts
    ]
    lines += ["", "## 훈련", "", "| 답 | 기대 요점 | 다룸 | 빠짐 | 규칙 지킴 |",
              "|----|----------|------|------|----------|"]
    lines += [
        f"| {d.label} | {d.expected} | {d.covered} | {d.missed} | {_mark(d.ok)} |"
        for d in result.drill
    ]
    lines += ["", "## 개인정보", "", "| 응답 | 발견 수 |", "|------|--------|"]
    lines += [f"| {source} | {count} |" for source, count in result.pii_by_source.items()]
    return "\n".join(lines) + "\n"


def write_reports(result: LoopResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = json.loads(result.model_dump_json())
    (out_dir / f"{result.name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / f"{result.name}.md").write_text(render_markdown(result), encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m handoff.evaluation_loop")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--leaver", default="eval")
    parser.add_argument("--fixtures-dir", default=Path("eval/fixtures"), type=Path)
    parser.add_argument("--require-targets", action="store_true")
    return parser


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        try:
            fixture = load_fixture(args.fixtures_dir / "loop_eval.yaml")
        except (OSError, TypeError, yaml.YAMLError, ValidationError):
            raise LoopError("fixtures", 0) from None
        with httpx.Client(base_url=args.base_url, timeout=120.0, transport=transport) as client:
            result = run_loop_eval(client, fixture, leaver=args.leaver)
        result = result.model_copy(update={"name": args.name})
    except LoopError as exc:
        print(str(exc))
        return 1
    write_reports(result, args.out_dir)
    print(f"report: {args.out_dir / (args.name + '.md')}")
    if result.unmet:
        print(f"targets: {TARGET_TEXT} · unmet: {', '.join(result.unmet)}")
    print(summary_line(result))
    if args.require_targets and result.unmet:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
