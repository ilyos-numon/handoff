import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from handoff import live_check_loop, providers_qwen
from handoff.llm_tasks.artifacts import WRITE_ARTIFACT_SYSTEM
from handoff.llm_tasks.cards import EXTRACT_CARDS_SYSTEM
from handoff.llm_tasks.depth import JUDGE_SYSTEM, SLOT_SYSTEM
from handoff.llm_tasks.drill import MAKE_DRILL_SYSTEM, REVIEW_DRILL_SYSTEM
from handoff.llm_tasks.gaps import CONTRADICTION_SYSTEM
from handoff.llm_tasks.sources import EXTRACT_SYSTEM, QUESTIONS_SYSTEM
from handoff.llm_tasks.verify import VERIFY_SYSTEM
from handoff.providers_qwen import QwenLLM

KEY = "sk-loop-secret-key"
BASE = "https://llm.example.test/v1"
SYSTEMS = [
    EXTRACT_SYSTEM,
    QUESTIONS_SYSTEM,
    JUDGE_SYSTEM,
    SLOT_SYSTEM,
    EXTRACT_CARDS_SYSTEM,
    CONTRADICTION_SYSTEM,
    VERIFY_SYSTEM,
    WRITE_ARTIFACT_SYSTEM,
    MAKE_DRILL_SYSTEM,
    REVIEW_DRILL_SYSTEM,
]

Handler = Callable[[httpx.Request], httpx.Response]

REALISTIC: dict[str, dict[str, Any]] = {
    EXTRACT_SYSTEM: {
        "entities": [
            {"name": "정산 배치", "type": "job", "mention": "정산 배치", "source_id": "src1"},
            {"name": "메시지 큐", "type": "system", "mention": "메시지 큐", "source_id": "src1"},
        ]
    },
    QUESTIONS_SYSTEM: {
        "questions": [
            {
                "text": "정산 배치가 실패하면 무엇부터 확인하나요?",
                "axis": "routines",
                "entity_names": ["정산 배치"],
            }
        ]
    },
    JUDGE_SYSTEM: {
        "slots": {
            "why": "missing",
            "when": "missing",
            "who": "missing",
            "how": "filled",
            "fallback": "missing",
        }
    },
    SLOT_SYSTEM: {"question": "재실행이 안 되면 대안은 무엇인가요?"},
    EXTRACT_CARDS_SYSTEM: {
        "cards": [
            {
                "type": "procedure",
                "title": "정산 배치 실패 대응",
                "body": "메시지 큐 적체를 먼저 확인하고 재실행 스크립트를 실행합니다.",
                "entities": ["정산 배치"],
                "confidence": 0.8,
                "evidence_question_ids": ["sys-01"],
            },
            {
                "type": "contact",
                "title": "정산 배치 문의",
                "body": "문제가 생기면 결제팀 리드에게 먼저 연락합니다.",
                "entities": ["정산 배치"],
                "confidence": 0.6,
                "evidence_question_ids": ["rtn-01"],
            },
        ]
    },
    CONTRADICTION_SYSTEM: {
        "contradictions": [
            {
                "card_ids": ["c1", "c2"],
                "subject": "정산 배치",
                "explanation": "실행 시각이 다릅니다.",
            }
        ]
    },
    VERIFY_SYSTEM: {"verdicts": [{"supported": False, "answers_question": False}]},
    WRITE_ARTIFACT_SYSTEM: {
        "sections": [{"heading": "1주차", "body": "- 정산 배치 실패 대응", "card_ids": ["c1"]}]
    },
    MAKE_DRILL_SYSTEM: {
        "title": "훈련: 정산 배치 실패",
        "situation": "새벽에 정산 배치가 실패했습니다.",
        "ask": "무엇부터 확인하나요?",
        "expected_points": ["메시지 큐 적체를 먼저 확인합니다."],
        "card_ids": ["c1"],
    },
    REVIEW_DRILL_SYSTEM: {
        "covered_points": ["메시지 큐 적체를 먼저 확인합니다."],
        "missed_points": [],
        "feedback": "근거 카드 c1 을 다시 읽어 보세요.",
    },
}


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers_qwen, "_sleep", lambda _s: None)


def _chat(content: str) -> httpx.Response:
    message = {"role": "assistant", "content": content}
    return httpx.Response(200, json={"choices": [{"message": message}]})


def _dispatch(answers: dict[str, dict[str, Any]], seen: list[str]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        system = json.loads(request.content)["messages"][0]["content"]
        seen.append(system)
        payload = answers.get(system)
        return _chat("{}" if payload is None else json.dumps(payload, ensure_ascii=False))

    return handler


def _llm(handler: Handler) -> QwenLLM:
    return QwenLLM(BASE, "qwen-test", KEY, 5.0, transport=httpx.MockTransport(handler))


def _last_line(out: str) -> str:
    return out.strip().splitlines()[-1]


def test_realistic_responses_call_each_method_once(capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[str] = []
    assert live_check_loop.run(_llm(_dispatch(REALISTIC, seen))) == 0
    assert seen == SYSTEMS
    line = _last_line(capsys.readouterr().out)
    assert line.startswith(
        "LIVE LOOP OK entities=2 questions=1 depth=ok slot_q=ok cards=2 contradictions=1 "
        "verify_guard=ok artifact=ok drill=ok review=ok latency_ms="
    )
    assert line.rsplit("=", 1)[1].isdigit()


def test_unparseable_responses_fall_back_and_still_pass(
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[str] = []
    assert live_check_loop.run(_llm(_dispatch({}, seen))) == 0
    assert len(seen) == 10
    line = _last_line(capsys.readouterr().out)
    assert line.startswith("LIVE LOOP OK ")
    assert "FAIL" not in line
    assert "contradictions=2" in line  # fake: 새벽 2시 카드 2장 vs 새벽 4시 카드


def test_supported_unrelated_claim_fails_verify_guard(
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = dict(REALISTIC)
    answers[VERIFY_SYSTEM] = {"verdicts": [{"supported": True, "answers_question": True}]}
    assert live_check_loop.run(_llm(_dispatch(answers, []))) == 1
    line = _last_line(capsys.readouterr().out)
    assert line.startswith("LIVE LOOP OK ")
    assert "verify_guard=FAIL" in line


def test_off_target_slot_question_fails_slot_q(capsys: pytest.CaptureFixture[str]) -> None:
    answers = dict(REALISTIC)
    answers[SLOT_SYSTEM] = {"question": "그 밖에 더 말씀하실 것이 있나요?"}
    assert live_check_loop.run(_llm(_dispatch(answers, []))) == 1
    assert "slot_q=FAIL" in _last_line(capsys.readouterr().out)


def test_http_error_reports_step_and_class_only(capsys: pytest.CaptureFixture[str]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    assert live_check_loop.run(_llm(handler)) == 1
    out = capsys.readouterr().out
    assert _last_line(out) == "LIVE LOOP FAIL extract_entities: ProviderError"
    assert KEY not in out
    assert "정산" not in out


def test_error_mid_loop_names_that_step(capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[str] = []
    ok = _dispatch(REALISTIC, seen)

    def handler(request: httpx.Request) -> httpx.Response:
        system = json.loads(request.content)["messages"][0]["content"]
        if system == WRITE_ARTIFACT_SYSTEM:
            return httpx.Response(401)
        return ok(request)

    assert live_check_loop.run(_llm(handler)) == 1
    assert _last_line(capsys.readouterr().out) == "LIVE LOOP FAIL write_artifact: ProviderError"


def test_main_skips_for_fake(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HANDOFF_PROVIDER", "fake")
    assert live_check_loop.main() == 0
    assert capsys.readouterr().out.startswith("LIVE SKIP")


def test_main_skips_without_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HANDOFF_PROVIDER", "qwen")
    monkeypatch.delenv("HANDOFF_LLM_API_KEY", raising=False)
    assert live_check_loop.main() == 0
    assert capsys.readouterr().out.startswith("LIVE SKIP")


def test_main_qwen_runs_loop_without_leaking_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HANDOFF_PROVIDER", "qwen")
    monkeypatch.setenv("HANDOFF_LLM_API_KEY", KEY)
    monkeypatch.setenv("HANDOFF_LLM_BASE_URL", BASE)
    seen: list[str] = []
    auth: list[str] = []
    inner = _dispatch(REALISTIC, seen)

    def handler(request: httpx.Request) -> httpx.Response:
        auth.append(request.headers["Authorization"])
        return inner(request)

    def factory(*args: Any, **kwargs: Any) -> QwenLLM:
        return QwenLLM(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(live_check_loop, "QwenLLM", factory)
    assert live_check_loop.main() == 0
    out = capsys.readouterr().out
    assert _last_line(out).startswith("LIVE LOOP OK ")
    assert len(seen) == 10
    assert set(auth) == {f"Bearer {KEY}"}
    assert KEY not in out
    assert "messages" not in out
