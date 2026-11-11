"""Quality evaluation runner (``python -m handoff.evaluation``).

Drives a mock leaver through the planned sessions over HTTP, asks the fixed
successor questions, grades every answer by rule and writes md/json reports.
"""

import argparse
import json
import math
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml
from pydantic import BaseModel, ValidationError

from handoff.coverage import CoverageReport

Grade = Literal["좋음", "보통", "틀림"]

GOOD: Grade = "좋음"
FAIR: Grade = "보통"
WRONG: Grade = "틀림"
NOTE_WRONG_SOURCE = "근거 다름"
NOTE_MISSED = "놓침"
NOTE_NOT_FABRICATED = "지어내지 않음"
NOTE_FABRICATED = "지어낸 답"
NOTE_MISSING_KEYWORDS = "키워드 누락"


class EvalError(Exception):
    """HTTP or fixture failure; reported as ``EVAL ERROR <step> status=<code>``."""

    def __init__(self, step: str, status: int) -> None:
        super().__init__(f"EVAL ERROR {step} status={status}")
        self.step = step
        self.status = status


class MockSession(BaseModel):
    nodes: int


class MockAnswer(BaseModel):
    main: str
    follow_up: str


class MockLeaver(BaseModel):
    role: str
    sessions: list[MockSession]
    answers: dict[str, MockAnswer]


class SuccessorQuestion(BaseModel):
    id: str
    text: str
    expect_nodes: list[str]
    keywords: list[str]


class SessionRecord(BaseModel):
    number: int
    session_id: str
    nodes: list[str]
    answers: int
    elapsed_seconds: float
    summary: list[str]


class RowCitation(BaseModel):
    session_number: int | None
    question_id: str
    score: float


class EvalRow(BaseModel):
    id: str
    question: str
    expect_nodes: list[str]
    found: bool
    cited_ok: bool | None
    grade: Grade
    note: str
    answer: str
    citations: list[RowCitation]


class EvalCounts(BaseModel):
    total: int
    covered: int
    answered: int
    cited_ok: int
    good: int
    fair: int
    wrong: int
    fabricated: int
    max_session_seconds: int


class EvalResult(BaseModel):
    name: str
    leaver: str
    generated_at: datetime
    sessions: list[SessionRecord]
    rows: list[EvalRow]
    counts: EvalCounts
    coverage: CoverageReport


def load_mock(path: Path) -> MockLeaver:
    return MockLeaver.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_questions(path: Path) -> list[SuccessorQuestion]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [SuccessorQuestion.model_validate(q) for q in data["questions"]]


def _call(
    client: httpx.Client, step: str, method: str, url: str, **kwargs: Any
) -> dict[str, Any]:
    try:
        resp = client.request(method, url, **kwargs)
    except httpx.HTTPError:
        raise EvalError(step, 0) from None
    if resp.status_code >= 400:
        raise EvalError(step, resp.status_code)
    body: dict[str, Any] = resp.json()
    return body


def _node_of(mock: MockLeaver, question_id: str) -> MockAnswer:
    answer = mock.answers.get(question_id)
    if answer is None:
        raise EvalError("fixtures", 0)
    return answer


def _run_session(
    client: httpx.Client, mock: MockLeaver, leaver: str, number: int, nodes: int
) -> SessionRecord:
    started = time.monotonic()
    start = _call(client, "start", "POST", "/sessions", json={"role": mock.role, "leaver": leaver})
    if start.get("number") != number:
        raise EvalError("start", 201)
    session_id: str = start["session_id"]
    question: dict[str, Any] | None = start["question"]
    answered: list[str] = []
    count = 0
    while question is not None:
        if question["kind"] == "main":
            if len(answered) >= nodes:
                break
            text = _node_of(mock, question["id"]).main
            answered.append(question["id"])
        else:
            if not answered:
                raise EvalError("answer", 0)
            text = _node_of(mock, answered[-1]).follow_up
        body = _call(
            client, "answer", "POST", f"/sessions/{session_id}/answers", json={"text": text}
        )
        count += 1
        question = body["next_question"]
    end = _call(client, "end", "POST", f"/sessions/{session_id}/end")
    elapsed = time.monotonic() - started
    return SessionRecord(
        number=number,
        session_id=session_id,
        nodes=answered,
        answers=count,
        elapsed_seconds=round(elapsed, 3),
        summary=list(end.get("summary", [])),
    )


def _matches(question_id: str, node_id: str) -> bool:
    return question_id == node_id or question_id.startswith(node_id + "-")


def judge(q: SuccessorQuestion, answer: dict[str, Any], answered_in: dict[str, str]) -> EvalRow:
    found = bool(answer.get("found"))
    text = str(answer.get("answer", ""))
    citations: list[dict[str, Any]] = list(answer.get("citations") or [])
    cited_ok: bool | None = None
    if q.expect_nodes:
        if not found:
            grade, note = WRONG, NOTE_MISSED
        else:
            cited_ok = any(
                _matches(str(c.get("question_id", "")), node)
                and node in answered_in
                and c.get("session_id") == answered_in[node]
                for c in citations
                for node in q.expect_nodes
            )
            if not cited_ok:
                grade, note = WRONG, NOTE_WRONG_SOURCE
            else:
                lowered = text.lower()
                missing = [k for k in q.keywords if k.strip().lower() not in lowered]
                grade, note = (FAIR, NOTE_MISSING_KEYWORDS) if missing else (GOOD, "")
    elif found:
        grade, note = WRONG, NOTE_FABRICATED
    else:
        grade, note = GOOD, NOTE_NOT_FABRICATED
    return EvalRow(
        id=q.id,
        question=q.text,
        expect_nodes=list(q.expect_nodes),
        found=found,
        cited_ok=cited_ok,
        grade=grade,
        note=note,
        answer=text,
        citations=[
            RowCitation(
                session_number=c.get("session_number"),
                question_id=str(c.get("question_id", "")),
                score=float(c.get("score", 0.0)),
            )
            for c in citations
        ],
    )


def _count(rows: list[EvalRow], sessions: list[SessionRecord]) -> EvalCounts:
    return EvalCounts(
        total=len(rows),
        covered=sum(1 for r in rows if r.expect_nodes),
        answered=sum(1 for r in rows if r.found),
        cited_ok=sum(1 for r in rows if r.cited_ok is True),
        good=sum(1 for r in rows if r.grade == GOOD),
        fair=sum(1 for r in rows if r.grade == FAIR),
        wrong=sum(1 for r in rows if r.grade == WRONG),
        fabricated=sum(1 for r in rows if r.note == NOTE_FABRICATED),
        max_session_seconds=math.ceil(max((s.elapsed_seconds for s in sessions), default=0.0)),
    )


def run_eval(
    client: httpx.Client,
    mock: MockLeaver,
    questions: list[SuccessorQuestion],
    leaver: str = "eval",
) -> EvalResult:
    sessions: list[SessionRecord] = []
    answered_in: dict[str, str] = {}
    for number, plan in enumerate(mock.sessions, start=1):
        record = _run_session(client, mock, leaver, number, plan.nodes)
        sessions.append(record)
        for node in record.nodes:
            answered_in[node] = record.session_id
    coverage = CoverageReport.model_validate(
        _call(client, "coverage", "GET", "/coverage", params={"leaver": leaver, "role": mock.role})
    )
    rows = [
        judge(q, _call(client, "query", "POST", "/kb/query", json={"question": q.text}), answered_in)
        for q in questions
    ]
    return EvalResult(
        name=leaver,
        leaver=leaver,
        generated_at=datetime.now(UTC),
        sessions=sessions,
        rows=rows,
        counts=_count(rows, sessions),
        coverage=coverage,
    )


def _mark(value: bool | None) -> str:
    if value is None:
        return "-"
    return "○" if value else "×"


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def summary_line(result: EvalResult) -> str:
    c = result.counts
    return (
        f"EVAL answered={c.answered}/{c.total} cited_ok={c.cited_ok}/{c.covered} "
        f"fabricated={c.fabricated} good={c.good} fair={c.fair} wrong={c.wrong} "
        f"max_session_seconds={c.max_session_seconds}"
    )


def render_markdown(result: EvalResult) -> str:
    c = result.counts
    lines = [
        f"# 품질 평가 — {result.name}",
        "",
        f"- 퇴사자: {result.leaver} · 생성: {result.generated_at.isoformat()}",
        (
            f"- 집계: 답변 있음 {c.answered}/{c.total} · 근거 맞음 {c.cited_ok}/{c.covered} · "
            f"좋음 {c.good} · 보통 {c.fair} · 틀림 {c.wrong} · 지어낸 답 {c.fabricated} · "
            f"최장 세션 {c.max_session_seconds}초"
        ),
        "",
        "## 질문",
        "",
        "| id | 질문 | 답변 있음? | 근거 세션 맞음? | 평가 | 비고 |",
        "|----|------|-----------|----------------|------|------|",
    ]
    for r in result.rows:
        lines.append(
            f"| {r.id} | {_cell(r.question)} | {_mark(r.found)} | {_mark(r.cited_ok)} "
            f"| {r.grade} | {_cell(r.note)} |"
        )
    lines += [
        "",
        "## 세션",
        "",
        "| 회차 | 노드 | 답변 수 | 경과 초 |",
        "|------|------|--------|--------|",
    ]
    for s in result.sessions:
        lines.append(
            f"| {s.number} | {', '.join(s.nodes)} | {s.answers} | {s.elapsed_seconds:.1f} |"
        )
    lines += ["", "## 커버리지", "", "| 축 | 채움 |", "|----|------|"]
    for axis in result.coverage.axes:
        lines.append(f"| {axis.label} | {axis.covered}/{axis.total} |")
    lines += ["", "### 빈 주제", ""]
    if result.coverage.empty_topics:
        lines += [
            f"- {t.question_id} ({t.label}): {_cell(t.text)}" for t in result.coverage.empty_topics
        ]
    else:
        lines.append("- 없음")
    return "\n".join(lines) + "\n"


def write_reports(result: EvalResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = json.loads(result.model_dump_json())
    (out_dir / f"{result.name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / f"{result.name}.md").write_text(render_markdown(result), encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m handoff.evaluation")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--leaver", default="eval")
    parser.add_argument("--fixtures-dir", default=Path("eval/fixtures"), type=Path)
    parser.add_argument("--require-zero-fabricated", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        try:
            mock = load_mock(args.fixtures_dir / "mock_leaver.yaml")
            questions = load_questions(args.fixtures_dir / "successor_questions.yaml")
        except (OSError, KeyError, TypeError, yaml.YAMLError, ValidationError):
            raise EvalError("fixtures", 0) from None
        with httpx.Client(base_url=args.base_url, timeout=120.0) as client:
            result = run_eval(client, mock, questions, leaver=args.leaver)
        result = result.model_copy(update={"name": args.name})
    except EvalError as exc:
        print(str(exc))
        return 2
    write_reports(result, args.out_dir)
    print(f"report: {args.out_dir / (args.name + '.md')}")
    print(summary_line(result))
    if args.require_zero_fabricated and result.counts.fabricated > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
