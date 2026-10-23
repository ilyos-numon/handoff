"""Knowledge honesty API E2E (fake provider, real uvicorn subprocess, httpx)."""

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from handoff.contracts import PREMORTEM_QUESTIONS
from handoff.providers import FakeEmbedder
from handoff.signals import ROUTE_MIN_MATCH

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROLE = "backend_developer"
NO_MORE = "추가로 기억나는 것은 없습니다."
FORBIDDEN_KEYS = {"rank", "grade", "rating", "person", "owner"}
ROLE_SUFFIXES = ("팀 리드", "팀장", "리드", "담당자", "담당", "당번", "센터", "팀")

MAIN_ANSWERS = {
    "sys-01": (
        "정기결제 갱신 배치 배포가 가장 무섭습니다. 갱신 배치를 새벽 3시에 돌리는 이유는 낮에 돌리면 "
        "결제 대행사 승인 한도에 걸리기 때문입니다. 그래서 배포 전에 배치 스케줄 화면에서 다음 실행 "
        "시각을 먼저 확인합니다."
    ),
    "rou-01": (
        "매달 25일 오전 10시에 다음 달 청구 예정 알림을 관리자 화면에서 수동으로 실행합니다. 오전 10시로 "
        "정한 이유는 새벽에 보내면 고객센터 전화가 한꺼번에 몰리기 때문입니다."
    ),
    "inc-01": (
        "가장 오래 걸린 장애는 쿠폰 중복 할인 장애로 네 시간이 걸렸습니다. 원인은 아마 할인 계산 캐시가 "
        "갱신되지 않은 것 때문이라고 봅니다."
    ),
    "ppl-01": (
        "구독 청구 쪽에서 막히면 청구팀 리드에게 먼저 묻습니다. 결제 대행 연동 문제는 연동 담당자에게 "
        "문의합니다. 질문 글에는 발생 시각과 재현 순서, 관련 화면 캡처를 함께 붙여야 답이 빨리 옵니다."
    ),
}
CERTAIN_ANSWERS = {
    "sys-01": MAIN_ANSWERS["sys-01"],
    "rou-01": MAIN_ANSWERS["rou-01"],
    "inc-01": "가장 오래 걸린 장애는 쿠폰 중복 할인 장애로 네 시간이 걸렸습니다. 할인 계산 캐시를 비우고 해결했습니다.",
}
PREMORTEM_ANSWERS = (
    (
        "가장 먼저 터질 일은 파트너 정산 파일 수신 실패입니다. 외부 대행사가 파일 형식을 예고 없이 바꾸면 "
        "대사 작업이 깨지기 때문에 조심해야 합니다."
    ),
    "대사 결과 화면에서 불일치 건수가 늘어나는 것이 첫 징후입니다. 매일 아침 그 화면의 불일치 건수를 먼저 확인합니다.",
    "수신이 실패하면 먼저 연동 담당자에게 연락하고 수동 업로드 절차로 파일을 다시 받습니다.",
)
ROUTING_QUESTION = "연동 담당자 연락처는?"
CONTACT_CARD_TEXT = "결제 대행 연동 문제는 연동 담당자에게 문의합니다."

StartServer = Callable[..., Any]
Seen = list[Any]


def _keys(obj: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.add(k)
            found |= _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= _keys(v)
    return found


def _ok(resp: httpx.Response, code: int = 200, seen: Seen | None = None) -> Any:
    assert resp.status_code == code, resp.text
    body = resp.json()
    assert not (_keys(body) & FORBIDDEN_KEYS), resp.url
    if seen is not None:
        seen.append(body)
    return body


def _session(
    client: httpx.Client,
    leaver: str,
    mains: dict[str, str],
    *,
    queued: dict[str, str] | None = None,
    end: bool = True,
) -> str:
    """Drive one session: answer main questions from `mains`, queued by text, others NO_MORE."""
    start = _ok(client.post("/sessions", json={"role": ROLE, "leaver": leaver}), 201)
    sid = start["session_id"]
    question = start["question"]
    done_mains = 0
    for _ in range(30):
        if question is None:
            break
        if question["kind"] == "main":
            if done_mains >= len(mains):
                break
            text = mains[question["id"]]
            done_mains += 1
        elif question["kind"] == "queued" and queued and question["text"] in queued:
            text = queued[question["text"]]
        else:
            text = NO_MORE
        question = _ok(client.post(f"/sessions/{sid}/answers", json={"text": text}))[
            "next_question"
        ]
    if end:
        _ok(client.post(f"/sessions/{sid}/end"))
    return sid


def _seed(client: httpx.Client, leaver: str, mains: dict[str, str] | None = None) -> list[dict]:
    _session(client, leaver, mains or MAIN_ANSWERS)
    cards = _ok(client.get(f"/leavers/{leaver}/cards"))
    assert cards
    return cards


def test_fence_found_cites_cards_and_miss_queues_and_bad_leaver_422(
    start_server: StartServer, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    seen: Seen = []
    with httpx.Client(base_url=server.url, timeout=20) as client:
        leaver = "fence1"
        cards = _seed(client, leaver)
        valid = {c["id"] for c in cards}

        hit = _ok(
            client.post("/kb/fence", json={"leaver": leaver, "subject": "갱신 배치 새벽 3시 실행"}),
            seen=seen,
        )
        assert hit["found"] is True
        assert hit["reasons"]
        assert hit["queued"] is None
        for point in hit["reasons"] + hit["risks"]:
            assert point["card_ids"] and set(point["card_ids"]) <= valid
        assert {c["id"] for c in hit["cards"]} <= valid

        miss = _ok(
            client.post(
                "/kb/fence", json={"leaver": leaver, "subject": "사무실 화분 물 주는 요일"}
            ),
            seen=seen,
        )
        assert miss["found"] is False
        assert miss["reasons"] == []
        assert miss["queued"] is not None
        assert miss["queued"]["reason"] == "울타리 근거 없음"

        bad = client.post("/kb/fence", json={"leaver": "BAD LEAVER!", "subject": "배치"})
        assert bad.status_code == 422
        empty = client.post("/kb/fence", json={"leaver": leaver, "subject": "   "})
        assert empty.status_code == 422
    assert not (_keys(seen) & FORBIDDEN_KEYS)


def test_premortem_questions_asked_first_and_artifact_has_signs_and_response(
    start_server: StartServer, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    with httpx.Client(base_url=server.url, timeout=20) as client:
        leaver = "pre1"
        items = _ok(client.post(f"/leavers/{leaver}/premortem/questions", params={"role": ROLE}))
        assert [i["text"] for i in items] == list(PREMORTEM_QUESTIONS)
        assert {i["source"] for i in items} == {"premortem"}

        start = _ok(client.post("/sessions", json={"role": ROLE, "leaver": leaver}), 201)
        assert start["question"]["kind"] == "queued"
        assert start["question"]["text"] == PREMORTEM_QUESTIONS[0]
        sid = start["session_id"]
        answers = dict(zip(PREMORTEM_QUESTIONS, PREMORTEM_ANSWERS, strict=True))
        question = start["question"]
        for _ in range(20):
            if question is None:
                break
            text = answers.get(question["text"], NO_MORE)
            question = _ok(client.post(f"/sessions/{sid}/answers", json={"text": text}))[
                "next_question"
            ]
        _ok(client.post(f"/sessions/{sid}/end"))
        cards = _ok(client.get(f"/leavers/{leaver}/cards"))
        valid = {c["id"] for c in cards}
        assert valid

        art = _ok(client.post(f"/leavers/{leaver}/artifacts/premortem"))
        assert art["kind"] == "premortem"
        assert art["card_ids"] and set(art["card_ids"]) <= valid
        assert "징후:" in art["markdown"] and "대응:" in art["markdown"]
        assert art["markdown"].index("징후:") < art["markdown"].index("대응:")

        empty = client.post("/leavers/nobody/artifacts/premortem")
        assert empty.status_code == 409
        assert empty.json()["detail"] == "no cards"
        bad = client.post("/leavers/BAD LEAVER!/premortem/questions")
        assert bad.status_code == 422


def test_unknown_area_uncertain_cards_filter_and_gaps_unknown_areas(
    start_server: StartServer, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    with httpx.Client(base_url=server.url, timeout=20) as client:
        leaver = "unk1"
        _seed(client, leaver)
        unc = _ok(client.get(f"/leavers/{leaver}/cards", params={"certainty": "uncertain"}))
        assert unc
        assert all(c["certainty"] == "uncertain" and c["uncertain_cues"] for c in unc)
        assert any("아마" in c["uncertain_cues"] for c in unc)
        assert all(c["confidence"] <= 0.4 for c in unc)
        certain = _ok(client.get(f"/leavers/{leaver}/cards", params={"certainty": "certain"}))
        assert certain
        assert not ({c["id"] for c in unc} & {c["id"] for c in certain})

        report = _ok(client.post(f"/leavers/{leaver}/gaps/refresh", params={"role": ROLE}))
        areas = report["unknown_areas"]
        assert areas
        in_areas = {cid for a in areas for cid in a["card_ids"]}
        assert {c["id"] for c in unc} == in_areas
        again = _ok(client.get(f"/leavers/{leaver}/gaps"))
        assert again["unknown_areas"] == areas

        bad = client.get(f"/leavers/{leaver}/cards", params={"certainty": "maybe"})
        assert bad.status_code == 422


def test_unknown_area_empty_when_only_certain_answers(
    start_server: StartServer, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    with httpx.Client(base_url=server.url, timeout=20) as client:
        leaver = "unk2"
        cards = _seed(client, leaver, CERTAIN_ANSWERS)
        assert all(c["certainty"] == "certain" for c in cards)
        report = _ok(client.post(f"/leavers/{leaver}/gaps/refresh", params={"role": ROLE}))
        assert report["unknown_areas"] == []
        assert _ok(client.get(f"/leavers/{leaver}/cards", params={"certainty": "uncertain"})) == []


def test_at_risk_returns_cards_with_matching_event_and_rejects_bad_event(
    start_server: StartServer, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    with httpx.Client(base_url=server.url, timeout=20) as client:
        leaver = "risk1"
        cards = _seed(client, leaver)
        valid = {c["id"] for c in cards}
        report = _ok(
            client.get(f"/leavers/{leaver}/cards/at-risk", params={"event": "system_replaced"})
        )
        assert report["event"] == "system_replaced"
        assert report["label"]
        assert len(report["cards"]) >= 1
        for entry in report["cards"]:
            assert entry["card"]["id"] in valid
            assert entry["conditions"]
            assert {c["event"] for c in entry["conditions"]} == {"system_replaced"}

        none = _ok(
            client.get(
                f"/leavers/{leaver}/cards/at-risk",
                params={"event": "system_replaced", "target": "존재하지않는대상"},
            )
        )
        assert none["cards"] == []

        assert (
            client.get(
                f"/leavers/{leaver}/cards/at-risk", params={"event": "moon_landing"}
            ).status_code
            == 422
        )
        assert client.get(f"/leavers/{leaver}/cards/at-risk").status_code == 422


def test_routing_unanswered_question_points_to_contact_role_and_no_cards_has_no_route(
    start_server: StartServer, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    with httpx.Client(base_url=server.url, timeout=20) as client:
        leaver = "route1"
        cards = _seed(client, leaver)
        contact_ids = {c["id"] for c in cards if c["type"] == "contact"}
        assert contact_ids

        asked = _ok(client.post("/kb/ask", json={"question": ROUTING_QUESTION, "leaver": leaver}))
        assert asked["answer"]["found"] is False
        assert asked["queued"] is not None
        route = asked["route"]
        assert route is not None
        assert route["ask_role"].endswith(ROLE_SUFFIXES)
        assert route["basis"] == "contact_card"
        assert route["card_ids"] and set(route["card_ids"]) <= contact_ids

        lone = _ok(client.post("/kb/ask", json={"question": ROUTING_QUESTION, "leaver": "nobody"}))
        assert lone["answer"]["found"] is False
        assert lone["route"] is None
        assert lone["queued"] is not None


def test_routing_question_precondition_embed_score_below_threshold_and_bigram_overlap() -> None:
    """The routing question must be unanswered by KB (score < 0.25) yet overlap the contact text."""
    from handoff.contracts import containment

    embedder = FakeEmbedder()
    texts = [*MAIN_ANSWERS.values(), " ".join(MAIN_ANSWERS.values())]
    q_vec = embedder.embed([ROUTING_QUESTION])[0]
    best = max(sum(a * b for a, b in zip(q_vec, v, strict=True)) for v in embedder.embed(texts))
    assert best < 0.25
    assert containment(ROUTING_QUESTION, CONTACT_CARD_TEXT) >= ROUTE_MIN_MATCH


def test_tacit_cue_answer_gets_t1_follow_up_with_criteria_and_plain_answer_does_not(
    start_server: StartServer, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    with httpx.Client(base_url=server.url, timeout=20) as client:
        tacit_text = "배포 시점은 그냥 감으로 정합니다."
        plain_text = "배포는 매주 화요일 오후 두 시에 정해진 절차대로 합니다."
        results: dict[str, dict] = {}
        for leaver, text in (("tacit1", tacit_text), ("tacit2", plain_text)):
            start = _ok(client.post("/sessions", json={"role": ROLE, "leaver": leaver}), 201)
            body = _ok(client.post(f"/sessions/{start['session_id']}/answers", json={"text": text}))
            assert body["next_question"] is not None
            results[leaver] = body["next_question"]
        assert results["tacit1"]["id"].endswith("-t1")
        assert results["tacit1"]["kind"] == "follow_up"
        assert "기준" in results["tacit1"]["text"]
        assert not results["tacit2"]["id"].endswith("-t1")

        direct = _ok(
            client.post("/depth/tacit", json={"question": "언제 배포하나요?", "answer": tacit_text})
        )
        assert direct["cues"] and "기준" in direct["text"]
        none = _ok(
            client.post("/depth/tacit", json={"question": "언제 배포하나요?", "answer": plain_text})
        )
        assert none == {"cues": [], "text": None}

        empty = client.post("/depth/tacit", json={"question": "언제 배포하나요?", "answer": "   "})
        assert empty.status_code == 422
        missing = client.post("/depth/tacit", json={"question": "언제 배포하나요?"})
        assert missing.status_code == 422


def _run_repeat(args: list[str], out_dir: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "HANDOFF_PROVIDER": "fake"}
    env.pop("HANDOFF_LLM_API_KEY", None)
    return subprocess.run(
        [sys.executable, "-m", "handoff.evaluation_repeat", *args, "--out-dir", str(out_dir)],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=420,
    )


def test_repeat_aggregation_runs_twice_with_identical_metrics_and_rejects_zero_runs(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    proc = _run_repeat(["--runs", "2", "--suites", "base,honesty", "--name", "e2e-repeat"], out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "REPEAT runs=2 ok=2" in proc.stdout.strip().splitlines()[-1]
    report = json.loads((out / "e2e-repeat.json").read_text(encoding="utf-8"))
    assert report["runs"] == 2 and report["ok"] == 2
    stats = report["stats"]
    assert any(k.startswith("honesty.") for k in stats)
    assert any(k.startswith("base.") for k in stats)
    for name, stat in stats.items():
        if stat["runs"] == 2 and name != "base.max_session_seconds":
            assert stat["min"] == stat["max"], name
    assert (out / "e2e-repeat.md").read_text(encoding="utf-8").strip()
    assert not (_keys(report) & FORBIDDEN_KEYS)

    bad = _run_repeat(["--runs", "0", "--name", "e2e-bad"], tmp_path / "bad")
    assert bad.returncode == 2


def test_no_surveillance_keys_in_any_honesty_response(
    start_server: StartServer, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    seen: Seen = []
    with httpx.Client(base_url=server.url, timeout=20) as client:
        leaver = "watch1"
        _ok(client.post(f"/leavers/{leaver}/premortem/questions", params={"role": ROLE}), seen=seen)
        _session(
            client,
            leaver,
            MAIN_ANSWERS,
            queued=dict(zip(PREMORTEM_QUESTIONS, PREMORTEM_ANSWERS, strict=True)),
        )
        cards = _ok(client.get(f"/leavers/{leaver}/cards"), seen=seen)
        assert cards
        _ok(client.get(f"/leavers/{leaver}/cards", params={"certainty": "uncertain"}), seen=seen)
        _ok(
            client.get(f"/leavers/{leaver}/cards/at-risk", params={"event": "policy_changed"}),
            seen=seen,
        )
        _ok(client.post(f"/leavers/{leaver}/gaps/refresh", params={"role": ROLE}), seen=seen)
        _ok(client.get(f"/leavers/{leaver}/gaps"), seen=seen)
        _ok(
            client.post("/kb/fence", json={"leaver": leaver, "subject": "카드 토큰 보관"}),
            seen=seen,
        )
        _ok(
            client.post("/kb/ask", json={"question": ROUTING_QUESTION, "leaver": leaver}), seen=seen
        )
        _ok(
            client.post("/depth/tacit", json={"question": "q?", "answer": "그냥 합니다"}), seen=seen
        )
        _ok(client.post(f"/leavers/{leaver}/artifacts/premortem"), seen=seen)
    assert len(seen) >= 10
    assert not (_keys(seen) & FORBIDDEN_KEYS)


def test_ask_found_answer_carries_certainty_uncertain_vs_certain(
    start_server: StartServer, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    with httpx.Client(base_url=server.url, timeout=20) as client:
        _seed(client, "cert1")
        unsure = _ok(
            client.post(
                "/kb/ask",
                json={"question": "쿠폰 중복 할인 장애 원인은 무엇이었나요?", "leaver": "cert1"},
            )
        )
        assert unsure["answer"]["found"] is True
        assert unsure["route"] is None
        assert unsure["certainty"] == "uncertain"
        sure = _ok(
            client.post(
                "/kb/ask",
                json={
                    "question": "갱신 배치를 새벽 3시에 돌리는 이유는 무엇인가요?",
                    "leaver": "cert1",
                },
            )
        )
        assert sure["answer"]["found"] is True
        assert sure["certainty"] == "certain"


def test_fence_on_uncertain_card_reports_uncertain_and_stays_cited(
    start_server: StartServer, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    with httpx.Client(base_url=server.url, timeout=20) as client:
        cards = _seed(client, "fence2")
        valid = {c["id"] for c in cards}
        uncertain_ids = {c["id"] for c in cards if c["certainty"] == "uncertain"}
        assert uncertain_ids
        body = _ok(
            client.post("/kb/fence", json={"leaver": "fence2", "subject": "할인 계산 캐시 갱신"})
        )
        assert body["found"] is True
        assert body["certainty"] == "uncertain"
        assert body["queued"] is None
        cited = {cid for p in body["reasons"] + body["risks"] for cid in p["card_ids"]}
        assert cited and cited <= valid
        assert cited & uncertain_ids


def test_at_risk_target_filter_keeps_only_cards_mentioning_target(
    start_server: StartServer, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    with httpx.Client(base_url=server.url, timeout=20) as client:
        _seed(client, "risk2")
        report = _ok(
            client.get(
                "/leavers/risk2/cards/at-risk",
                params={"event": "system_replaced", "target": "배치"},
            )
        )
        assert report["target"] == "배치"
        assert report["cards"]
        for entry in report["cards"]:
            card = entry["card"]
            assert "배치" in card["title"] or "배치" in card["body"] or "배치" in card["entities"]


def test_tacit_follow_up_is_asked_only_once_per_thread(
    start_server: StartServer, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    with httpx.Client(base_url=server.url, timeout=20) as client:
        start = _ok(client.post("/sessions", json={"role": ROLE, "leaver": "tacit3"}), 201)
        sid = start["session_id"]
        first = _ok(
            client.post(
                f"/sessions/{sid}/answers", json={"text": "배포 시점은 그냥 감으로 정합니다."}
            )
        )["next_question"]
        assert first["id"].endswith("-t1")
        second = _ok(
            client.post(
                f"/sessions/{sid}/answers", json={"text": "경험상 느낌으로 보통 월요일에 합니다."}
            )
        )["next_question"]
        assert second is None or not second["id"].endswith("-t1")


def test_repeat_rejects_loop_suite_without_base(tmp_path: Path) -> None:
    proc = _run_repeat(["--runs", "1", "--suites", "loop", "--name", "e2e-loop"], tmp_path / "o")
    assert proc.returncode == 2


def test_premortem_enqueue_is_idempotent_for_same_leaver(
    start_server: StartServer, tmp_path: Path
) -> None:
    server = start_server(tmp_path / "data", {})
    with httpx.Client(base_url=server.url, timeout=20) as client:
        first = _ok(client.post("/leavers/pre2/premortem/questions", params={"role": ROLE}))
        again = _ok(client.post("/leavers/pre2/premortem/questions", params={"role": ROLE}))
        assert len(first) == len(again) == 3
        queue = _ok(client.get("/leavers/pre2/queue", params={"source": "premortem"}))
        assert len(queue) == 3
