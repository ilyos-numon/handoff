"""Coverage report: covered topics per axis and the list of empty topics."""

from pydantic import BaseModel

from handoff.kb import KBEntry
from handoff.question_tree import AXIS_LABELS, Axis, QuestionTree
from handoff.store import SessionRow

_AXIS_ORDER: tuple[Axis, ...] = ("systems", "routines", "incidents", "people")


class AxisCoverage(BaseModel):
    axis: Axis
    label: str
    covered: int
    total: int


class EmptyTopic(BaseModel):
    question_id: str
    axis: Axis
    label: str
    text: str


class CoverageReport(BaseModel):
    leaver: str
    role: str
    sessions_done: int
    sessions_planned: int
    items_total: int
    axes: list[AxisCoverage]
    empty_topics: list[EmptyTopic]


def _is_covered(node_id: str, question_ids: set[str]) -> bool:
    prefix = node_id + "-"
    return any(qid == node_id or qid.startswith(prefix) for qid in question_ids)


def build_coverage(tree: QuestionTree, leaver: str, sessions: list[SessionRow],
                   answers: list[KBEntry], planned: int) -> CoverageReport:
    """Build a coverage report. `answers` must contain ended-session answers only."""
    question_ids = {a.question_id for a in answers}
    covered: dict[str, int] = {axis: 0 for axis in _AXIS_ORDER}
    totals: dict[str, int] = {axis: 0 for axis in _AXIS_ORDER}
    empty: list[EmptyTopic] = []
    for node in tree.nodes:
        totals[node.axis] += 1
        if _is_covered(node.id, question_ids):
            covered[node.axis] += 1
        else:
            empty.append(EmptyTopic(question_id=node.id, axis=node.axis,
                                    label=AXIS_LABELS[node.axis], text=node.text))
    axes = [AxisCoverage(axis=axis, label=AXIS_LABELS[axis], covered=covered[axis],
                         total=totals[axis]) for axis in _AXIS_ORDER]
    return CoverageReport(
        leaver=leaver,
        role=tree.role,
        sessions_done=sum(1 for s in sessions if s.ended_at is not None),
        sessions_planned=planned,
        items_total=len(answers),
        axes=axes,
        empty_topics=empty,
    )
