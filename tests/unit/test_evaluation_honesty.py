import re
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from pydantic import ValidationError

from handoff.contracts import PREMORTEM_QUESTIONS, split_sentences
from handoff.evaluation_honesty import (
    FIRST_NODES,
    HONESTY_TARGETS,
    NO_MORE_ANSWER,
    AtRiskCase,
    FenceCase,
    HonestyFixture,
    HonestyMetrics,
    RoutingCase,
    TacitCase,
    UncertainCase,
    _session_text,
    count_pii,
    count_surveillance_keys,
    judge_at_risk,
    judge_fence,
    judge_premortem,
    judge_routing,
    judge_tacit,
    judge_uncertain,
    load_fixture,
    main,
    unmet_targets,
)
from handoff.privacy import find_pii
from handoff.signals import TACIT_CUES

FIXTURES = Path(__file__).resolve().parents[2] / "eval" / "fixtures"
HONESTY_RE = re.compile(
    r"^HONESTY fence_accuracy=\d\.\d{3} fence_fabricated=\d+ uncertain_detect=\d\.\d{3} "
    r"tacit_followup=\d\.\d{3} routing=\d\.\d{3} at_risk=\d\.\d{3} premortem_cited=\d\.\d{3} "
    r"pii_leaks=\d+ surveillance_keys=\d+$"
)


@pytest.fixture(scope="module")
def fixture() -> HonestyFixture:
    return load_fixture(FIXTURES / "honesty_eval.yaml")


def _raw() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((FIXTURES / "honesty_eval.yaml").read_text("utf-8"))
    return data


def _card(cid: str, title: str, body: str, certainty: str = "certain") -> dict[str, Any]:
    return {"id": cid, "type": "pitfall", "title": title, "body": body, "certainty": certainty}


# ---- fixture ------------------------------------------------------------------


def test_fixture_counts_match_contract(fixture: HonestyFixture) -> None:
    assert fixture.role == "backend_developer"
    assert [s.nodes for s in fixture.sessions] == [3, 3]
    assert set(FIRST_NODES) <= set(fixture.answers)
    assert len(fixture.premortem_answers) == len(PREMORTEM_QUESTIONS)
    tacit_true = [c for c in fixture.tacit_cases if c.tacit]
    assert len(fixture.tacit_cases) >= 8 and len(tacit_true) == 5
    assert sorted(cue for c in tacit_true for cue in TACIT_CUES if cue in c.answer) == sorted(
        TACIT_CUES
    )
    assert sum(c.uncertain for c in fixture.uncertain_cases) == 3
    assert sum(not c.uncertain for c in fixture.uncertain_cases) == 3
    assert len({c.event for c in fixture.at_risk_cases}) == len(fixture.at_risk_cases) >= 3
    assert sum(c.expect_found for c in fixture.fence_cases) == 3
    assert sum(not c.expect_found for c in fixture.fence_cases) == 2
    assert len(fixture.routing_cases) >= 4


def test_fixture_has_no_pii_and_no_copied_sentences(fixture: HonestyFixture) -> None:
    raw = (FIXTURES / "honesty_eval.yaml").read_text("utf-8")
    assert find_pii(raw) == []
    others = "".join(
        (FIXTURES / name).read_text("utf-8") for name in ("mock_leaver.yaml", "loop_eval.yaml")
    )
    for text in fixture.answer_texts():
        for sentence in split_sentences(text):
            assert sentence not in others


def test_fixture_rejects_missing_tree_node() -> None:
    data = _raw()
    del data["answers"]["ppl-01"]
    with pytest.raises(ValidationError, match="ppl-01"):
        HonestyFixture.model_validate(data)


def test_fixture_rejects_tacit_true_without_cue() -> None:
    data = _raw()
    first_true = next(c for c in data["tacit_cases"] if c["tacit"])
    first_true["answer"] = "지난달 판매량을 보고 정합니다."
    with pytest.raises(ValidationError):
        HonestyFixture.model_validate(data)


def test_fixture_rejects_non_unique_uncertain_term() -> None:
    data = _raw()
    data["uncertain_cases"][0]["term"] = "배포"
    with pytest.raises(ValidationError, match="exactly one"):
        HonestyFixture.model_validate(data)


def test_fixture_rejects_duplicate_at_risk_event() -> None:
    data = _raw()
    data["at_risk_cases"][1]["event"] = data["at_risk_cases"][0]["event"]
    with pytest.raises(ValidationError, match="distinct"):
        HonestyFixture.model_validate(data)


def test_honesty_targets_cover_every_metric() -> None:
    assert set(HONESTY_TARGETS) == set(HonestyMetrics.model_fields)


# ---- session answers ----------------------------------------------------------


def test_session_text_by_kind(fixture: HonestyFixture) -> None:
    assert _session_text(fixture, {"kind": "main", "id": "sys-01"}) == fixture.answers[
        "sys-01"
    ].main
    assert _session_text(fixture, {"kind": "follow_up", "id": "sys-01-t1"}) == fixture.answers[
        "sys-01"
    ].follow_up
    assert _session_text(
        fixture, {"kind": "queued", "id": "q2", "text": PREMORTEM_QUESTIONS[1]}
    ) == fixture.premortem_answers[1]
    assert _session_text(fixture, {"kind": "queued", "id": "q9", "text": "다른 질문"}) == (
        NO_MORE_ANSWER
    )
    assert _session_text(fixture, {"kind": "follow_up", "id": "q2-s1"}) == NO_MORE_ANSWER


# ---- judges -------------------------------------------------------------------


def test_judge_tacit_true_and_false() -> None:
    yes = TacitCase(question="q", answer="그냥 합니다", tacit=True)
    no = TacitCase(question="q", answer="정해진 표대로 합니다", tacit=False)
    assert judge_tacit(yes, {"cues": ["그냥"], "text": "판단 기준은 무엇인가요?"}).ok
    assert not judge_tacit(yes, {"cues": ["그냥"], "text": "더 말해 주세요"}).ok
    assert not judge_tacit(yes, {"cues": [], "text": None}).ok
    assert judge_tacit(no, {"cues": [], "text": None}).ok
    assert not judge_tacit(no, {"cues": ["보통"], "text": "기준은?"}).ok


def test_judge_uncertain_requires_unknown_area() -> None:
    cards = [
        _card("c2", "캐시 문제", "아마 할인 캐시 때문입니다", "uncertain"),
        _card("c1", "다른 카드", "할인 캐시 설정을 확인합니다"),
    ]
    case = UncertainCase(term="할인 캐시", uncertain=True)
    # first card by id order is c1 (certain) -> mismatch
    assert not judge_uncertain(case, cards, {"unknown_areas": []}).ok
    only = [cards[0]]
    hit = judge_uncertain(case, only, {"unknown_areas": [{"card_ids": ["c2"]}]})
    assert hit.ok and hit.card_id == "c2" and hit.in_unknown
    assert not judge_uncertain(case, only, {"unknown_areas": []}).ok
    certain = UncertainCase(term="설정", uncertain=False)
    assert judge_uncertain(certain, cards, {}).ok
    missing = judge_uncertain(UncertainCase(term="없는 구절", uncertain=False), cards, {})
    assert not missing.ok and missing.card_id is None


def test_judge_at_risk_counts_found_and_absent_terms() -> None:
    report = {"cards": [{"card": _card("c1", "파일 형식", "외부 대행사가 바꿉니다")}]}
    case = AtRiskCase(
        event="vendor_changed", expect_terms=["파일 형식", "없는 말"], absent_terms=["대행사", "캐시"]
    )
    row = judge_at_risk(case, report)
    assert (row.hits, row.total) == (2, 4)
    assert row.found_terms == ["파일 형식"] and row.leaked_terms == ["대행사"]


def test_judge_fence_found_case() -> None:
    case = FenceCase(subject="배치 새벽 실행", expect_found=True, expect_terms=["승인 한도"])
    resp = {
        "found": True,
        "reasons": [{"text": "승인 한도에 걸리기 때문입니다", "card_ids": ["c1"]}],
        "risks": [],
        "cards": [],
        "queued": None,
    }
    assert judge_fence(case, resp, {"c1"}).ok
    assert not judge_fence(case, resp, {"c2"}).ok  # cites a card the leaver does not have
    wrong_term = FenceCase(subject="s", expect_found=True, expect_terms=["고객센터"])
    assert not judge_fence(wrong_term, resp, {"c1"}).ok


def test_judge_fence_not_found_case_and_fabrication() -> None:
    case = FenceCase(subject="사내 식당 메뉴", expect_found=False)
    miss = judge_fence(case, {"found": False, "queued": {"id": "q1"}}, set())
    assert miss.ok and not miss.fabricated
    assert not judge_fence(case, {"found": False, "queued": None}, set()).ok
    made_up = judge_fence(case, {"found": True, "reasons": [], "queued": None}, set())
    assert made_up.fabricated and not made_up.ok


def test_judge_routing() -> None:
    case = RoutingCase(question="누구에게 묻나요?", expect_role_terms=["청구팀"])
    route = {"ask_role": "청구팀 리드"}
    assert judge_routing(case, {"answer": {"found": False}, "route": route}).ok
    assert not judge_routing(case, {"answer": {"found": True}, "route": route}).ok
    assert not judge_routing(case, {"answer": {"found": False}, "route": None}).ok
    other = {"ask_role": "운영 당번"}
    assert not judge_routing(case, {"answer": {"found": False}, "route": other}).ok
    not_a_role = {"ask_role": "청구팀 공지"}
    assert not judge_routing(case, {"answer": {"found": False}, "route": not_a_role}).ok


def test_judge_premortem_sections() -> None:
    art = {
        "sections": [
            {"heading": "1. a", "body": "징후: x\n대응: y", "card_ids": ["c1", "c2"]},
            {"heading": "2. b", "body": "징후: x", "card_ids": ["c1"]},
            {"heading": "3. c", "body": "징후: x\n대응: y", "card_ids": ["c9"]},
        ]
    }
    assert [r.ok for r in judge_premortem(art, {"c1", "c2"})] == [True, False, False]
    assert judge_premortem(None, {"c1"}) == []


def test_count_surveillance_keys_exact_names_nested() -> None:
    data = {"a": [{"rank": 1, "person_note": 2}, {"x": {"Owner": "r", "grade": 3}}], "rating": 0}
    assert count_surveillance_keys(data) == 4
    assert count_surveillance_keys({"score": 1, "card_ids": ["rank"]}) == 0


def test_count_pii_per_source() -> None:
    counts = count_pii({"cards": [{"body": "문의는 ops@example.com 으로"}], "gaps": {"x": "없음"}})
    assert counts == {"cards": 1, "gaps": 0}


def test_unmet_targets_min_and_max() -> None:
    good = HonestyMetrics(
        fence_accuracy=0.8, fence_fabricated=0, uncertain_detect=0.8, tacit_followup=0.8,
        routing=0.75, at_risk=0.7, premortem_cited=1.0, pii_leaks=0, surveillance_keys=0,
    )
    assert unmet_targets(good) == []
    bad = good.model_copy(update={"routing": 0.5, "fence_fabricated": 1, "surveillance_keys": 2})
    assert unmet_targets(bad) == ["fence_fabricated", "routing", "surveillance_keys"]


# ---- CLI ----------------------------------------------------------------------


def _empty_server(request: httpx.Request) -> httpx.Response:
    path, method = request.url.path, request.method
    if path == "/depth/tacit":
        return httpx.Response(200, json={"cues": [], "text": None})
    if path.endswith("/premortem/questions"):
        return httpx.Response(200, json=[])
    if path == "/sessions":
        return httpx.Response(201, json={"session_id": "s1", "number": 1, "question": None})
    if path.endswith("/end"):
        return httpx.Response(200, json={"summary": []})
    if path.endswith("/cards") and method == "GET":
        return httpx.Response(200, json=[])
    if path.endswith("/gaps/refresh"):
        return httpx.Response(200, json={"gaps": [], "unknown_areas": []})
    if path.endswith("/cards/at-risk"):
        return httpx.Response(200, json={"cards": []})
    if path == "/kb/fence":
        return httpx.Response(200, json={"found": False, "queued": {"id": "q1"}})
    if path == "/kb/ask":
        return httpx.Response(200, json={"answer": {"found": False}, "route": None})
    if path.endswith("/artifacts/premortem"):
        return httpx.Response(409, json={"detail": "no cards"})
    return httpx.Response(404, json={"detail": "not found"})


def test_main_unmet_with_require_targets_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["--base-url", "http://t", "--out-dir", str(tmp_path), "--name", "h",
            "--fixtures-dir", str(FIXTURES)]
    assert main([*argv, "--require-targets"], transport=httpx.MockTransport(_empty_server)) == 1
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert HONESTY_RE.match(last)
    assert "routing=0.000" in last
    assert (tmp_path / "h.md").exists() and (tmp_path / "h.json").exists()
    assert main(argv, transport=httpx.MockTransport(_empty_server)) == 0


def test_main_http_error_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    argv = ["--base-url", "http://t", "--out-dir", str(tmp_path), "--name", "h",
            "--fixtures-dir", str(FIXTURES)]
    assert main(argv, transport=httpx.MockTransport(broken)) == 2
    assert capsys.readouterr().out.strip() == "HONESTY ERROR depth_tacit status=500"


def test_main_missing_fixture_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["--base-url", "http://t", "--out-dir", str(tmp_path), "--name", "h",
            "--fixtures-dir", str(tmp_path)]
    assert main(argv, transport=httpx.MockTransport(_empty_server)) == 2
    assert capsys.readouterr().out.strip() == "HONESTY ERROR fixtures status=0"
