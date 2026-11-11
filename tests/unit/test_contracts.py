import inspect
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from handoff import contracts as c
from handoff.config import load_settings
from handoff.contracts import (
    ARTIFACT_CARD_TYPES,
    ARTIFACT_TITLES,
    CARD_TYPE_AXIS,
    ENTITY_TYPE_AXIS,
    FAKE_COMPOSE_PREFIX,
    QUEUE_PRIORITY,
    SLOT_CUES,
    SLOT_FOLLOW_UP_TEMPLATES,
    SLOT_TARGET_CUES,
    SLOTS,
    AskRequest,
    CardDraft,
    ContradictionDraft,
    DrillDraft,
    GenerateQuestionsRequest,
    KnowledgeConflict,
    KnowledgeNotFound,
    SourceUploadRequest,
    char_bigrams,
    containment,
    is_queue_id,
    judge_slots_by_cues,
    split_sentences,
    topic_id,
)
from handoff.providers import LLM, FakeLLM, build_providers
from handoff.providers_qwen import QwenLLM

NEW_METHODS = (
    "extract_entities",
    "generate_questions",
    "judge_depth",
    "slot_follow_up",
    "extract_cards",
    "find_contradictions",
    "verify_claims",
    "write_artifact",
    "make_drill",
    "review_drill",
)


@pytest.mark.parametrize(
    ("qid", "expected"),
    [
        ("sys-01-s2", "sys-01"),
        ("q12-s1", "q12"),
        ("q12", "q12"),
        ("rou-03", "rou-03"),
        ("inc-02-f1", "inc-02"),
        ("other", "other"),
    ],
)
def test_topic_id(qid: str, expected: str) -> None:
    assert topic_id(qid) == expected


def test_is_queue_id() -> None:
    assert is_queue_id("q3")
    assert is_queue_id("q12-s1")
    assert not is_queue_id("sys-01")
    assert not is_queue_id("sys-01-s1")
    assert not is_queue_id("qq1")


def test_split_sentences() -> None:
    text = "먼저 확인합니다. 그다음 실행!  끝인가요?\n\n새 줄。 마지막"
    assert split_sentences(text) == ["먼저 확인합니다.", "그다음 실행!", "끝인가요?", "새 줄。", "마지막"]
    assert split_sentences("") == []
    assert split_sentences("  \n  ") == []


def test_char_bigrams_keeps_only_hangul_alnum_lowercased() -> None:
    assert char_bigrams("Ab, 가나!") == {"ab", "b가", "가나"}
    assert char_bigrams("a") == set()
    assert char_bigrams("!!") == set()


def test_containment() -> None:
    assert containment("금요일 배포", "금요일 배포는 피합니다") == 1.0
    assert containment("", "anything") == 0.0
    assert containment("ab", "xy") == 0.0
    assert containment("abc", "ab") == 0.5


def test_judge_slots_by_cues() -> None:
    j = judge_slots_by_cues("배포 전에 변경 목록을 확인합니다. 장애가 잦았기 때문입니다.")
    assert j.missing[0] == "when"
    assert j.slots["why"] == "filled"
    assert j.slots["how"] == "filled"
    assert set(j.slots) == set(SLOTS)
    assert j.missing == [s for s in SLOTS if j.slots[s] == "missing"]


def test_judge_slots_all_filled_and_none() -> None:
    full = "매주 팀 리드에게 알리고 절차대로 확인합니다. 장애 때문입니다. 안 되면 롤백합니다."
    assert judge_slots_by_cues(full).missing == []
    empty = judge_slots_by_cues("그냥 합니다")
    assert empty.missing == list(SLOTS)
    assert "not_applicable" not in empty.slots.values()


def test_constant_tables_cover_literals() -> None:
    assert set(SLOT_CUES) == set(SLOTS)
    assert set(SLOT_FOLLOW_UP_TEMPLATES) == set(SLOTS)
    assert set(SLOT_TARGET_CUES) == set(SLOTS)
    assert QUEUE_PRIORITY == {
        "manager": 0, "successor": 1, "gap": 2, "premortem": 3, "generated": 4
    }
    assert set(ENTITY_TYPE_AXIS) == {"system", "job", "incident", "role", "document", "external"}
    assert set(CARD_TYPE_AXIS) == {"procedure", "pitfall", "decision", "contact", "entity"}
    assert set(ARTIFACT_CARD_TYPES) == set(ARTIFACT_TITLES)
    for slot in SLOTS:
        template = SLOT_FOLLOW_UP_TEMPLATES[slot]
        assert any(cue in template for cue in SLOT_TARGET_CUES[slot])


def test_fake_compose_prefix_matches_fake_llm() -> None:
    assert FakeLLM().compose_answer("q", [_citation()]).startswith(FAKE_COMPOSE_PREFIX)


def test_error_types() -> None:
    assert str(KnowledgeNotFound(c.SESSION_NOT_FOUND)) == "session not found"
    assert isinstance(KnowledgeNotFound("x"), LookupError)
    assert isinstance(KnowledgeConflict("x"), RuntimeError)


def test_model_validation() -> None:
    with pytest.raises(ValidationError):
        SourceUploadRequest(kind="wiki", title="  ", text="x")
    with pytest.raises(ValidationError):
        SourceUploadRequest(kind="mail", title="t", text="x")  # type: ignore[arg-type]
    assert SourceUploadRequest(kind="cron", title=" t ", text="x").title == "t"
    assert GenerateQuestionsRequest().limit == 8
    assert GenerateQuestionsRequest().role == c.DEFAULT_ROLE
    with pytest.raises(ValidationError):
        GenerateQuestionsRequest(limit=21)
    with pytest.raises(ValidationError):
        AskRequest(question="q", leaver="Bad Name")
    with pytest.raises(ValidationError):
        CardDraft(type="pitfall", title="t", body="b", confidence=1.5, evidence_question_ids=["a"])
    with pytest.raises(ValidationError):
        CardDraft(type="pitfall", title="t", body="b", confidence=0.5, evidence_question_ids=[])
    with pytest.raises(ValidationError):
        ContradictionDraft(card_ids=["c1"], subject="s", explanation="e")
    with pytest.raises(ValidationError):
        DrillDraft(title="t", situation="s", ask="a", expected_points=[], card_ids=["c1"])


def test_no_score_fields_on_drill_models() -> None:
    for model in (c.DrillScenario, c.DrillFeedback, c.DrillFeedbackDraft):
        fields = set(model.model_fields)
        assert not fields & {"score", "grade", "rank"}


def test_llm_protocol_and_adapters_have_new_methods() -> None:
    for name in NEW_METHODS:
        assert hasattr(LLM, name)
        proto = inspect.signature(getattr(LLM, name))
        for impl in (FakeLLM, QwenLLM):
            assert inspect.signature(getattr(impl, name)).parameters.keys() == (
                proto.parameters.keys()
            )


def test_build_providers_still_works() -> None:
    _, llm = build_providers(load_settings({}))
    assert isinstance(llm, FakeLLM)


def _citation() -> c.Citation:
    return c.Citation(
        session_id="s1",
        question_id="sys-01",
        axis="systems",
        excerpt="근거",
        score=0.9,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_honesty_constants() -> None:
    assert ARTIFACT_CARD_TYPES["premortem"] == ("pitfall", "procedure")
    assert ARTIFACT_TITLES["premortem"] == "사전 부검: 먼저 터질 일 Top5"
    assert c.CHANGE_EVENTS == (
        "system_replaced",
        "vendor_changed",
        "team_reorganized",
        "policy_changed",
        "schedule_changed",
    )
    assert set(c.CHANGE_EVENT_LABELS) == set(c.CHANGE_EVENTS)
    assert len(c.PREMORTEM_QUESTIONS) == 3
    assert c.PREMORTEM_TOP == 5
    assert "{subject}" in c.FENCE_QUEUE_TEMPLATE
    assert "{subject}" in c.FENCE_QUESTION_TEMPLATE


def _new_card_kwargs() -> dict[str, object]:
    return {
        "leaver": "kim",
        "role": c.DEFAULT_ROLE,
        "session_id": "s1",
        "type": "pitfall",
        "title": "t",
        "body": "b",
        "entities": [],
        "confidence": 0.5,
        "evidence": [
            c.Evidence(session_id="s1", question_id="sys-01", session_number=1, excerpt="e")
        ],
    }


def test_new_card_defaults_keep_existing_construction() -> None:
    card = c.NewCard(**_new_card_kwargs())  # type: ignore[arg-type]
    assert card.certainty == "certain"
    assert card.uncertain_cues == []
    assert card.invalidated_when == []
    full = c.KnowledgeCard(
        **_new_card_kwargs(),  # type: ignore[arg-type]
        id="c1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert full.certainty == "certain"


def test_new_card_invalidated_when_max_three() -> None:
    cond = c.InvalidationCondition(event="vendor_changed", condition="업체가 바뀌면")
    with pytest.raises(ValidationError):
        c.NewCard(**_new_card_kwargs(), invalidated_when=[cond] * 4)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        c.InvalidationCondition(event="moved", condition="x")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        c.InvalidationCondition(event="vendor_changed", condition="  ")


def test_honesty_models_defaults_and_validation() -> None:
    answer = c.KBAnswer(found=False, answer="", citations=[])
    response = c.AskResponse(answer=answer, queued=None)
    assert response.route is None and response.certainty is None
    report = c.GapReport(leaver="kim", gaps=[], queued=[])
    assert report.unknown_areas == []
    with pytest.raises(ValidationError):
        c.UnknownArea(topic="t", card_ids=[], cues=[], detail="d")
    with pytest.raises(ValidationError):
        c.RouteCandidate(ref="c1", basis="contact_card", text="t", roles=[])
    with pytest.raises(ValidationError):
        c.FenceRequest(leaver="kim", subject="   ")
    assert c.FenceRequest(leaver="kim", subject=" 야간 배치 ").subject == "야간 배치"
    assert c.FenceRequest(leaver="kim", subject="x").role == c.DEFAULT_ROLE
    with pytest.raises(ValidationError):
        c.FencePoint(text="t", card_ids=[])
    draft = c.FenceDraft()
    assert draft.reasons == [] and draft.risks == []
    assert c.TacitFollowUpResponse(cues=[], text=None).text is None


def test_manager_reason_and_priority_question_request() -> None:
    assert c.MANAGER_REASON == "팀장 우선 질문"
    req = c.PriorityQuestionRequest.model_validate({"text": "  배포 승인은 누가 하나요?  "})
    assert req.text == "배포 승인은 누가 하나요?"
    assert req.axis is None
    assert req.role == c.DEFAULT_ROLE
    with_axis = c.PriorityQuestionRequest.model_validate(
        {"text": "x", "axis": "people", "role": "r"})
    assert (with_axis.axis, with_axis.role) == ("people", "r")
    for bad in ({"text": "   "}, {"text": "a" * 301}, {"text": "x", "axis": "nope"}):
        with pytest.raises(ValidationError):
            c.PriorityQuestionRequest.model_validate(bad)
