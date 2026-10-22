"""Question tree models and loader."""

import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

Axis = Literal["systems", "routines", "incidents", "people"]
AXIS_LABELS: dict[str, str] = {
    "systems": "담당 시스템",
    "routines": "반복 업무",
    "incidents": "사고 이력",
    "people": "관계자",
}

_ROLE_RE = re.compile(r"^[a-z_]+$")
_AXIS_PREFIX: dict[str, str] = {
    "systems": "sys",
    "routines": "rou",
    "incidents": "inc",
    "people": "ppl",
}
_MIN_NODES = 8
_MIN_NODES_PER_AXIS = 2


class FollowUp(BaseModel):
    id: str
    text: str


class QuestionNode(BaseModel):
    id: str
    axis: Axis
    text: str
    follow_ups: list[FollowUp] = Field(min_length=1)


class QuestionTree(BaseModel):
    role: str
    title: str
    version: int
    axes: list[Axis]
    nodes: list[QuestionNode]


class UnknownRoleError(LookupError):
    """Raised when no question tree exists for a role."""


def _check_constraints(tree: QuestionTree) -> None:
    all_axes = set(AXIS_LABELS)
    if set(tree.axes) != all_axes:
        raise ValueError(f"axes must be exactly {sorted(all_axes)}")
    if len(tree.nodes) < _MIN_NODES:
        raise ValueError(f"tree needs at least {_MIN_NODES} nodes, got {len(tree.nodes)}")
    counts: Counter[str] = Counter(node.axis for node in tree.nodes)
    for axis in all_axes:
        if counts[axis] < _MIN_NODES_PER_AXIS:
            raise ValueError(f"axis {axis} needs at least {_MIN_NODES_PER_AXIS} nodes")
    seen: set[str] = set()
    for node in tree.nodes:
        if not re.fullmatch(rf"{_AXIS_PREFIX[node.axis]}-\d{{2}}", node.id):
            raise ValueError(f"invalid node id {node.id!r} for axis {node.axis}")
        ids = [node.id] + [f.id for f in node.follow_ups]
        for follow_up in node.follow_ups:
            if not re.fullmatch(rf"{re.escape(node.id)}-f\d+", follow_up.id):
                raise ValueError(f"invalid follow-up id {follow_up.id!r} under {node.id}")
        for item_id in ids:
            if item_id in seen:
                raise ValueError(f"duplicate id {item_id!r}")
            seen.add(item_id)
    for prev, cur in zip(tree.nodes, tree.nodes[1:]):
        if prev.axis == cur.axis:
            raise ValueError(f"consecutive nodes {prev.id} and {cur.id} share axis {cur.axis}")
    if {node.axis for node in tree.nodes[:4]} != all_axes:
        raise ValueError("first 4 nodes must cover all four axes")


def _build_tree(data: Any) -> QuestionTree:
    """Validate raw tree data (schema + constraints). Raises ValueError on violation."""
    tree = QuestionTree.model_validate(data)
    _check_constraints(tree)
    return tree


def load_tree(role: str) -> QuestionTree:
    if not _ROLE_RE.fullmatch(role):
        raise UnknownRoleError(role)
    path = Path(__file__).parent / "question_trees" / f"{role}.yaml"
    if not path.is_file():
        raise UnknownRoleError(role)
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return _build_tree(data)
