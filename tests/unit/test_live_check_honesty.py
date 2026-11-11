import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from handoff import live_check_honesty, providers_qwen
from handoff.contracts import ArtifactSection, RouteDraft
from handoff.llm_tasks.artifacts import WRITE_ARTIFACT_SYSTEM
from handoff.llm_tasks.expiry import ANNOTATE_SYSTEM
from handoff.llm_tasks.fence import EXPLAIN_FENCE_SYSTEM
from handoff.llm_tasks.routing import ROUTE_SYSTEM
from handoff.llm_tasks.tacit import TACIT_SYSTEM
from handoff.providers import FakeLLM
from handoff.providers_qwen import QwenLLM

KEY = "sk-honesty-secret-key"
BASE = "https://llm.example.test/v1"
SYSTEMS = [EXPLAIN_FENCE_SYSTEM, ANNOTATE_SYSTEM, ROUTE_SYSTEM, TACIT_SYSTEM, WRITE_ARTIFACT_SYSTEM]

Handler = Callable[[httpx.Request], httpx.Response]

REALISTIC: dict[str, dict[str, Any]] = {
    EXPLAIN_FENCE_SYSTEM: {
        "reasons": [{"text": "택배사 집하 차량이 4시에 오기 때문입니다.", "card_ids": ["c1"]}],
        "risks": [{"text": "당일 출고가 밀립니다.", "card_ids": ["c2"]}],
    },
    ANNOTATE_SYSTEM: {
        "cards": [
            {
                "index": 0,
                "invalidated_when": [
                    {"event": "vendor_changed", "condition": "택배사가 바뀌면"}
                ],
            },
            {
                "index": 1,
                "invalidated_when": [
                    {"event": "system_replaced", "condition": "재고 시스템이 바뀌면"}
                ],
            },
            {
                "index": 2,
                "invalidated_when": [
                    {"event": "system_replaced", "condition": "대기열 구조가 바뀌면"}
                ],
            },
        ]
    },
    ROUTE_SYSTEM: {"ref": "c1", "role": "물류운영팀 리드"},
    TACIT_SYSTEM: {"question": "'감으로' 라고 하셨는데, 어떤 기준으로 인원을 나누나요?"},
    WRITE_ARTIFACT_SYSTEM: {
        "sections": [
            {
                "heading": "1. 출고 마감 직전 재고 동기화",
                "body": "징후: 피킹 목록 지연 알림이 옵니다.\n대응: 대기열을 먼저 확인합니다.",
                "card_ids": ["c2", "c3"],
            }
        ]
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
    assert live_check_honesty.run(_llm(_dispatch(REALISTIC, seen))) == 0
    assert seen == SYSTEMS
    line = _last_line(capsys.readouterr().out)
    assert line.startswith(
        "LIVE HONESTY OK fence=2 expiry=3 route=물류운영팀 리드 tacit=ok premortem=1 latency_ms="
    )
    assert line.rsplit("=", 1)[1].isdigit()


def test_unparseable_responses_fall_back_and_still_pass(
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[str] = []
    assert live_check_honesty.run(_llm(_dispatch({}, seen))) == 0
    assert seen == SYSTEMS
    line = _last_line(capsys.readouterr().out)
    assert line.startswith("LIVE HONESTY OK ")
    assert "tacit=ok" in line
    assert "premortem=1" in line  # fake: 함정 카드 1장


def test_fake_llm_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert live_check_honesty.run(FakeLLM()) == 0
    assert _last_line(capsys.readouterr().out).startswith("LIVE HONESTY OK ")


def test_route_null_reports_none(capsys: pytest.CaptureFixture[str]) -> None:
    answers = dict(REALISTIC)
    answers[ROUTE_SYSTEM] = {"ref": None, "role": None}
    assert live_check_honesty.run(_llm(_dispatch(answers, []))) == 0
    assert " route=none " in _last_line(capsys.readouterr().out)


def test_fence_point_with_foreign_card_id_fails(capsys: pytest.CaptureFixture[str]) -> None:
    answers = dict(REALISTIC)
    answers[EXPLAIN_FENCE_SYSTEM] = {
        "reasons": [{"text": "지어낸 이유입니다.", "card_ids": ["c99"]}],
        "risks": [],
    }
    assert live_check_honesty.run(_llm(_dispatch(answers, []))) == 1
    assert _last_line(capsys.readouterr().out) == "LIVE HONESTY FAIL explain_fence: CheckFailed"


def _stub(method: str, result: Any) -> FakeLLM:
    """FakeLLM with one method replaced by a result that breaks a check."""
    llm = FakeLLM()
    setattr(llm, method, result)
    return llm


@pytest.mark.parametrize(
    ("step", "method", "result"),
    [
        ("annotate_invalidation", "annotate_invalidation", lambda _cards: [[]]),
        (
            "route_question",
            "route_question",
            lambda _q, _c: RouteDraft(ref="c1", role="지어낸 역할"),
        ),
        ("tacit_follow_up", "tacit_follow_up", lambda _q, _a, _c: "더 말씀해 주세요."),
        ("write_artifact", "write_artifact", lambda _k, _c: []),
    ],
)
def test_check_failure_names_step(
    capsys: pytest.CaptureFixture[str], step: str, method: str, result: Any
) -> None:
    llm = _stub(method, result)
    assert live_check_honesty.run(llm) == 1
    assert _last_line(capsys.readouterr().out) == f"LIVE HONESTY FAIL {step}: CheckFailed"


def test_premortem_without_response_line_fails(capsys: pytest.CaptureFixture[str]) -> None:
    section = ArtifactSection(heading="1. x", body="징후: 알림", card_ids=["c2"])
    llm = _stub("write_artifact", lambda _k, _c: [section])
    assert live_check_honesty.run(llm) == 1
    assert _last_line(capsys.readouterr().out) == "LIVE HONESTY FAIL write_artifact: CheckFailed"


def test_http_error_reports_step_and_class_only(capsys: pytest.CaptureFixture[str]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    assert live_check_honesty.run(_llm(handler)) == 1
    out = capsys.readouterr().out
    assert _last_line(out) == "LIVE HONESTY FAIL explain_fence: ProviderError"
    assert KEY not in out
    assert "출고" not in out


def test_error_mid_run_names_that_step(capsys: pytest.CaptureFixture[str]) -> None:
    ok = _dispatch(REALISTIC, [])

    def handler(request: httpx.Request) -> httpx.Response:
        system = json.loads(request.content)["messages"][0]["content"]
        if system == TACIT_SYSTEM:
            return httpx.Response(401)
        return ok(request)

    assert live_check_honesty.run(_llm(handler)) == 1
    assert _last_line(capsys.readouterr().out) == "LIVE HONESTY FAIL tacit_follow_up: ProviderError"


def test_main_skips_for_fake(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HANDOFF_PROVIDER", "fake")
    assert live_check_honesty.main() == 0
    assert _last_line(capsys.readouterr().out).startswith("LIVE SKIP: ")


def test_main_skips_without_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HANDOFF_PROVIDER", "qwen")
    monkeypatch.delenv("HANDOFF_LLM_API_KEY", raising=False)
    assert live_check_honesty.main() == 0
    assert _last_line(capsys.readouterr().out).startswith("LIVE SKIP: ")


def test_main_qwen_runs_without_leaking_key(
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

    monkeypatch.setattr(live_check_honesty, "QwenLLM", factory)
    assert live_check_honesty.main() == 0
    out = capsys.readouterr().out
    assert _last_line(out).startswith("LIVE HONESTY OK ")
    assert seen == SYSTEMS
    assert set(auth) == {f"Bearer {KEY}"}
    assert KEY not in out
    assert "messages" not in out
    assert "출고" not in out.replace("물류운영팀 리드", "")
