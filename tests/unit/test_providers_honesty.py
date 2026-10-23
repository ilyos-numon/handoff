"""S2: LLM protocol methods for honesty features delegate to llm_tasks modules."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from handoff.contracts import Evidence, KnowledgeCard, NewCard, RouteCandidate
from handoff.llm_tasks import expiry, fence, routing, tacit
from handoff.providers import LLM, FakeLLM
from handoff.providers_qwen import QwenLLM

NOW = datetime(2026, 10, 23, tzinfo=UTC)
EVIDENCE = Evidence(session_id="s1", session_number=1, question_id="sys-01", excerpt="근거")
NEW_CARD = NewCard(
    leaver="lee",
    role="dev",
    session_id="s1",
    type="decision",
    title="배치 서버",
    body="장애 때문에 이렇게 합니다.",
    entities=[],
    evidence=[EVIDENCE],
    confidence=0.8,
)
CARD = KnowledgeCard(id="c1", created_at=NOW, **NEW_CARD.model_dump())
CANDIDATE = RouteCandidate(ref="c1", basis="contact_card", text="결제팀 리드", roles=["결제팀 리드"])


def _qwen() -> QwenLLM:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no network call expected")

    return QwenLLM("https://llm.example.test/v1", "m", "k", 1.0, httpx.MockTransport(handler))


def test_fake_and_qwen_satisfy_protocol() -> None:
    llms: list[LLM] = [FakeLLM(), _qwen()]
    for llm in llms:
        for name in ("tacit_follow_up", "annotate_invalidation", "route_question", "explain_fence"):
            assert callable(getattr(llm, name))


FAKE_CALLS: list[tuple[str, Callable[[FakeLLM], Any]]] = [
    ("tacit", lambda llm: llm.tacit_follow_up("질문", "그냥 합니다", ["그냥"])),
    ("expiry", lambda llm: llm.annotate_invalidation([NEW_CARD])),
    ("routing", lambda llm: llm.route_question("누구에게?", [CANDIDATE])),
    ("fence", lambda llm: llm.explain_fence("배치 서버", [CARD])),
]


@pytest.mark.parametrize(("module", "call"), FAKE_CALLS)
def test_fake_llm_delegates_to_stub(module: str, call: Callable[[FakeLLM], Any]) -> None:
    with pytest.raises(NotImplementedError, match=f"^{module}: implemented in stage S"):
        call(FakeLLM())


@pytest.mark.parametrize(
    ("module", "fn", "args", "call"),
    [
        (
            tacit,
            "qwen_tacit_follow_up",
            ("질문", "그냥 합니다", ["그냥"]),
            lambda llm: llm.tacit_follow_up("질문", "그냥 합니다", ["그냥"]),
        ),
        (
            expiry,
            "qwen_annotate_invalidation",
            ([NEW_CARD],),
            lambda llm: llm.annotate_invalidation([NEW_CARD]),
        ),
        (
            routing,
            "qwen_route_question",
            ("누구에게?", [CANDIDATE]),
            lambda llm: llm.route_question("누구에게?", [CANDIDATE]),
        ),
        (
            fence,
            "qwen_explain_fence",
            ("배치 서버", [CARD]),
            lambda llm: llm.explain_fence("배치 서버", [CARD]),
        ),
    ],
)
def test_qwen_llm_passes_chat_and_args(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    fn: str,
    args: tuple[Any, ...],
    call: Callable[[QwenLLM], Any],
) -> None:
    seen: list[tuple[Any, ...]] = []
    sentinel = object()

    def recorder(*a: Any) -> object:
        seen.append(a)
        return sentinel

    monkeypatch.setattr(module, fn, recorder)
    llm = _qwen()
    assert call(llm) is sentinel
    assert len(seen) == 1
    assert seen[0][0] == llm._chat
    assert seen[0][1:] == args


@pytest.mark.parametrize(
    ("call", "stage"),
    [
        (lambda: tacit.fake_tacit_follow_up("q", "a", ["그냥"]), "S4"),
        (lambda: tacit.qwen_tacit_follow_up(_qwen()._chat, "q", "a", ["그냥"]), "S4"),
        (lambda: expiry.fake_annotate_invalidation([NEW_CARD]), "S5"),
        (lambda: expiry.qwen_annotate_invalidation(_qwen()._chat, [NEW_CARD]), "S5"),
        (lambda: routing.fake_route_question("q", [CANDIDATE]), "S7"),
        (lambda: routing.qwen_route_question(_qwen()._chat, "q", [CANDIDATE]), "S7"),
        (lambda: fence.fake_explain_fence("s", [CARD]), "S8"),
        (lambda: fence.qwen_explain_fence(_qwen()._chat, "s", [CARD]), "S8"),
    ],
)
def test_stubs_name_owner_stage(call: Callable[[], Any], stage: str) -> None:
    with pytest.raises(NotImplementedError, match=f"implemented in stage {stage}$"):
        call()
