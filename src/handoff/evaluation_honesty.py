"""Knowledge honesty evaluation runner (``python -m handoff.evaluation_honesty``).

Drives a fixture leaver through tacit follow-ups, pre-mortem questions and two
interview sessions over HTTP, then grades fence answers, uncertainty marks,
question routing, at-risk cards, the pre-mortem artifact and privacy rules.
Writes md/json reports.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from handoff.contracts import (
    NO_CARDS,
    PREMORTEM_QUESTIONS,
    ChangeEvent,
    topic_id,
)
from handoff.privacy import find_pii
from handoff.signals import TACIT_CUES, TACIT_TARGET_CUES, extract_roles, tacit_cues

NO_MORE_ANSWER = "추가로 기억나는 것은 없습니다."
MAX_SESSION_ANSWERS = 40
FIRST_NODES: tuple[str, ...] = ("sys-01", "rou-01", "inc-01", "ppl-01", "sys-02", "rou-02")
SURVEILLANCE_KEYS: frozenset[str] = frozenset({"rank", "grade", "rating", "person", "owner"})
SECTION_SIGN = "징후:"
SECTION_RESPONSE = "대응:"
HONESTY_TARGETS: dict[str, tuple[Literal["min", "max"], float]] = {
    "fence_accuracy": ("min", 0.8),
    "fence_fabricated": ("max", 0),
    "uncertain_detect": ("min", 0.8),
    "tacit_followup": ("min", 0.8),
    "routing": ("min", 0.75),
    "at_risk": ("min", 0.7),
    "premortem_cited": ("min", 1.0),
    "pii_leaks": ("max", 0),
    "surveillance_keys": ("max", 0),
}


class HonestyError(Exception):
    """HTTP or fixture failure; reported as ``HONESTY ERROR <step> status=<code>``."""

    def __init__(self, step: str, status: int, detail: Any = None) -> None:
        super().__init__(f"HONESTY ERROR {step} status={status}")
        self.step = step
        self.status = status
        self.detail = detail


# ---- fixture ------------------------------------------------------------------


class SessionPlan(BaseModel):
    nodes: int = Field(ge=1)


class NodeAnswer(BaseModel):
    main: str = Field(min_length=1)
    follow_up: str = Field(min_length=1)


class TacitCase(BaseModel):
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    tacit: bool


class UncertainCase(BaseModel):
    term: str = Field(min_length=1)
    uncertain: bool


class AtRiskCase(BaseModel):
    event: ChangeEvent
    expect_terms: list[str] = Field(min_length=1)
    absent_terms: list[str] = []


class FenceCase(BaseModel):
    subject: str = Field(min_length=1)
    expect_found: bool
    expect_terms: list[str] = []


class RoutingCase(BaseModel):
    question: str = Field(min_length=1)
    expect_role_terms: list[str] = Field(min_length=1)


class HonestyFixture(BaseModel):
    role: str = Field(min_length=1)
    sessions: list[SessionPlan] = Field(min_length=1)
    answers: dict[str, NodeAnswer]
    premortem_answers: list[str] = Field(min_length=3, max_length=3)
    tacit_cases: list[TacitCase] = Field(min_length=8)
    uncertain_cases: list[UncertainCase] = Field(min_length=6)
    at_risk_cases: list[AtRiskCase] = Field(min_length=3)
    fence_cases: list[FenceCase] = Field(min_length=5)
    routing_cases: list[RoutingCase] = Field(min_length=4)

    @model_validator(mode="after")
    def _check(self) -> "HonestyFixture":
        missing = [n for n in FIRST_NODES if n not in self.answers]
        if missing:
            raise ValueError(f"answers missing nodes: {', '.join(missing)}")
        true_tacit = [c for c in self.tacit_cases if c.tacit]
        false_tacit = [c for c in self.tacit_cases if not c.tacit]
        if len(true_tacit) < 5 or len(false_tacit) < 3:
            raise ValueError("tacit_cases need >= 5 true and >= 3 false")
        if any(not tacit_cues(c.answer) for c in true_tacit):
            raise ValueError("tacit true case without a tacit cue")
        if any(tacit_cues(c.answer) for c in false_tacit):
            raise ValueError("tacit false case with a tacit cue")
        covered = {cue for c in true_tacit for cue in tacit_cues(c.answer)}
        if set(TACIT_CUES) - covered:
            raise ValueError("tacit true cases must cover every tacit cue")
        if sum(c.uncertain for c in self.uncertain_cases) < 3 or sum(
            not c.uncertain for c in self.uncertain_cases
        ) < 3:
            raise ValueError("uncertain_cases need >= 3 true and >= 3 false")
        texts = self.answer_texts()
        for case in self.uncertain_cases:
            if sum(case.term in t for t in texts) != 1:
                raise ValueError(f"uncertain term must appear in exactly one answer: {case.term}")
        events = [c.event for c in self.at_risk_cases]
        if len(set(events)) != len(events):
            raise ValueError("at_risk_cases need distinct events")
        if sum(c.expect_found for c in self.fence_cases) < 3 or sum(
            not c.expect_found for c in self.fence_cases
        ) < 2:
            raise ValueError("fence_cases need >= 3 found and >= 2 not found")
        if any(c.expect_found and not c.expect_terms for c in self.fence_cases):
            raise ValueError("found fence case needs expect_terms")
        return self

    def answer_texts(self) -> list[str]:
        texts: list[str] = []
        for a in self.answers.values():
            texts += [a.main, a.follow_up]
        return texts + list(self.premortem_answers)


def load_fixture(path: Path) -> HonestyFixture:
    return HonestyFixture.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


# ---- result -------------------------------------------------------------------


class HonestyMetrics(BaseModel):
    fence_accuracy: float
    fence_fabricated: int
    uncertain_detect: float
    tacit_followup: float
    routing: float
    at_risk: float
    premortem_cited: float
    pii_leaks: int
    surveillance_keys: int


class SessionRow(BaseModel):
    number: int
    session_id: str
    nodes: list[str]
    answers: int


class TacitRow(BaseModel):
    question: str
    tacit: bool
    cues: list[str]
    text: str | None
    ok: bool


class UncertainRow(BaseModel):
    term: str
    uncertain: bool
    card_id: str | None
    certainty: str | None
    in_unknown: bool
    ok: bool


class AtRiskRow(BaseModel):
    event: ChangeEvent
    card_ids: list[str]
    found_terms: list[str]
    leaked_terms: list[str]
    hits: int
    total: int


class FenceRow(BaseModel):
    subject: str
    expect_found: bool
    found: bool
    points: int
    queued: bool
    fabricated: bool
    ok: bool


class RoutingRow(BaseModel):
    question: str
    found: bool
    ask_role: str | None
    ok: bool


class PremortemRow(BaseModel):
    heading: str
    card_ids: list[str]
    ok: bool


class HonestyResult(BaseModel):
    name: str
    leaver: str
    generated_at: datetime
    metrics: HonestyMetrics
    unmet: list[str]
    sessions: list[SessionRow]
    tacit: list[TacitRow]
    uncertain: list[UncertainRow]
    at_risk: list[AtRiskRow]
    fence: list[FenceRow]
    routing: list[RoutingRow]
    premortem: list[PremortemRow]
    pii_by_source: dict[str, int]
    surveillance_by_source: dict[str, int]


# ---- http ---------------------------------------------------------------------


def _call(client: httpx.Client, step: str, method: str, url: str, **kwargs: Any) -> Any:
    try:
        resp = client.request(method, url, **kwargs)
    except httpx.HTTPError:
        raise HonestyError(step, 0) from None
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        raise HonestyError(step, resp.status_code, detail)
    try:
        return resp.json()
    except ValueError:
        raise HonestyError(step, resp.status_code) from None


def _rate(n: int, m: int) -> float:
    return round(n / m, 3) if m else 0.0


def _card_text(card: dict[str, Any]) -> str:
    return f"{card.get('title', '')} {card.get('body', '')}"


def _card_num(card: dict[str, Any]) -> int:
    digits = str(card.get("id", ""))[1:]
    return int(digits) if digits.isdigit() else 0


# ---- judges -------------------------------------------------------------------


def judge_tacit(case: TacitCase, resp: dict[str, Any]) -> TacitRow:
    cues = [str(c) for c in resp.get("cues") or []]
    text = resp.get("text")
    text = text if isinstance(text, str) else None
    if case.tacit:
        ok = bool(cues) and text is not None and any(c in text for c in TACIT_TARGET_CUES)
    else:
        ok = not cues and text is None
    return TacitRow(question=case.question, tacit=case.tacit, cues=cues, text=text, ok=ok)


def judge_uncertain(
    case: UncertainCase, cards: list[dict[str, Any]], gap_report: dict[str, Any]
) -> UncertainRow:
    ordered = sorted(cards, key=_card_num)
    card = next((c for c in ordered if case.term in _card_text(c)), None)
    unknown_ids = {
        str(cid) for area in gap_report.get("unknown_areas") or [] for cid in area.get("card_ids") or []
    }
    if card is None:
        return UncertainRow(
            term=case.term, uncertain=case.uncertain, card_id=None, certainty=None,
            in_unknown=False, ok=False,
        )
    card_id = str(card.get("id", ""))
    certainty = card.get("certainty")
    in_unknown = card_id in unknown_ids
    expected = "uncertain" if case.uncertain else "certain"
    ok = certainty == expected and (in_unknown or not case.uncertain)
    return UncertainRow(
        term=case.term,
        uncertain=case.uncertain,
        card_id=card_id,
        certainty=certainty if isinstance(certainty, str) else None,
        in_unknown=in_unknown,
        ok=ok,
    )


def judge_at_risk(case: AtRiskCase, report: dict[str, Any]) -> AtRiskRow:
    cards = [item.get("card") or {} for item in report.get("cards") or []]
    texts = [_card_text(c) for c in cards]
    found = [t for t in case.expect_terms if any(t in x for x in texts)]
    leaked = [t for t in case.absent_terms if any(t in x for x in texts)]
    return AtRiskRow(
        event=case.event,
        card_ids=[str(c.get("id", "")) for c in cards],
        found_terms=found,
        leaked_terms=leaked,
        hits=len(found) + len(case.absent_terms) - len(leaked),
        total=len(case.expect_terms) + len(case.absent_terms),
    )


def judge_fence(case: FenceCase, resp: dict[str, Any], card_ids: set[str]) -> FenceRow:
    found = bool(resp.get("found"))
    points: list[dict[str, Any]] = list(resp.get("reasons") or []) + list(resp.get("risks") or [])
    queued = resp.get("queued") is not None
    fabricated = found and not case.expect_found
    if case.expect_found:
        texts = [str(p.get("text", "")) for p in points]
        texts += [_card_text(c) for c in resp.get("cards") or []]
        ok = (
            found
            and all(
                p.get("card_ids") and {str(c) for c in p["card_ids"]} <= card_ids for p in points
            )
            and any(term in t for term in case.expect_terms for t in texts)
        )
    else:
        ok = not found and queued
    return FenceRow(
        subject=case.subject,
        expect_found=case.expect_found,
        found=found,
        points=len(points),
        queued=queued,
        fabricated=fabricated,
        ok=ok,
    )


def judge_routing(case: RoutingCase, resp: dict[str, Any]) -> RoutingRow:
    answer = resp.get("answer") or {}
    found = bool(answer.get("found"))
    route = resp.get("route")
    ask_role = route.get("ask_role") if isinstance(route, dict) else None
    ask_role = ask_role if isinstance(ask_role, str) else None
    ok = (
        not found
        and ask_role is not None
        and any(t in ask_role for t in case.expect_role_terms)
        and extract_roles(ask_role) == [ask_role]
    )
    return RoutingRow(question=case.question, found=found, ask_role=ask_role, ok=ok)


def judge_premortem(artifact: dict[str, Any] | None, card_ids: set[str]) -> list[PremortemRow]:
    if artifact is None:
        return []
    rows: list[PremortemRow] = []
    for s in artifact.get("sections") or []:
        ids = [str(c) for c in s.get("card_ids") or []]
        body = str(s.get("body", ""))
        ok = bool(ids) and set(ids) <= card_ids and SECTION_SIGN in body and SECTION_RESPONSE in body
        rows.append(PremortemRow(heading=str(s.get("heading", "")), card_ids=ids, ok=ok))
    return rows


def count_surveillance_keys(value: Any) -> int:
    if isinstance(value, dict):
        return sum(
            (1 if str(k).lower() in SURVEILLANCE_KEYS else 0) + count_surveillance_keys(v)
            for k, v in value.items()
        )
    if isinstance(value, list):
        return sum(count_surveillance_keys(v) for v in value)
    return 0


def count_pii(payloads: dict[str, Any]) -> dict[str, int]:
    return {
        source: len(find_pii(json.dumps(data, ensure_ascii=False)))
        for source, data in payloads.items()
    }


def unmet_targets(m: HonestyMetrics) -> list[str]:
    unmet: list[str] = []
    for name, (kind, target) in HONESTY_TARGETS.items():
        value = float(getattr(m, name))
        if (kind == "min" and value < target) or (kind == "max" and value > target):
            unmet.append(name)
    return unmet


# ---- steps --------------------------------------------------------------------


def _session_text(fixture: HonestyFixture, question: dict[str, Any]) -> str:
    kind = question.get("kind")
    qid = str(question.get("id", ""))
    if kind == "main":
        node = fixture.answers.get(qid)
        if node is None:
            raise HonestyError("fixtures", 0)
        return node.main
    if kind == "queued":
        text = str(question.get("text", ""))
        if text in PREMORTEM_QUESTIONS:
            return fixture.premortem_answers[PREMORTEM_QUESTIONS.index(text)]
        return NO_MORE_ANSWER
    node = fixture.answers.get(topic_id(qid))
    return node.follow_up if node is not None else NO_MORE_ANSWER


def run_session(
    client: httpx.Client, fixture: HonestyFixture, leaver: str, number: int, nodes: int
) -> tuple[SessionRow, list[Any]]:
    start = _call(
        client, "session_start", "POST", "/sessions", json={"role": fixture.role, "leaver": leaver}
    )
    session_id = str(start["session_id"])
    payloads: list[Any] = [start]
    question: dict[str, Any] | None = start.get("question")
    answered: list[str] = []
    count = 0
    while question is not None and count < MAX_SESSION_ANSWERS:
        if question.get("kind") == "main":
            if len(answered) >= nodes:
                break
            answered.append(str(question.get("id", "")))
        text = _session_text(fixture, question)
        body = _call(
            client, "session_answer", "POST", f"/sessions/{session_id}/answers", json={"text": text}
        )
        payloads.append(body)
        count += 1
        question = body.get("next_question")
    payloads.append(_call(client, "session_end", "POST", f"/sessions/{session_id}/end"))
    return SessionRow(number=number, session_id=session_id, nodes=answered, answers=count), payloads


def run_honesty_eval(
    client: httpx.Client, fixture: HonestyFixture, leaver: str = "honesty"
) -> HonestyResult:
    role = fixture.role
    payloads: dict[str, list[Any]] = {
        "tacit": [], "premortem": [], "sessions": [], "cards": [], "gaps": [],
        "at_risk": [], "fence": [], "routing": [], "artifact": [],
    }
    # (1) tacit follow-ups
    tacit_rows: list[TacitRow] = []
    for case in fixture.tacit_cases:
        resp = _call(
            client, "depth_tacit", "POST", "/depth/tacit",
            json={"question": case.question, "answer": case.answer},
        )
        payloads["tacit"].append(resp)
        tacit_rows.append(judge_tacit(case, resp))
    # (2) pre-mortem questions
    payloads["premortem"].append(
        _call(
            client, "premortem_questions", "POST", f"/leavers/{leaver}/premortem/questions",
            params={"role": role},
        )
    )
    # (3) sessions
    session_rows: list[SessionRow] = []
    for number, plan in enumerate(fixture.sessions, start=1):
        row, bodies = run_session(client, fixture, leaver, number, plan.nodes)
        session_rows.append(row)
        payloads["sessions"] += bodies
    # (4) cards
    cards: list[dict[str, Any]] = list(_call(client, "cards", "GET", f"/leavers/{leaver}/cards"))
    payloads["cards"].append(cards)
    card_ids = {str(c.get("id", "")) for c in cards}
    # (5) gaps
    gap_report = _call(
        client, "gaps_refresh", "POST", f"/leavers/{leaver}/gaps/refresh", params={"role": role}
    )
    payloads["gaps"].append(gap_report)
    uncertain_rows = [judge_uncertain(c, cards, gap_report) for c in fixture.uncertain_cases]
    # (6) at-risk
    at_risk_rows: list[AtRiskRow] = []
    for acase in fixture.at_risk_cases:
        report = _call(
            client, "cards_at_risk", "GET", f"/leavers/{leaver}/cards/at-risk",
            params={"event": acase.event},
        )
        payloads["at_risk"].append(report)
        at_risk_rows.append(judge_at_risk(acase, report))
    # (7) fence
    fence_rows: list[FenceRow] = []
    for fcase in fixture.fence_cases:
        resp = _call(
            client, "kb_fence", "POST", "/kb/fence",
            json={"leaver": leaver, "subject": fcase.subject, "role": role},
        )
        payloads["fence"].append(resp)
        fence_rows.append(judge_fence(fcase, resp, card_ids))
    # (8) routing
    routing_rows: list[RoutingRow] = []
    for rcase in fixture.routing_cases:
        resp = _call(
            client, "kb_ask", "POST", "/kb/ask", json={"question": rcase.question, "leaver": leaver}
        )
        payloads["routing"].append(resp)
        routing_rows.append(judge_routing(rcase, resp))
    # (9) pre-mortem artifact
    artifact: dict[str, Any] | None
    try:
        artifact = _call(
            client, "artifact_premortem", "POST", f"/leavers/{leaver}/artifacts/premortem"
        )
    except HonestyError as exc:
        if exc.status != 409 or exc.detail != NO_CARDS:
            raise
        artifact = None
    payloads["artifact"].append(artifact)
    premortem_rows = judge_premortem(artifact, card_ids)
    # (10) privacy and surveillance
    pii = count_pii(dict(payloads))
    surveillance = {source: count_surveillance_keys(data) for source, data in payloads.items()}

    at_risk_total = sum(r.total for r in at_risk_rows)
    metrics = HonestyMetrics(
        fence_accuracy=_rate(sum(r.ok for r in fence_rows), len(fence_rows)),
        fence_fabricated=sum(r.fabricated for r in fence_rows),
        uncertain_detect=_rate(sum(r.ok for r in uncertain_rows), len(uncertain_rows)),
        tacit_followup=_rate(sum(r.ok for r in tacit_rows), len(tacit_rows)),
        routing=_rate(sum(r.ok for r in routing_rows), len(routing_rows)),
        at_risk=_rate(sum(r.hits for r in at_risk_rows), at_risk_total),
        premortem_cited=_rate(sum(r.ok for r in premortem_rows), len(premortem_rows)),
        pii_leaks=sum(pii.values()),
        surveillance_keys=sum(surveillance.values()),
    )
    return HonestyResult(
        name=leaver,
        leaver=leaver,
        generated_at=datetime.now(UTC),
        metrics=metrics,
        unmet=unmet_targets(metrics),
        sessions=session_rows,
        tacit=tacit_rows,
        uncertain=uncertain_rows,
        at_risk=at_risk_rows,
        fence=fence_rows,
        routing=routing_rows,
        premortem=premortem_rows,
        pii_by_source=pii,
        surveillance_by_source=surveillance,
    )


# ---- reports ------------------------------------------------------------------


def summary_line(result: HonestyResult) -> str:
    m = result.metrics
    return (
        f"HONESTY fence_accuracy={m.fence_accuracy:.3f} fence_fabricated={m.fence_fabricated} "
        f"uncertain_detect={m.uncertain_detect:.3f} tacit_followup={m.tacit_followup:.3f} "
        f"routing={m.routing:.3f} at_risk={m.at_risk:.3f} premortem_cited={m.premortem_cited:.3f} "
        f"pii_leaks={m.pii_leaks} surveillance_keys={m.surveillance_keys}"
    )


def _mark(value: bool) -> str:
    return "○" if value else "×"


def _cell(text: str | None) -> str:
    return " ".join((text or "-").split()).replace("|", "\\|")


def _target_text(name: str) -> str:
    kind, target = HONESTY_TARGETS[name]
    return f"≥ {target:g}" if kind == "min" else f"≤ {target:g}"


def _value_text(value: float) -> str:
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def render_markdown(result: HonestyResult) -> str:
    m = result.metrics
    lines = [
        f"# 지식 정직성 평가 — {result.name}",
        "",
        f"- 퇴사자: {result.leaver} · 생성: {result.generated_at.isoformat()}",
        f"- 목표 미달: {', '.join(result.unmet) if result.unmet else '없음'}",
        "",
        "## 지표",
        "",
        "| 지표 | 값 | 목표 | 달성 |",
        "|------|----|------|------|",
    ]
    for name in HONESTY_TARGETS:
        lines.append(
            f"| {name} | {_value_text(getattr(m, name))} | {_target_text(name)} "
            f"| {_mark(name not in result.unmet)} |"
        )
    lines += ["", "## 세션", "", "| 회차 | 주 질문 | 답 수 |", "|------|--------|------|"]
    lines += [f"| {s.number} | {', '.join(s.nodes)} | {s.answers} |" for s in result.sessions]
    lines += ["", "## 암묵지 꼬리 질문", "", "| 질문 | 기대 | 신호어 | 꼬리 질문 | 맞음 |",
              "|------|------|--------|----------|------|"]
    lines += [
        f"| {_cell(r.question)} | {_mark(r.tacit)} | {_cell(', '.join(r.cues))} "
        f"| {_cell(r.text)} | {_mark(r.ok)} |"
        for r in result.tacit
    ]
    lines += ["", "## 확신도", "", "| 구절 | 기대 불확실 | 카드 | 확신도 | 무지의 지도 | 맞음 |",
              "|------|------------|------|--------|------------|------|"]
    lines += [
        f"| {_cell(r.term)} | {_mark(r.uncertain)} | {r.card_id or '-'} | {r.certainty or '-'} "
        f"| {_mark(r.in_unknown)} | {_mark(r.ok)} |"
        for r in result.uncertain
    ]
    lines += ["", "## 유통기한 (at-risk)", "", "| 이벤트 | 카드 | 찾은 구절 | 섞인 구절 | 점수 |",
              "|--------|------|----------|----------|------|"]
    lines += [
        f"| {r.event} | {_cell(', '.join(r.card_ids))} | {_cell(', '.join(r.found_terms))} "
        f"| {_cell(', '.join(r.leaked_terms))} | {r.hits}/{r.total} |"
        for r in result.at_risk
    ]
    lines += ["", "## 울타리", "", "| 주제 | 기대 | 찾음 | 점 | 큐 적재 | 지어냄 | 맞음 |",
              "|------|------|------|----|--------|--------|------|"]
    lines += [
        f"| {_cell(r.subject)} | {_mark(r.expect_found)} | {_mark(r.found)} | {r.points} "
        f"| {_mark(r.queued)} | {_mark(r.fabricated)} | {_mark(r.ok)} |"
        for r in result.fence
    ]
    lines += ["", "## 질문 라우팅", "", "| 질문 | 답 있음 | 물어볼 역할 | 맞음 |",
              "|------|--------|------------|------|"]
    lines += [
        f"| {_cell(r.question)} | {_mark(r.found)} | {_cell(r.ask_role)} | {_mark(r.ok)} |"
        for r in result.routing
    ]
    lines += ["", "## 사전 부검", "", "| 절 | 카드 | 유효 |", "|----|------|------|"]
    lines += [
        f"| {_cell(r.heading)} | {_cell(', '.join(r.card_ids))} | {_mark(r.ok)} |"
        for r in result.premortem
    ]
    lines += ["", "## 개인정보·감시 금지", "", "| 응답 | 개인정보 | 금지 키 |",
              "|------|---------|--------|"]
    lines += [
        f"| {source} | {count} | {result.surveillance_by_source.get(source, 0)} |"
        for source, count in result.pii_by_source.items()
    ]
    return "\n".join(lines) + "\n"


def write_reports(result: HonestyResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = json.loads(result.model_dump_json())
    (out_dir / f"{result.name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / f"{result.name}.md").write_text(render_markdown(result), encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m handoff.evaluation_honesty")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--leaver", default="honesty")
    parser.add_argument("--fixtures-dir", default=Path("eval/fixtures"), type=Path)
    parser.add_argument("--require-targets", action="store_true")
    return parser


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        try:
            fixture = load_fixture(args.fixtures_dir / "honesty_eval.yaml")
        except (OSError, TypeError, yaml.YAMLError, ValidationError):
            raise HonestyError("fixtures", 0) from None
        with httpx.Client(base_url=args.base_url, timeout=120.0, transport=transport) as client:
            result = run_honesty_eval(client, fixture, leaver=args.leaver)
        result = result.model_copy(update={"name": args.name})
    except HonestyError as exc:
        print(str(exc))
        return 2
    write_reports(result, args.out_dir)
    print(f"report: {args.out_dir / (args.name + '.md')}")
    if result.unmet:
        print(f"unmet: {', '.join(result.unmet)}")
    print(summary_line(result))
    if args.require_targets and result.unmet:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
