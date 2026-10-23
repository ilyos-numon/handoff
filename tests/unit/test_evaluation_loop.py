import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from handoff.contracts import SLOT_FOLLOW_UP_TEMPLATES, judge_slots_by_cues
from handoff.evaluation_loop import (
    ExpectedCard,
    LoopFixture,
    LoopMetrics,
    Ratio,
    judge_artifact,
    judge_cards,
    judge_drill,
    judge_gaps,
    load_fixture,
    main,
    unmet_targets,
)

FIXTURES = Path(__file__).resolve().parents[2] / "eval" / "fixtures"
LOOP_RE = re.compile(
    r"^LOOP depth_agree=\d\.\d{3} slot_target=\d\.\d{3} slot_fill=\d\.\d{3} "
    r"card_recall=\d\.\d{3} card_evidence=\d\.\d{3} gap_recall=\d\.\d{3} "
    r"loop_closed=\d+/\d+ artifact_cited=\d\.\d{3} drill_ok=\d+/\d+ pii_leaks=\d+$"
)
METRIC_NAMES = {
    "depth_agree", "slot_target", "slot_fill", "card_recall", "card_evidence",
    "gap_recall", "loop_closed", "artifact_cited", "drill_ok", "pii_leaks",
}


@pytest.fixture(scope="module")
def fixture() -> LoopFixture:
    return load_fixture(FIXTURES / "loop_eval.yaml")


def _card(cid: str, ctype: str, node: str, title: str, body: str) -> dict[str, Any]:
    return {
        "id": cid, "leaver": "eval", "role": "backend_developer", "session_id": "s1",
        "type": ctype, "title": title, "body": body, "entities": [], "confidence": 0.8,
        "evidence": [{"session_id": "s1", "question_id": node, "session_number": 1,
                      "excerpt": body[:200]}],
        "created_at": "2026-10-23T00:00:00Z",
    }


class FakeServer:
    """Minimal in-memory stand-in for the knowledge loop HTTP API."""

    def __init__(self, fixture: LoopFixture) -> None:
        self.fixture = fixture
        self.cards = [
            _card(f"c{i}", e.type, e.node, " ".join(e.terms), f"{' '.join(e.terms)} 관련 내용입니다.")
            for i, e in enumerate(fixture.expected_cards, start=1)
        ]
        self.fail: dict[str, int] = {}
        self.wrong_judge = 0          # first N judge calls return all "missing"
        self.follow_slot_override: str | None = None
        self.no_fill = False
        self.close_loop = True
        self.bad_section = False
        self.drill_extra: dict[str, Any] = {}
        self.feedback_extra: dict[str, Any] = {}
        self.gap_leak = ""
        self.gap_topics = ["수수료 검증 스크립트", "정산 배치 재실행 스크립트", "대행사 장애 연락망", "로그 보관 배치"]
        self.queue: list[dict[str, Any]] = []
        self.loop_answers: dict[str, str] = {}
        self.session_question: dict[str, Any] | None = None
        self.pending: list[dict[str, Any]] = []
        self.judge_calls = 0
        self.followup_given: set[str] = set()

    def _json(self, status: int, body: Any) -> httpx.Response:
        return httpx.Response(status, json=body)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for key, code in self.fail.items():
            if path.endswith(key):
                return self._json(code, {"detail": "provider unavailable"})
        body = json.loads(request.content) if request.content else {}
        method = request.method
        if (method, path) == ("POST", "/depth/judge"):
            self.judge_calls += 1
            j = judge_slots_by_cues(body["answer"])
            slots = dict(j.slots)
            if self.judge_calls <= self.wrong_judge:
                slots = {s: "missing" for s in slots}
            if self.no_fill and self.judge_calls % 2 == 0:
                slots = {s: "missing" for s in slots}
            return self._json(200, {"slots": slots, "missing": [s for s, v in slots.items() if v == "missing"]})
        if (method, path) == ("POST", "/depth/follow-up"):
            j = judge_slots_by_cues(body["answer"])
            slot = self.follow_slot_override or (j.missing[0] if j.missing else None)
            text = SLOT_FOLLOW_UP_TEMPLATES.get(slot)  # type: ignore[call-overload]
            return self._json(200, {"slot": slot, "text": text})
        if (method, path) == ("GET", "/leavers/eval/cards"):
            return self._json(200, self.cards)
        if (method, path) == ("POST", "/leavers/eval/sources/mock"):
            return self._json(201, [])
        if (method, path) == ("POST", "/leavers/eval/questions/generate"):
            return self._json(200, {"entities": [], "queued": []})
        if (method, path) == ("POST", "/leavers/eval/gaps/refresh"):
            gaps = [
                {"id": f"g{i}", "leaver": "eval", "kind": "mentioned_only", "topic": t,
                 "detail": f"자료에 나왔지만 지식 카드가 없습니다.{self.gap_leak}", "risk": "high",
                 "card_ids": [], "entity_ids": [], "queue_item_id": None,
                 "created_at": "2026-10-23T00:00:00Z"}
                for i, t in enumerate(self.gap_topics, start=1)
            ]
            return self._json(200, {"leaver": "eval", "gaps": gaps, "queued": []})
        if (method, path) == ("POST", "/kb/ask"):
            item = {"id": f"q{len(self.queue) + 1}", "leaver": body["leaver"], "role": "backend_developer",
                    "source": "successor", "axis": "routines", "text": body["question"],
                    "reason": "후임 미답 질문", "status": "pending", "created_at": "2026-10-23T00:00:00Z"}
            self.queue.append(item)
            self.pending.append(item)
            return self._json(200, {"answer": {"found": False, "answer": "", "citations": []}, "queued": item})
        if (method, path) == ("POST", "/sessions"):
            return self._json(201, {"session_id": "loop1", "question": self._next(), "number": 1})
        if (method, path) == ("POST", "/sessions/loop1/answers"):
            cur = self.session_question
            assert cur is not None
            if cur["kind"] == "queued":
                self.loop_answers[cur["id"]] = body["text"]
            if cur["kind"] == "queued" and cur["id"] not in self.followup_given:
                self.followup_given.add(cur["id"])
                nxt: dict[str, Any] | None = {"id": f"{cur['id']}-s1", "axis": "routines",
                                              "text": "그 방법이 안 되면 대안은 무엇인가요?",
                                              "kind": "follow_up"}
                self.session_question = nxt
            else:
                nxt = self._next()
            return self._json(200, {"next_question": nxt})
        if (method, path) == ("POST", "/sessions/loop1/end"):
            return self._json(200, {"session_id": "loop1", "ingested": 1, "number": 1, "summary": []})
        if (method, path) == ("POST", "/kb/query"):
            for item in self.queue:
                if item["text"] == body["question"] and item["id"] in self.loop_answers:
                    qid = item["id"] if self.close_loop else "rou-01"
                    return self._json(200, {"found": True, "answer": self.loop_answers[item["id"]],
                                            "citations": [{"session_id": "loop1", "question_id": qid,
                                                           "axis": "routines", "excerpt": "x",
                                                           "score": 0.9}]})
            return self._json(200, {"found": False, "answer": "", "citations": []})
        if method == "POST" and path.startswith("/leavers/eval/artifacts/"):
            kind = path.rsplit("/", 1)[1]
            sections = [{"heading": "절", "body": "본문", "card_ids": ["c1", "c2"]}]
            if self.bad_section:
                sections.append({"heading": "절2", "body": "본문", "card_ids": ["c999"]})
            return self._json(200, {"id": "a1", "leaver": "eval", "kind": kind, "title": "t",
                                    "sections": sections, "markdown": "# t", "card_ids": ["c1"],
                                    "generated_at": "2026-10-23T00:00:00Z"})
        if (method, path) == ("POST", "/leavers/eval/drills"):
            return self._json(201, {"id": "d1", "leaver": "eval", "title": "훈련", "situation": "상황",
                                    "ask": "어떻게?", "expected_points": ["로그를 확인한다", "리드에게 알린다"],
                                    "card_ids": ["c1"], "created_at": "2026-10-23T00:00:00Z",
                                    **self.drill_extra})
        if (method, path) == ("POST", "/drills/d1/answers"):
            points = ["로그를 확인한다", "리드에게 알린다"]
            covered = [p for p in points if p in body["text"]]
            return self._json(200, {"drill_id": "d1", "card_refs": ["c1"], "covered_points": covered,
                                    "missed_points": [p for p in points if p not in covered],
                                    "feedback": "근거 카드를 읽어 보세요.", **self.feedback_extra})
        if method == "GET" and path.endswith("/queue"):
            leaver = path.split("/")[2]
            return self._json(200, [q for q in self.queue if q["leaver"] == leaver])
        return self._json(404, {"detail": "not found"})

    def _next(self) -> dict[str, Any] | None:
        if self.pending:
            item = self.pending.pop(0)
            q: dict[str, Any] | None = {"id": item["id"], "axis": item["axis"], "text": item["text"],
                                        "kind": "queued"}
        else:
            q = {"id": "sys-01", "axis": "systems", "text": "주 질문", "kind": "main"}
        self.session_question = q
        return q


def _run(
    server: FakeServer, tmp_path: Path, capsys: pytest.CaptureFixture[str], *extra: str
) -> tuple[int, list[str], dict[str, Any] | None]:
    code = main(
        ["--base-url", "http://test", "--out-dir", str(tmp_path), "--name", "run",
         "--fixtures-dir", str(FIXTURES), *extra],
        transport=httpx.MockTransport(server.handler),
    )
    lines = capsys.readouterr().out.strip().splitlines()
    report = tmp_path / "run.json"
    data = json.loads(report.read_text(encoding="utf-8")) if report.exists() else None
    return code, lines, data


# --- fixture -------------------------------------------------------------------


def test_fixture_structure(fixture: LoopFixture) -> None:
    assert len(fixture.depth_cases) >= 8
    assert len(fixture.expected_cards) >= 10
    assert fixture.expected_gaps == ["수수료 검증", "정산 배치 재실행", "대행사 장애 연락망", "로그 보관"]
    assert len(fixture.loop_cases) == 2
    assert fixture.drill_answers.weak == "잘 모르겠습니다"
    successor = yaml.safe_load((FIXTURES / "successor_questions.yaml").read_text(encoding="utf-8"))
    texts = {q["id"]: q["text"] for q in successor["questions"]}
    assert [c.question for c in fixture.loop_cases] == [texts["q16"], texts["q17"]]
    mock = yaml.safe_load((FIXTURES / "mock_leaver.yaml").read_text(encoding="utf-8"))
    assert {c.node for c in fixture.expected_cards} <= set(mock["answers"])
    for case in fixture.depth_cases:
        assert case.target_slot() is not None


def test_fixture_rejects_case_without_missing_slot(tmp_path: Path) -> None:
    data = yaml.safe_load((FIXTURES / "loop_eval.yaml").read_text(encoding="utf-8"))
    data["depth_cases"][0]["gold"] = {s: "filled" for s in data["depth_cases"][0]["gold"]}
    (tmp_path / "loop_eval.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError):
        load_fixture(tmp_path / "loop_eval.yaml")


# --- full run ------------------------------------------------------------------


def test_full_run_meets_targets(
    fixture: LoopFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = FakeServer(fixture)
    code, lines, data = _run(server, tmp_path, capsys, "--require-targets")
    assert code == 0
    assert LOOP_RE.match(lines[-1]), lines[-1]
    assert data is not None
    assert set(data["metrics"]) == METRIC_NAMES
    m = data["metrics"]
    assert m["depth_agree"] == 1.0 and m["slot_target"] == 1.0 and m["slot_fill"] == 1.0
    assert m["card_recall"] == 1.0 and m["card_evidence"] == 1.0
    assert m["gap_recall"] == 1.0
    assert m["loop_closed"] == {"n": 2, "m": 2}
    assert m["artifact_cited"] == 1.0
    assert m["drill_ok"] == {"n": 2, "m": 2}
    assert m["pii_leaks"] == 0
    assert data["unmet"] == []
    md = (tmp_path / "run.md").read_text(encoding="utf-8")
    assert "| depth_agree |" in md and "## 후임 질문 루프" in md


def test_loop_answers_queued_and_follow_up(
    fixture: LoopFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = FakeServer(fixture)
    _run(server, tmp_path, capsys)
    assert server.loop_answers == {"q1": fixture.loop_cases[0].answer, "q2": fixture.loop_cases[1].answer}
    assert server.followup_given == {"q1", "q2"}


def test_gap_target_unmet_exits_one_with_require_targets(
    fixture: LoopFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = FakeServer(fixture)
    server.gap_topics = server.gap_topics[:2]
    code, lines, data = _run(server, tmp_path, capsys, "--require-targets")
    # FakeServer reports only 2 of 4 expected gaps → gap_recall 0.5 < 0.6
    assert data is not None and data["unmet"] == ["gap_recall"]
    assert code == 1
    assert LOOP_RE.match(lines[-1])


def test_unmet_without_require_targets_exits_zero(
    fixture: LoopFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = FakeServer(fixture)
    server.close_loop = False
    code, lines, data = _run(server, tmp_path, capsys)
    assert code == 0
    assert data is not None and data["metrics"]["loop_closed"] == {"n": 0, "m": 2}
    assert "loop_closed=0/2" in lines[-1]


# --- metrics -------------------------------------------------------------------


def test_depth_agree_counts_matching_slots(
    fixture: LoopFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = FakeServer(fixture)
    server.wrong_judge = 1  # first case judged all "missing"
    _, _, data = _run(server, tmp_path, capsys)
    assert data is not None
    first = fixture.depth_cases[0]
    agreed_first = sum(1 for v in first.gold.values() if v == "missing")
    total = len(fixture.depth_cases) * 5
    expected = round((total - 5 + agreed_first) / total, 3)
    assert data["metrics"]["depth_agree"] == expected
    assert data["depth"][0]["agreed"] == agreed_first


def test_slot_target_requires_slot_and_cue(
    fixture: LoopFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = FakeServer(fixture)
    server.follow_slot_override = "fallback"
    _, _, data = _run(server, tmp_path, capsys)
    assert data is not None
    targets = [c.target_slot() for c in fixture.depth_cases]
    expected = round(sum(1 for t in targets if t == "fallback") / len(targets), 3)
    assert data["metrics"]["slot_target"] == expected


def test_slot_target_text_without_cue_fails(fixture: LoopFixture) -> None:
    case = fixture.depth_cases[0]
    target = case.target_slot()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/depth/follow-up":
            return httpx.Response(200, json={"slot": target, "text": "더 말씀해 주세요."})
        slots = dict(case.gold)
        return httpx.Response(200, json={"slots": slots, "missing": []})

    from handoff.evaluation_loop import run_depth

    with httpx.Client(base_url="http://t", transport=httpx.MockTransport(handler)) as client:
        rows = run_depth(client, [case])
    assert rows[0].agreed == 5
    assert rows[0].target_ok is False
    assert rows[0].fill_ok is False  # gold target slot is still "missing"


def test_slot_fill_requires_target_filled(
    fixture: LoopFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = FakeServer(fixture)
    server.no_fill = True  # every refill judgement returns all "missing"
    _, _, data = _run(server, tmp_path, capsys)
    assert data is not None and data["metrics"]["slot_fill"] == 0.0


def test_judge_cards_recall_and_evidence() -> None:
    expected = [
        ExpectedCard(type="pitfall", node="rou-02", terms=["금요일"]),
        ExpectedCard(type="procedure", node="sys-04", terms=["백업", "테이블"]),
        ExpectedCard(type="contact", node="ppl-01", terms=["채널"]),
    ]
    cards = [
        _card("c1", "pitfall", "rou-02", "금요일 배포 금지", "금요일에는 배포하지 않습니다."),
        _card("c2", "procedure", "sys-04-f1", "데이터 수정", "백업 테이블에 복사한 뒤 실행합니다."),
        _card("c3", "procedure", "ppl-01", "채널", "채널에 올립니다."),  # wrong type
        _card("c4", "pitfall", "inc-01", "금요일", "금요일 조심"),         # same type, other node
    ]
    rows = judge_cards(expected, cards)
    assert [r.found for r in rows] == [True, True, False]
    assert [r.evidence_ok for r in rows] == [True, True, False]
    assert rows[0].card_ids == ["c1", "c4"]


def test_judge_cards_evidence_wrong_node() -> None:
    rows = judge_cards(
        [ExpectedCard(type="pitfall", node="rou-02", terms=["금요일"])],
        [_card("c1", "pitfall", "sys-01", "금요일", "금요일")],
    )
    assert rows[0].found is True and rows[0].evidence_ok is False


def test_judge_gaps_matches_topic_or_detail() -> None:
    gaps = [
        {"topic": "수수료 검증 스크립트", "detail": "카드가 없습니다."},
        {"topic": "정산", "detail": "로그 보관 배치 기록이 없습니다."},
    ]
    rows = judge_gaps(["수수료 검증", "로그 보관", "대행사 장애 연락망"], gaps)
    assert [r.found for r in rows] == [True, True, False]


def test_artifact_cited_counts_sections_with_real_cards(
    fixture: LoopFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = FakeServer(fixture)
    server.bad_section = True
    _, _, data = _run(server, tmp_path, capsys)
    assert data is not None and data["metrics"]["artifact_cited"] == 0.5
    row = judge_artifact("adr", {"sections": [{"card_ids": []}, {"card_ids": ["c1"]}]}, {"c1"})
    assert (row.sections, row.cited) == (2, 1)


def test_judge_drill_rules() -> None:
    drill = {"expected_points": ["a", "b"]}
    assert judge_drill("weak", drill, {"covered_points": [], "missed_points": ["a", "b"]}).ok
    assert judge_drill("x", drill, {"covered_points": ["a"], "missed_points": ["b"]}).ok
    assert not judge_drill("x", drill, {"covered_points": ["a"], "missed_points": []}).ok
    assert not judge_drill("x", drill, {"covered_points": ["a"], "missed_points": ["a", "b"]}).ok
    assert not judge_drill(
        "x", drill, {"covered_points": ["a"], "missed_points": ["b"], "score": 50}
    ).ok
    assert not judge_drill("x", {"expected_points": []}, {"covered_points": [], "missed_points": []}).ok
    six = {"expected_points": list("abcdef")}
    assert not judge_drill("x", six, {"covered_points": [], "missed_points": list("abcdef")}).ok


def test_drill_ok_counts_attempts(
    fixture: LoopFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = FakeServer(fixture)
    server.feedback_extra = {"grade": "B"}
    _, _, data = _run(server, tmp_path, capsys)
    assert data is not None and data["metrics"]["drill_ok"] == {"n": 0, "m": 2}
    assert [d["label"] for d in data["drill"]] == ["weak", "first_point"]
    assert [d["covered"] for d in data["drill"]] == [0, 1]


def test_pii_leaks_counted(
    fixture: LoopFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = FakeServer(fixture)
    server.cards[0]["body"] += " 문의는 ops@example.com 으로."
    server.gap_leak = " 담당 @pay-lead 010-0000-0000"
    _, lines, data = _run(server, tmp_path, capsys)
    assert data is not None
    assert data["pii_by_source"]["cards"] == 1
    assert data["pii_by_source"]["gaps"] == 8  # four gaps × (handle + phone)
    assert data["metrics"]["pii_leaks"] == 9
    assert lines[-1].endswith("pii_leaks=9")
    assert "example.com" not in (tmp_path / "run.json").read_text(encoding="utf-8")


def test_unmet_targets_rules() -> None:
    good = LoopMetrics(
        depth_agree=0.8, slot_target=0.8, slot_fill=0.7, card_recall=0.7, card_evidence=0.9,
        gap_recall=0.6, loop_closed=Ratio(n=2, m=2), artifact_cited=1.0,
        drill_ok=Ratio(n=2, m=2), pii_leaks=0,
    )
    assert unmet_targets(good) == []
    bad = good.model_copy(update={
        "depth_agree": 0.79, "artifact_cited": 0.99, "loop_closed": Ratio(n=1, m=2),
        "drill_ok": Ratio(n=0, m=0), "pii_leaks": 1,
    })
    assert unmet_targets(bad) == ["depth_agree", "artifact_cited", "loop_closed", "drill_ok", "pii_leaks"]


# --- errors --------------------------------------------------------------------


def test_http_error_prints_loop_error(
    fixture: LoopFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = FakeServer(fixture)
    server.fail = {"/artifacts/runbook": 409}
    code, lines, data = _run(server, tmp_path, capsys)
    assert code == 1
    assert lines[-1] == "LOOP ERROR artifact_runbook status=409"
    assert data is None


def test_depth_provider_error(
    fixture: LoopFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = FakeServer(fixture)
    server.fail = {"/depth/judge": 503}
    code, lines, _ = _run(server, tmp_path, capsys)
    assert code == 1
    assert lines[-1] == "LOOP ERROR depth_judge status=503"


def test_connection_error_status_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    code = main(
        ["--base-url", "http://test", "--out-dir", str(tmp_path), "--name", "run",
         "--fixtures-dir", str(FIXTURES)],
        transport=httpx.MockTransport(handler),
    )
    assert code == 1
    assert capsys.readouterr().out.strip().splitlines()[-1] == "LOOP ERROR depth_judge status=0"


def test_missing_fixture_prints_loop_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--base-url", "http://test", "--out-dir", str(tmp_path), "--name", "run",
                 "--fixtures-dir", str(tmp_path / "none")])
    assert code == 1
    assert capsys.readouterr().out.strip().splitlines()[-1] == "LOOP ERROR fixtures status=0"
