from datetime import UTC, datetime, timedelta

from handoff.coverage import build_coverage
from handoff.kb import KBEntry
from handoff.question_tree import AXIS_LABELS, load_tree
from handoff.store import SessionRow

_T0 = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)


def _session(number: int, ended: bool) -> SessionRow:
    return SessionRow(
        session_id=f"s{number}",
        leaver="alice",
        role="backend_developer",
        number=number,
        started_at=_T0,
        deadline=_T0 + timedelta(minutes=30),
        ended_at=_T0 + timedelta(minutes=10) if ended else None,
        node_index=0,
        current_json=None,
        summary=[],
    )


def _answer(question_id: str, axis: str = "systems") -> KBEntry:
    return KBEntry(
        session_id="s1",
        question_id=question_id,
        axis=axis,  # type: ignore[arg-type]
        question="q",
        answer="a",
        created_at=_T0,
        session_number=1,
    )


def test_no_answers_all_empty() -> None:
    tree = load_tree("backend_developer")
    report = build_coverage(tree, "alice", [], [], 3)
    assert report.leaver == "alice"
    assert report.role == "backend_developer"
    assert report.sessions_done == 0
    assert report.sessions_planned == 3
    assert report.items_total == 0
    assert [(a.covered, a.total) for a in report.axes] == [(0, 10)] * 4
    assert len(report.empty_topics) == 40
    assert [t.question_id for t in report.empty_topics[:2]] == ["sys-01", "rou-01"]
    first = report.empty_topics[0]
    assert first.axis == "systems"
    assert first.label == AXIS_LABELS["systems"]
    assert first.text == tree.nodes[0].text


def test_follow_up_answer_covers_node() -> None:
    tree = load_tree("backend_developer")
    report = build_coverage(tree, "alice", [], [_answer("sys-01-f1")], 3)
    assert report.axes[0].covered == 1
    assert report.items_total == 1
    assert "sys-01" not in [t.question_id for t in report.empty_topics]
    assert len(report.empty_topics) == 39


def test_generated_follow_up_answer_covers_node() -> None:
    tree = load_tree("backend_developer")
    report = build_coverage(tree, "alice", [], [_answer("sys-01-g1")], 3)
    assert report.axes[0].covered == 1
    assert "sys-01" not in [t.question_id for t in report.empty_topics]


def test_main_answer_covers_and_prefix_is_exact() -> None:
    tree = load_tree("backend_developer")
    answers = [_answer("rou-02", "routines"), _answer("rou-0", "routines")]
    report = build_coverage(tree, "alice", [], answers, 3)
    routines = report.axes[1]
    assert routines.covered == 1
    assert report.items_total == 2
    ids = [t.question_id for t in report.empty_topics]
    assert "rou-02" not in ids
    assert "rou-01" in ids


def test_sessions_done_counts_ended_only() -> None:
    tree = load_tree("backend_developer")
    sessions = [_session(1, True), _session(2, True), _session(3, False)]
    report = build_coverage(tree, "alice", sessions, [], 3)
    assert report.sessions_done == 2
    assert report.sessions_planned == 3


def test_axes_order_and_labels() -> None:
    tree = load_tree("backend_developer")
    report = build_coverage(tree, "alice", [], [], 3)
    assert [a.axis for a in report.axes] == ["systems", "routines", "incidents", "people"]
    assert [a.label for a in report.axes] == [
        AXIS_LABELS[a] for a in ("systems", "routines", "incidents", "people")
    ]


def test_empty_topics_in_tree_order() -> None:
    tree = load_tree("backend_developer")
    report = build_coverage(tree, "alice", [], [_answer("inc-01", "incidents")], 3)
    expected = [n.id for n in tree.nodes if n.id != "inc-01"]
    assert [t.question_id for t in report.empty_topics] == expected


def test_queue_answers_count_as_queued_answered_not_covered() -> None:
    tree = load_tree("backend_developer")
    answers = [_answer("q3", "people"), _answer("q7", "systems")]
    report = build_coverage(tree, "alice", [], answers, 3)
    assert [a.covered for a in report.axes] == [0, 0, 0, 0]
    assert [a.queued_answered for a in report.axes] == [1, 0, 0, 1]
    assert len(report.empty_topics) == 40
    assert report.items_total == 2


def test_tree_answers_do_not_count_as_queued_answered() -> None:
    tree = load_tree("backend_developer")
    answers = [_answer("sys-01"), _answer("sys-01-f1"), _answer("rou-02-g1", "routines")]
    report = build_coverage(tree, "alice", [], answers, 3)
    assert [a.queued_answered for a in report.axes] == [0, 0, 0, 0]
    assert report.axes[0].covered == 1


def test_queue_follow_ups_of_same_topic_count_once() -> None:
    tree = load_tree("backend_developer")
    answers = [_answer("q12", "routines"), _answer("q12-f1", "routines"),
               _answer("q12-g2", "routines"), _answer("q13", "routines")]
    report = build_coverage(tree, "alice", [], answers, 3)
    assert report.axes[1].queued_answered == 2
    assert report.axes[1].covered == 0


def test_queued_answered_defaults_to_zero() -> None:
    from handoff.coverage import AxisCoverage

    assert AxisCoverage(axis="systems", label="x", covered=0, total=1).queued_answered == 0
