import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from handoff.depth import DepthService
from handoff.knowledge_store import KnowledgeStore
from handoff.llm_tasks.tacit import (
    TACIT_SYSTEM,
    fake_tacit_follow_up,
    qwen_tacit_follow_up,
)
from handoff.providers import FakeLLM, ProviderError
from handoff.routes_depth import router
from handoff.signals import TACIT_FOLLOW_UP_TEMPLATE
from handoff.store import SessionRow, SessionStore

T0 = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
TACIT_ANSWER = "물 주는 시점은 그냥 감으로 정합니다."


def _chat_returning(*contents: str) -> tuple[list[tuple[str, str, int, bool]], object]:
    calls: list[tuple[str, str, int, bool]] = []
    queue = list(contents)

    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        calls.append((system, user, max_tokens, json_mode))
        return queue.pop(0)

    return calls, chat


class FailingTacitLLM(FakeLLM):
    def tacit_follow_up(self, question: str, answer: str, cues: list[str]) -> str:
        raise ProviderError("llm request failed: status=503")


class BlankTacitLLM(FakeLLM):
    def tacit_follow_up(self, question: str, answer: str, cues: list[str]) -> str:
        return "  "


class RecordingTacitLLM(FakeLLM):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, list[str]]] = []

    def tacit_follow_up(self, question: str, answer: str, cues: list[str]) -> str:
        self.calls.append((question, answer, cues))
        return "  어떤 신호를 기준으로 정하나요?  "


@pytest.fixture
def sessions() -> SessionStore:
    store = SessionStore(":memory:")
    store.insert_session(
        SessionRow(
            session_id="s1", leaver="alice", role="backend_developer", number=1,
            started_at=T0, deadline=T0 + timedelta(minutes=30), ended_at=None,
            node_index=0, current_json=None, summary=[],
        )
    )
    return store


def _service(sessions: SessionStore, llm: object = None) -> DepthService:
    ks = KnowledgeStore.from_session_store(sessions)
    return DepthService(llm or FakeLLM(), ks, sessions, 1, clock=lambda: T0)  # type: ignore[arg-type]


# ---- llm_tasks fake


def test_fake_uses_template_with_first_cue() -> None:
    text = fake_tacit_follow_up("q", TACIT_ANSWER, ["그냥", "감으로"])
    assert text == TACIT_FOLLOW_UP_TEMPLATE.format(cue="그냥")
    assert "'그냥'" in text


# ---- llm_tasks qwen (fake chat function only)


def test_qwen_parses_on_target_question() -> None:
    calls, chat = _chat_returning(json.dumps({"question": " 어떤 기준으로 정하나요? "}))
    text = qwen_tacit_follow_up(chat, "언제 물을 주나요?", TACIT_ANSWER, ["그냥", "감으로"])  # type: ignore[arg-type]
    assert text == "어떤 기준으로 정하나요?"
    [(system, user, max_tokens, json_mode)] = calls
    assert system == TACIT_SYSTEM
    assert "언제 물을 주나요?" in user and TACIT_ANSWER in user
    assert "'그냥'" in user and "'감으로'" in user
    assert json_mode is True and max_tokens > 0


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[1, 2]",
        json.dumps({"q": "기준은?"}),
        json.dumps({"question": ""}),
        json.dumps({"question": 3}),
        json.dumps({"question": "기준" + "가" * 200}),
    ],
)
def test_qwen_parse_failure_uses_template(content: str) -> None:
    _, chat = _chat_returning(content)
    text = qwen_tacit_follow_up(chat, "q", TACIT_ANSWER, ["감으로"])  # type: ignore[arg-type]
    assert text == TACIT_FOLLOW_UP_TEMPLATE.format(cue="감으로")


def test_qwen_off_target_question_uses_template() -> None:
    _, chat = _chat_returning(json.dumps({"question": "언제 하나요?"}))
    text = qwen_tacit_follow_up(chat, "q", TACIT_ANSWER, ["그냥"])  # type: ignore[arg-type]
    assert text == TACIT_FOLLOW_UP_TEMPLATE.format(cue="그냥")


def test_qwen_propagates_provider_error() -> None:
    def chat(system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
        raise ProviderError("llm request failed: status=503")

    with pytest.raises(ProviderError):
        qwen_tacit_follow_up(chat, "q", TACIT_ANSWER, ["그냥"])


def test_qwen_prompt_rules() -> None:
    assert "지어내지 않" in TACIT_SYSTEM
    assert "역할명" in TACIT_SYSTEM
    assert "JSON 만 출력" in TACIT_SYSTEM
    for cue in ("기준", "판단", "신호", "근거"):
        assert cue in TACIT_SYSTEM


# ---- DepthService.tacit


def test_tacit_no_cues_returns_nones(sessions: SessionStore) -> None:
    resp = _service(sessions).tacit("q", "매주 화요일에 화면에서 확인합니다.")
    assert resp.cues == []
    assert resp.text is None


def test_tacit_with_cues_uses_llm_text(sessions: SessionStore) -> None:
    llm = RecordingTacitLLM()
    resp = _service(sessions, llm).tacit("언제 물을 주나요?", TACIT_ANSWER)
    assert resp.cues == ["그냥", "감으로"]
    assert resp.text == "어떤 신호를 기준으로 정하나요?"
    assert llm.calls == [("언제 물을 주나요?", TACIT_ANSWER, ["그냥", "감으로"])]


def test_tacit_fake_llm_gives_template(sessions: SessionStore) -> None:
    resp = _service(sessions).tacit("q", "경험상 이틀이면 됩니다.")
    assert resp.cues == ["경험상"]
    assert resp.text == TACIT_FOLLOW_UP_TEMPLATE.format(cue="경험상")


@pytest.mark.parametrize("llm", [FailingTacitLLM(), BlankTacitLLM()])
def test_tacit_falls_back_to_template(sessions: SessionStore, llm: FakeLLM) -> None:
    resp = _service(sessions, llm).tacit("q", TACIT_ANSWER)
    assert resp.cues == ["그냥", "감으로"]
    assert resp.text == TACIT_FOLLOW_UP_TEMPLATE.format(cue="그냥")


# ---- route


def _client(service: object) -> TestClient:
    app = FastAPI()
    app.state.knowledge = SimpleNamespace(depth=service)
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def client(sessions: SessionStore) -> Iterator[TestClient]:
    with _client(_service(sessions)) as c:
        yield c


def test_route_tacit_no_cues(client: TestClient) -> None:
    resp = client.post("/depth/tacit", json={"question": "q", "answer": "매주 확인합니다."})
    assert resp.status_code == 200
    assert resp.json() == {"cues": [], "text": None}


def test_route_tacit_with_cues(client: TestClient) -> None:
    resp = client.post("/depth/tacit", json={"question": "q", "answer": TACIT_ANSWER})
    assert resp.status_code == 200
    assert resp.json() == {
        "cues": ["그냥", "감으로"],
        "text": TACIT_FOLLOW_UP_TEMPLATE.format(cue="그냥"),
    }


def test_route_tacit_provider_error_still_200(sessions: SessionStore) -> None:
    with _client(_service(sessions, FailingTacitLLM())) as c:
        resp = c.post("/depth/tacit", json={"question": "q", "answer": TACIT_ANSWER})
    assert resp.status_code == 200
    assert resp.json()["text"] == TACIT_FOLLOW_UP_TEMPLATE.format(cue="그냥")


@pytest.mark.parametrize(
    "body",
    [
        {"question": "q", "answer": "   "},
        {"question": "q", "answer": ""},
        {"question": " ", "answer": TACIT_ANSWER},
        {"question": "q"},
    ],
)
def test_route_tacit_blank_422(client: TestClient, body: dict[str, str]) -> None:
    assert client.post("/depth/tacit", json=body).status_code == 422
