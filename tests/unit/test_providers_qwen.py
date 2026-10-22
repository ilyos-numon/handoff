import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from handoff import live_check, providers_qwen
from handoff.config import Settings
from handoff.kb import Citation, KBEntry
from handoff.providers import ProviderError, build_providers, fake_summary
from handoff.providers_qwen import QwenEmbedder, QwenLLM
from handoff.question_tree import Axis, FollowUp

KEY = "sk-test-secret-key"
BASE = "https://llm.example.test/v1"
CANDIDATES = [
    FollowUp(id="sys-01-f1", text="그 부분에서 문제가 났을 때 어떻게 복구했나요?"),
    FollowUp(id="sys-01-f2", text="꼭 확인하는 설정값이 있나요?"),
]

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waits: list[float] = []
    monkeypatch.setattr(providers_qwen, "_sleep", waits.append)
    return waits


def _chat_response(content: str, reasoning: str | None = None) -> httpx.Response:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return httpx.Response(200, json={"choices": [{"message": message}]})


def _llm(handler: Handler) -> QwenLLM:
    return QwenLLM(BASE, "qwen-test", KEY, 5.0, transport=httpx.MockTransport(handler))


def _recording(responses: list[httpx.Response]) -> tuple[Handler, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses[len(seen) - 1]

    return handler, seen


def _entry(question_id: str, axis: Axis, answer: str) -> KBEntry:
    return KBEntry(
        session_id="s1",
        question_id=question_id,
        axis=axis,
        question="질문",
        answer=answer,
        created_at=datetime(2026, 10, 23, tzinfo=UTC),
    )


def _citation() -> Citation:
    return Citation(
        session_id="s1", question_id="sys-01", axis="systems", excerpt="금요일 배포는 피합니다.", score=0.9
    )


def test_chat_request_body_and_headers() -> None:
    handler, seen = _recording([_chat_response('{"pick": 1, "question": ""}')])
    result = _llm(handler).follow_up("주 질문", "답변", CANDIDATES)
    assert result == CANDIDATES[1]
    req = seen[0]
    assert req.url.path == "/v1/chat/completions"
    assert req.headers["Authorization"] == f"Bearer {KEY}"
    assert req.headers["Content-Type"].startswith("application/json")
    body = json.loads(req.content)
    assert body["enable_thinking"] is False
    assert body["model"] == "qwen-test"
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 200
    assert body["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_follow_up_new_question_gets_g1_id() -> None:
    handler, _ = _recording(
        [_chat_response('{"pick": null, "question": "  새벽 배치 타임아웃은 어떻게 감지하나요?  "}')]
    )
    result = _llm(handler).follow_up("주 질문", "답변", CANDIDATES)
    assert result == FollowUp(id="sys-01-g1", text="새벽 배치 타임아웃은 어떻게 감지하나요?")


@pytest.mark.parametrize(
    "content",
    ["not json", '{"pick": 7, "question": ""}', '{"pick": null, "question": "짧음"}', "[1]"],
)
def test_follow_up_falls_back_to_first_candidate(content: str) -> None:
    handler, _ = _recording([_chat_response(content)])
    assert _llm(handler).follow_up("주 질문", "답변", CANDIDATES) == CANDIDATES[0]


def test_follow_up_provider_error_falls_back() -> None:
    handler, _ = _recording([httpx.Response(400, json={})])
    assert _llm(handler).follow_up("주 질문", "답변", CANDIDATES) == CANDIDATES[0]


def test_follow_up_empty_candidates_makes_no_call() -> None:
    handler, seen = _recording([])
    assert _llm(handler).follow_up("주 질문", "답변", []) is None
    assert seen == []


def test_compose_returns_text_and_body() -> None:
    handler, seen = _recording([_chat_response("  금요일 배포는 피합니다.  ")])
    llm = _llm(handler)
    assert llm.compose_answer("왜요?", [_citation()]) == "금요일 배포는 피합니다."
    body = json.loads(seen[0].content)
    assert body["max_tokens"] == 300
    assert body["enable_thinking"] is False
    assert "response_format" not in body
    assert "[근거 1] (담당 시스템) 금요일 배포는 피합니다." in body["messages"][1]["content"]
    assert "NONE" in body["messages"][0]["content"]
    assert llm.last_reasoning_chars == 0


@pytest.mark.parametrize("content", ["NONE", " NONE.", "", "   "])
def test_compose_none_returns_none(content: str) -> None:
    handler, _ = _recording([_chat_response(content)])
    assert _llm(handler).compose_answer("PG사?", [_citation()]) is None


@pytest.mark.parametrize(
    "content",
    [
        "제공된 근거에는 해당 정보가 없습니다. 따라서 질문에 답할 수 없습니다. NONE",
        "근거가 부족합니다.\nNONE",
        "제공된 근거 자료에는 사무실 주차 등록 절차에 대한 정보가 포함되어 있지 않습니다.",
        "휴가 신청 절차는 근거에서 확인할 수 없습니다.",
        "해당 질문에 답변할 수 없습니다.",
    ],
)
def test_compose_trailing_none_and_refusals_return_none(content: str) -> None:
    handler, _ = _recording([_chat_response(content)])
    assert _llm(handler).compose_answer("주차?", [_citation()]) is None


def test_compose_grounded_answer_with_none_like_words_kept() -> None:
    text = "결제 승인 모듈 배포가 가장 위험합니다. NONEXISTENT 설정은 건드리지 않습니다."
    handler, _ = _recording([_chat_response(text)])
    assert _llm(handler).compose_answer("무서운 배포?", [_citation()]) == text


def test_compose_provider_error_propagates() -> None:
    handler, _ = _recording([httpx.Response(401, json={})])
    with pytest.raises(ProviderError):
        _llm(handler).compose_answer("왜요?", [_citation()])


def test_last_reasoning_chars_recorded() -> None:
    handler, _ = _recording([_chat_response("답", reasoning="생각" * 5)])
    llm = _llm(handler)
    llm.compose_answer("왜요?", [_citation()])
    assert llm.last_reasoning_chars == 10


def test_summarize_parses_three_lines() -> None:
    content = "1. 첫째 줄\n\n- 둘째 줄\n• 셋째 " + "가" * 80 + "\n2) 넷째 줄"
    handler, seen = _recording([_chat_response(content)])
    entries = [_entry("sys-01", "systems", "a1")]
    result = _llm(handler).summarize(entries)
    assert result == ["첫째 줄", "둘째 줄", ("셋째 " + "가" * 80)[:60]]
    assert json.loads(seen[0].content)["max_tokens"] == 300


def test_summarize_pads_with_fake_summary() -> None:
    entries = [
        _entry("sys-01", "systems", "a1"),
        _entry("rou-01", "routines", "a2"),
        _entry("inc-01", "incidents", "a3"),
    ]
    handler, _ = _recording([_chat_response("* 한 줄만\n")])
    result = _llm(handler).summarize(entries)
    assert result == ["한 줄만", *fake_summary(entries)[1:]]


def test_summarize_fallbacks() -> None:
    entries = [_entry("sys-01", "systems", "a1")]
    handler, _ = _recording([httpx.Response(500, json={}), httpx.Response(503, json={})])
    assert _llm(handler).summarize(entries) == fake_summary(entries)
    handler, _ = _recording([_chat_response("\n  \n")])
    assert _llm(handler).summarize(entries) == fake_summary(entries)
    handler, seen = _recording([])
    assert len(_llm(handler).summarize([])) == 3
    assert seen == []


def test_retry_after_429_succeeds(_no_wait: list[float]) -> None:
    handler, seen = _recording([httpx.Response(429, json={}), _chat_response("답변")])
    assert _llm(handler).compose_answer("왜요?", [_citation()]) == "답변"
    assert len(seen) == 2
    assert _no_wait == [1.0]


def test_timeout_retried_then_fails(_no_wait: list[float]) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ProviderError, match=r"^llm request failed: status=timeout$"):
        _llm(handler).compose_answer("왜요?", [_citation()])
    assert len(calls) == 2
    assert _no_wait == [1.0]


def test_400_fails_immediately_without_key(_no_wait: list[float]) -> None:
    handler, seen = _recording([httpx.Response(400, json={"error": "bad"})])
    with pytest.raises(ProviderError) as info:
        _llm(handler).compose_answer("왜요?", [_citation()])
    assert str(info.value) == "llm request failed: status=400"
    assert KEY not in str(info.value)
    assert KEY not in repr(info.value)
    assert len(seen) == 1
    assert _no_wait == []


def _embed_handler(dim: int, seen: list[dict[str, Any]], bad_dim: bool = False) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        offset = sum(len(b["input"]) for b in seen[:-1])
        data = [
            {"index": i, "embedding": [float(offset + i)] * (dim - 1 if bad_dim else dim)}
            for i in range(len(body["input"]))
        ]
        return httpx.Response(200, json={"data": list(reversed(data))})

    return handler


def test_embed_batches_and_sorts() -> None:
    seen: list[dict[str, Any]] = []
    embedder = QwenEmbedder(
        BASE, "emb-test", KEY, 4, 5.0, transport=httpx.MockTransport(_embed_handler(4, seen))
    )
    texts = [f"t{i}" for i in range(25)]
    vectors = embedder.embed(texts)
    assert [len(b["input"]) for b in seen] == [10, 10, 5]
    assert seen[0]["dimensions"] == 4
    assert seen[0]["encoding_format"] == "float"
    assert seen[0]["model"] == "emb-test"
    assert [b["input"] for b in seen] == [texts[:10], texts[10:20], texts[20:]]
    assert [v[0] for v in vectors] == [float(i) for i in range(25)]
    assert all(len(v) == 4 for v in vectors)
    assert embedder.dim == 4


def test_embed_dimension_mismatch_raises() -> None:
    seen: list[dict[str, Any]] = []
    embedder = QwenEmbedder(
        BASE, "emb-test", KEY, 4, 5.0, transport=httpx.MockTransport(_embed_handler(4, seen, True))
    )
    with pytest.raises(ProviderError):
        embedder.embed(["a", "b"])


def test_embed_error_message_format() -> None:
    handler, _ = _recording([httpx.Response(403, json={})])
    embedder = QwenEmbedder(BASE, "emb", KEY, 4, 5.0, transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match=r"^embed request failed: status=403$"):
        embedder.embed(["a"])


def test_build_providers_qwen(tmp_path: Path) -> None:
    settings = Settings(
        provider="qwen",
        data_dir=tmp_path,
        min_score=0.4,
        collection="handoff_kb",
        llm_api_key=KEY,
        embed_dim=512,
    )
    embedder, llm = build_providers(settings)
    assert isinstance(embedder, QwenEmbedder)
    assert isinstance(llm, QwenLLM)
    assert embedder.dim == 512
    assert llm.last_reasoning_chars == 0


def test_live_check_skips_for_fake(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HANDOFF_PROVIDER", "fake")
    assert live_check.main() == 0
    out = capsys.readouterr().out
    assert out.startswith("LIVE SKIP")


def test_live_check_skips_without_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HANDOFF_PROVIDER", "qwen")
    monkeypatch.delenv("HANDOFF_LLM_API_KEY", raising=False)
    assert live_check.main() == 0
    assert capsys.readouterr().out.startswith("LIVE SKIP")
