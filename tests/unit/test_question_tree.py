from collections import Counter
from typing import Any

import pytest

from handoff.question_tree import (
    AXIS_LABELS,
    QuestionTree,
    UnknownRoleError,
    _build_tree,
    load_tree,
)

AXES = {"systems", "routines", "incidents", "people"}


@pytest.fixture
def tree() -> QuestionTree:
    return load_tree("backend_developer")


def _raw() -> dict[str, Any]:
    return load_tree("backend_developer").model_dump()


def test_load_success(tree: QuestionTree) -> None:
    assert tree.role == "backend_developer"
    assert tree.title == "백엔드 개발자"
    assert tree.version == 1
    assert set(tree.axes) == AXES
    assert len(tree.nodes) >= 8


def test_axis_labels() -> None:
    assert AXIS_LABELS == {
        "systems": "담당 시스템",
        "routines": "반복 업무",
        "incidents": "사고 이력",
        "people": "관계자",
    }


def test_at_least_two_nodes_per_axis(tree: QuestionTree) -> None:
    counts = Counter(n.axis for n in tree.nodes)
    assert set(counts) == AXES
    assert all(c >= 2 for c in counts.values())


def test_every_node_has_follow_up(tree: QuestionTree) -> None:
    assert all(1 <= len(n.follow_ups) <= 2 for n in tree.nodes)


def test_ids_unique(tree: QuestionTree) -> None:
    ids = [n.id for n in tree.nodes] + [f.id for n in tree.nodes for f in n.follow_ups]
    assert len(ids) == len(set(ids))


def test_consecutive_axes_differ(tree: QuestionTree) -> None:
    for a, b in zip(tree.nodes, tree.nodes[1:]):
        assert a.axis != b.axis


def test_first_four_cover_all_axes(tree: QuestionTree) -> None:
    assert {n.axis for n in tree.nodes[:4]} == AXES


def test_first_node_fixed(tree: QuestionTree) -> None:
    first = tree.nodes[0]
    assert first.id == "sys-01"
    assert first.axis == "systems"
    assert first.text == "담당하는 시스템 중 배포가 가장 무서운 부분은 어디인가요? 왜요?"
    assert first.follow_ups[0].id == "sys-01-f1"
    assert first.follow_ups[0].text == (
        "그 부분에서 문제가 났을 때 어떻게 복구했나요? 지금도 같은 방법이 통하나요?"
    )


@pytest.mark.parametrize("role", ["unknown_role", "../x", "Backend", "", "a/b"])
def test_unknown_role(role: str) -> None:
    with pytest.raises(UnknownRoleError):
        load_tree(role)


def test_unknown_role_is_lookup_error() -> None:
    assert issubclass(UnknownRoleError, LookupError)


def test_build_tree_accepts_valid_data() -> None:
    assert _build_tree(_raw()).nodes[0].id == "sys-01"


def test_too_few_nodes_rejected() -> None:
    data = _raw()
    data["nodes"] = data["nodes"][:7]
    with pytest.raises(ValueError):
        _build_tree(data)


def test_duplicate_id_rejected() -> None:
    data = _raw()
    data["nodes"][4]["id"] = "sys-01"
    data["nodes"][4]["follow_ups"][0]["id"] = "sys-01-f1"
    with pytest.raises(ValueError, match="duplicate"):
        _build_tree(data)


def test_consecutive_same_axis_rejected() -> None:
    data = _raw()
    n = data["nodes"]
    # systems, routines, incidents, people, people, systems, routines, incidents
    data["nodes"] = [n[0], n[1], n[2], n[3], n[7], n[4], n[5], n[6]]
    with pytest.raises(ValueError, match="consecutive"):
        _build_tree(data)


def test_first_four_not_covering_axes_rejected() -> None:
    data = _raw()
    n = data["nodes"]
    # systems, routines, incidents, routines, people, systems, incidents, people
    data["nodes"] = [n[0], n[1], n[2], n[5], n[3], n[4], n[6], n[7]]
    with pytest.raises(ValueError, match="first 4"):
        _build_tree(data)


def test_missing_follow_ups_rejected() -> None:
    data = _raw()
    data["nodes"][2]["follow_ups"] = []
    with pytest.raises(ValueError):
        _build_tree(data)


def test_bad_node_id_format_rejected() -> None:
    data = _raw()
    data["nodes"][0]["id"] = "sys-1"
    data["nodes"][0]["follow_ups"][0]["id"] = "sys-1-f1"
    with pytest.raises(ValueError):
        _build_tree(data)


def test_bad_follow_up_id_format_rejected() -> None:
    data = _raw()
    data["nodes"][1]["follow_ups"][0]["id"] = "rou-99-f1"
    with pytest.raises(ValueError):
        _build_tree(data)


def test_axis_with_one_node_rejected() -> None:
    data = _raw()
    # replace the second people node (index 7) with a systems node; nodes[6] is incidents
    node = data["nodes"][7]
    node["axis"] = "systems"
    node["id"] = "sys-09"
    node["follow_ups"] = [{"id": "sys-09-f1", "text": "x"}]
    with pytest.raises(ValueError):
        _build_tree(data)
