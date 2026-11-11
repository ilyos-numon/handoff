import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from handoff import evaluation
from handoff.config import Settings
from handoff.coverage import AxisCoverage, CoverageReport
from handoff.evaluation import (
    EvalCounts,
    EvalError,
    EvalResult,
    EvalRow,
    MockLeaver,
    SuccessorQuestion,
    judge,
    load_mock,
    load_questions,
    main,
    render_markdown,
    run_eval,
)
from handoff.main import create_app
from handoff.question_tree import load_tree

FIXTURES = Path(__file__).resolve().parents[2] / "eval" / "fixtures"
ROLE = "backend_developer"


@pytest.fixture(scope="module")
def mock() -> MockLeaver:
    return load_mock(FIXTURES / "mock_leaver.yaml")


@pytest.fixture(scope="module")
def questions() -> list[SuccessorQuestion]:
    return load_questions(FIXTURES / "successor_questions.yaml")


@pytest.fixture(scope="module")
def first30() -> list[str]:
    return [n.id for n in load_tree(ROLE).nodes[:30]]


# --- fixture structure -----------------------------------------------------


def test_mock_leaver_structure(mock: MockLeaver, first30: list[str]) -> None:
    assert mock.role == ROLE
    assert len(mock.sessions) == 10
    assert sum(s.nodes for s in mock.sessions) == 30
    assert list(mock.answers) == first30
    for node_id, answer in mock.answers.items():
        assert 0 < len(answer.main) <= 200, node_id
        assert 0 < len(answer.follow_up) <= 200, node_id
    rou02 = mock.answers["rou-02"].main
    assert "금요일" in rou02 and "정산 배치" in rou02


def test_successor_questions_structure(
    mock: MockLeaver, questions: list[SuccessorQuestion], first30: list[str]
) -> None:
    assert [q.id for q in questions] == [f"q{i:02d}" for i in range(1, 21)]
    covered = [q for q in questions if q.expect_nodes]
    empty = [q for q in questions if not q.expect_nodes]
    assert len(covered) == 14
    assert len(empty) == 6
    tree = load_tree(ROLE)
    axis_of = {n.id: n.axis for n in tree.nodes}
    per_axis: dict[str, int] = {}
    for q in covered:
        assert all(n in first30 for n in q.expect_nodes), q.id
        assert 1 <= len(q.keywords) <= 2, q.id
        source = " ".join(
            mock.answers[n].main + " " + mock.answers[n].follow_up for n in q.expect_nodes
        ).lower()
        for k in q.keywords:
            assert k.strip().lower() in source, (q.id, k)
        per_axis[axis_of[q.expect_nodes[0]]] = per_axis.get(axis_of[q.expect_nodes[0]], 0) + 1
    assert set(per_axis) == {"systems", "routines", "incidents", "people"}
    assert all(v >= 3 for v in per_axis.values())
    for q in empty:
        assert q.keywords == []
    tree_texts = {n.text for n in tree.nodes}
    assert all(q.text not in tree_texts for q in questions)


# --- judge rules -----------------------------------------------------------

ANSWERED_IN = {"rou-02": "s2"}
Q_COVERED = SuccessorQuestion(
    id="q01", text="배포를 왜 금요일에는 안 하나요?", expect_nodes=["rou-02"],
    keywords=["금요일", "정산"],
)
Q_EMPTY = SuccessorQuestion(id="q20", text="휴가 신청은?", expect_nodes=[], keywords=[])


def _answer(found: bool, text: str, cites: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "found": found,
        "answer": text,
        "citations": [
            {"session_id": sid, "question_id": qid, "axis": "routines", "excerpt": text,
             "score": 0.8, "session_number": 2}
            for sid, qid in cites
        ],
    }


@pytest.mark.parametrize(
    ("q", "answer", "grade", "note", "cited_ok"),
    [
        (Q_COVERED, _answer(True, " 금요일 배포 후 정산 배치 실패", [("s2", "rou-02")]),
         "좋음", "", True),
        (Q_COVERED, _answer(True, "금요일 배포는 피합니다", [("s2", "rou-02-f1")]),
         "보통", "키워드 누락", True),
        (Q_COVERED, _answer(True, "금요일 정산", [("s9", "rou-02"), ("s2", "sys-01")]),
         "틀림", "근거 다름", False),
        (Q_COVERED, _answer(False, "이 주제는 인수인계에 없습니다.", []),
         "틀림", "놓침", None),
        (Q_EMPTY, _answer(False, "이 주제는 인수인계에 없습니다.", []),
         "좋음", "지어내지 않음", None),
        (Q_EMPTY, _answer(True, "휴가는 게시판에서", [("s2", "rou-02")]),
         "틀림", "지어낸 답", None),
    ],
)
def test_judge_rules(
    q: SuccessorQuestion, answer: dict[str, Any], grade: str, note: str,
    cited_ok: bool | None,
) -> None:
    row = judge(q, answer, ANSWERED_IN)
    assert row.grade == grade
    assert row.note == note
    assert row.cited_ok is cited_ok
    assert row.found is answer["found"]
    assert len(row.citations) == len(answer["citations"])


def test_judge_keyword_case_insensitive() -> None:
    q = SuccessorQuestion(id="q04", text="DB?", expect_nodes=["sys-04"], keywords=[" Index "])
    row = judge(q, _answer(True, "INDEX 를 타게", [("s5", "sys-04")]), {"sys-04": "s5"})
    assert row.grade == "좋음"


# --- full run with fake provider -------------------------------------------


@pytest.fixture(scope="module")
def result(
    tmp_path_factory: pytest.TempPathFactory, mock: MockLeaver,
    questions: list[SuccessorQuestion],
) -> Iterator[EvalResult]:
    data_dir = tmp_path_factory.mktemp("eval")
    settings = Settings(provider="fake", data_dir=data_dir, min_score=0.25, collection="handoff_kb")
    with TestClient(create_app(settings)) as client:
        yield run_eval(client, mock, questions)


def test_run_eval_full(result: EvalResult, first30: list[str]) -> None:
    assert len(result.sessions) == 10
    assert [s.number for s in result.sessions] == list(range(1, 11))
    assert [n for s in result.sessions for n in s.nodes] == first30
    assert all(s.answers == 6 for s in result.sessions)
    assert all(len(s.summary) == 3 for s in result.sessions)
    assert len(result.rows) == 20
    c = result.counts
    assert c.total == 20 and c.covered == 14
    assert c.good + c.fair + c.wrong == 20
    assert c.cited_ok <= c.answered
    assert result.coverage.sessions_done == 10
    assert sum(a.covered for a in result.coverage.axes) == 30
    assert result.leaver == "eval"


def test_run_eval_rejects_wrong_session_number(
    tmp_path: Path, mock: MockLeaver, questions: list[SuccessorQuestion]
) -> None:
    settings = Settings(provider="fake", data_dir=tmp_path, min_score=0.25, collection="handoff_kb")
    with TestClient(create_app(settings)) as client:
        client.post("/sessions", json={"role": ROLE, "leaver": "eval"})
        with pytest.raises(EvalError) as exc:
            run_eval(client, mock, questions)
    assert exc.value.step == "start"


def test_render_markdown(result: EvalResult) -> None:
    md = render_markdown(result)
    assert "| id | 질문 | 답변 있음? | 근거 세션 맞음? | 평가 | 비고 |" in md
    assert sum(1 for line in md.splitlines() if line.startswith("| q")) == 20
    assert "| q01 |" in md


# --- main ------------------------------------------------------------------


def _fake_result(fabricated: int) -> EvalResult:
    row = EvalRow(
        id="q20", question="휴가 신청은?", expect_nodes=[], found=fabricated > 0,
        cited_ok=None, grade="틀림" if fabricated else "좋음",
        note="지어낸 답" if fabricated else "지어내지 않음", answer="...", citations=[],
    )
    return EvalResult(
        name="x", leaver="eval", generated_at=datetime.now(UTC), sessions=[], rows=[row],
        counts=EvalCounts(
            total=1, covered=0, answered=row.found and 1 or 0, cited_ok=0,
            good=0 if fabricated else 1, fair=0, wrong=1 if fabricated else 0,
            fabricated=fabricated, max_session_seconds=3,
        ),
        coverage=CoverageReport(
            leaver="eval", role=ROLE, sessions_done=0, sessions_planned=10, items_total=0,
            axes=[AxisCoverage(axis="systems", label="담당 시스템", covered=0, total=10)],
            empty_topics=[],
        ),
    )


def _argv(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--base-url", "http://testserver", "--out-dir", str(tmp_path), "--name", "run1",
        "--fixtures-dir", str(FIXTURES), *extra,
    ]


def test_main_writes_reports_and_returns_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(evaluation, "run_eval", lambda *a, **k: _fake_result(0))
    assert main(_argv(tmp_path)) == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last == (
        "EVAL answered=0/1 cited_ok=0/0 fabricated=0 good=1 fair=0 wrong=0 "
        "max_session_seconds=3"
    )
    data = json.loads((tmp_path / "run1.json").read_text(encoding="utf-8"))
    assert data["name"] == "run1"
    assert set(data) >= {"name", "leaver", "generated_at", "sessions", "rows", "counts", "coverage"}
    assert (tmp_path / "run1.md").read_text(encoding="utf-8").startswith("# ")


def test_main_require_zero_fabricated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(evaluation, "run_eval", lambda *a, **k: _fake_result(1))
    assert main(_argv(tmp_path)) == 0
    assert main(_argv(tmp_path, "--require-zero-fabricated")) == 1


def test_main_http_error_returns_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*_: object, **__: object) -> EvalResult:
        raise EvalError("start", 503)

    monkeypatch.setattr(evaluation, "run_eval", boom)
    assert main(_argv(tmp_path)) == 2
    assert capsys.readouterr().out.strip().splitlines()[-1] == "EVAL ERROR start status=503"


def test_main_fixture_error_returns_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = _argv(tmp_path)
    argv[argv.index("--fixtures-dir") + 1] = str(tmp_path / "missing")
    assert main(argv) == 2
    assert capsys.readouterr().out.strip().startswith("EVAL ERROR fixtures status=")
