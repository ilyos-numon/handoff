import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from handoff import providers_qwen
from handoff.config import load_settings
from handoff.kb import KBEntry
from handoff.providers import FakeEmbedder, FakeLLM, ProviderError, build_providers, fake_summary
from handoff.providers_qwen import QwenLLM
from handoff.providers_replay import (
    Cassette,
    CassetteMiss,
    DailyBudget,
    RecordingQwenLLM,
    ReplayLLM,
    denormalize,
    normalize,
)
from handoff.question_tree import FollowUp

KEY = "sk-test-secret-key"
BASE = "https://llm.example.test/v1"
MODEL = "qwen-test"
SID_A = "0123456789abcdef0123456789abcdef"
SID_B = "fedcba9876543210fedcba9876543210"

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers_qwen, "_sleep", lambda _s: None)


def _chat_response(content: str) -> httpx.Response:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    return httpx.Response(200, json={"choices": [{"message": message}]})


def _counting(content: str) -> tuple[Handler, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _chat_response(content)

    return handler, seen


def _entry(session_id: str, answer: str) -> KBEntry:
    return KBEntry(
        session_id=session_id,
        question_id="sys-01",
        axis="systems",
        question="질문",
        answer=answer,
        created_at=datetime(2026, 10, 23, tzinfo=UTC),
    )


def _budget(
    tmp_path: Path, cap: int, today: Callable[[], str] = lambda: "2026-10-23"
) -> DailyBudget:
    return DailyBudget(tmp_path / "budget.json", cap, today)


def _record_summary(tmp_path: Path, content: str, entries: list[KBEntry]) -> Path:
    path = tmp_path / "cassette.json"
    handler, _ = _counting(content)
    rec = RecordingQwenLLM(
        Cassette(path), BASE, MODEL, KEY, 5.0, transport=httpx.MockTransport(handler)
    )
    assert rec.summarize(entries)[0] == content.splitlines()[0]
    return path


# --- normalize / denormalize ---


def test_normalize_card_ids_in_first_seen_order() -> None:
    ids: dict[str, str] = {}
    assert normalize("c12 그리고 c3, 다시 c12", ids) == "⟨c1⟩ 그리고 ⟨c2⟩, 다시 ⟨c1⟩"
    assert ids == {"c12": "⟨c1⟩", "c3": "⟨c2⟩"}


def test_normalize_session_ids_and_cards_share_dict() -> None:
    ids: dict[str, str] = {}
    out = normalize(f"세션 {SID_A} 카드 c7 세션 {SID_B}", ids)
    assert out == "세션 ⟨s1⟩ 카드 ⟨c1⟩ 세션 ⟨s2⟩"


def test_normalize_roundtrip_with_reordered_card_ids() -> None:
    first: dict[str, str] = {}
    second: dict[str, str] = {}
    assert normalize("근거 c5, c2", first) == normalize("근거 c9, c7", second)
    recorded = normalize("c2 가 맞습니다", first)
    assert recorded == "⟨c2⟩ 가 맞습니다"
    assert denormalize(recorded, second) == "c7 가 맞습니다"


def test_denormalize_session_id_and_unknown_placeholder_kept() -> None:
    ids: dict[str, str] = {}
    normalize(SID_B, ids)
    assert denormalize("⟨s1⟩ / ⟨c4⟩", ids) == f"{SID_B} / ⟨c4⟩"


def test_normalize_ids_followed_by_korean_particles() -> None:
    ids: dict[str, str] = {}
    out = normalize(f"c12의 근거와 c45는 세션 {SID_A}에서 나왔다", ids)
    assert out == "⟨c1⟩의 근거와 ⟨c2⟩는 세션 ⟨s1⟩에서 나왔다"
    assert denormalize(out, ids) == f"c12의 근거와 c45는 세션 {SID_A}에서 나왔다"
    other: dict[str, str] = {}
    assert normalize(f"c3의 근거와 c8는 세션 {SID_B}에서 나왔다", other) == out
    assert denormalize(out, other) == f"c3의 근거와 c8는 세션 {SID_B}에서 나왔다"


def test_normalize_leaves_ids_inside_ascii_words() -> None:
    ids: dict[str, str] = {}
    text = f"abc12 c3x c4_ x{SID_A} {SID_A}z"
    assert normalize(text, ids) == text
    assert ids == {}


# --- cassette ---


def test_cassette_missing_file_is_empty_and_save_format(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "cassette.json"
    cassette = Cassette(path)
    assert len(cassette) == 0
    key, _ = cassette.key(MODEL, "sys", "user", 10, False)
    cassette.put(key, "응답")
    cassette.save()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"version": 1, "entries": {key: "응답"}}
    assert "응답" in path.read_text(encoding="utf-8")
    assert len(Cassette(path)) == 1


def test_cassette_key_ignores_id_values_but_not_params(tmp_path: Path) -> None:
    cassette = Cassette(tmp_path / "c.json")
    k1, ids1 = cassette.key(MODEL, "sys", f"{SID_A} c1", 10, False)
    k2, _ = cassette.key(MODEL, "sys", f"{SID_B} c8", 10, False)
    k3, _ = cassette.key(MODEL, "sys", f"{SID_B} c8", 11, False)
    k4, _ = cassette.key(MODEL, "sys", f"{SID_B} c8", 10, True)
    assert k1 == k2
    assert len({k1, k3, k4}) == 3
    assert ids1 == {SID_A: "⟨s1⟩", "c1": "⟨c1⟩"}


# --- record -> replay ---


def test_record_then_replay_hit_denormalizes_ids(tmp_path: Path) -> None:
    path = _record_summary(tmp_path, f"세션 {SID_A} 정리\n둘\n셋", [_entry("s", f"{SID_A} 답")])
    live_handler, live_seen = _counting("live")
    replay = ReplayLLM(
        Cassette(path),
        MODEL,
        FakeLLM(),
        QwenLLM(BASE, MODEL, KEY, 5.0, transport=httpx.MockTransport(live_handler)),
        _budget(tmp_path, 5),
    )
    lines = replay.summarize([_entry("s", f"{SID_B} 답")])
    assert lines == [f"세션 {SID_B} 정리", "둘", "셋"]
    assert live_seen == []
    stats = replay.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 0
    assert stats["fake_fallbacks"] == 0


def test_miss_without_live_uses_fake(tmp_path: Path) -> None:
    replay = ReplayLLM(
        Cassette(tmp_path / "none.json"), MODEL, FakeLLM(), None, _budget(tmp_path, 5)
    )
    entries = [_entry("s", "배포는 금요일에 하지 않습니다")]
    assert replay.summarize(entries) == fake_summary(entries)
    assert replay.stats() == {
        "hits": 0,
        "misses": 1,
        "live_calls_today": 0,
        "live_cap": 5,
        "fake_fallbacks": 1,
    }


def test_miss_with_live_within_cap_calls_live_then_fake_over_cap(tmp_path: Path) -> None:
    handler, seen = _counting("실시간 한 줄\n둘\n셋")
    live = QwenLLM(BASE, MODEL, KEY, 5.0, transport=httpx.MockTransport(handler))
    replay = ReplayLLM(
        Cassette(tmp_path / "none.json"), MODEL, FakeLLM(), live, _budget(tmp_path, 1)
    )
    entries = [_entry("s", "답변")]
    assert replay.summarize(entries) == ["실시간 한 줄", "둘", "셋"]
    assert len(seen) == 1
    assert replay.summarize(entries) == fake_summary(entries)
    assert len(seen) == 1
    stats = replay.stats()
    assert stats["misses"] == 2
    assert stats["live_calls_today"] == 1
    assert stats["live_cap"] == 1
    assert stats["fake_fallbacks"] == 1


def test_zero_cap_never_calls_live(tmp_path: Path) -> None:
    handler, seen = _counting("x")
    live = QwenLLM(BASE, MODEL, KEY, 5.0, transport=httpx.MockTransport(handler))
    replay = ReplayLLM(
        Cassette(tmp_path / "none.json"), MODEL, FakeLLM(), live, _budget(tmp_path, 0)
    )
    cand = [FollowUp(id="sys-01-f1", text="복구는 어떻게 했나요?")]
    assert replay.follow_up("q", "a", cand) == cand[0]
    assert seen == []
    assert replay.stats()["fake_fallbacks"] == 1


# --- budget ---


def test_budget_resets_when_date_changes(tmp_path: Path) -> None:
    day = ["2026-10-23"]
    budget = _budget(tmp_path, 2, lambda: day[0])
    assert budget.try_spend() and budget.try_spend()
    assert not budget.try_spend()
    assert budget.used_today() == 2
    day[0] = "2026-10-24"
    assert budget.used_today() == 0
    assert budget.try_spend()
    assert budget.used_today() == 1


def test_budget_persists_across_instances(tmp_path: Path) -> None:
    assert _budget(tmp_path, 1).try_spend()
    assert not _budget(tmp_path, 1).try_spend()
    assert _budget(tmp_path, 3).used_today() == 1


# --- CassetteMiss must not be swallowed ---


def test_cassette_miss_is_not_provider_error() -> None:
    assert not issubclass(CassetteMiss, ProviderError)
    assert not issubclass(CassetteMiss, ValueError)


def test_cassette_miss_escapes_summarize_fallback() -> None:
    class Missing(QwenLLM):
        def _chat(self, system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
            raise CassetteMiss("none")

    llm = Missing(BASE, MODEL, KEY, 5.0, transport=httpx.MockTransport(_counting("x")[0]))
    with pytest.raises(CassetteMiss):
        llm.summarize([_entry("s", "답변")])


# --- recording ---


def test_recording_saves_normalized_response(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    handler, seen = _counting(f"c4 와 {SID_A}")
    rec = RecordingQwenLLM(
        Cassette(path), BASE, MODEL, KEY, 5.0, transport=httpx.MockTransport(handler)
    )
    out = rec._chat("sys", f"카드 c4 세션 {SID_A}", 50)
    assert out == f"c4 와 {SID_A}"
    assert len(seen) == 1
    entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
    assert list(entries.values()) == ["⟨c1⟩ 와 ⟨s1⟩"]
    assert KEY not in path.read_text(encoding="utf-8")


# --- build_providers ---


def test_build_providers_replay_without_key(tmp_path: Path) -> None:
    settings = load_settings(
        {
            "HANDOFF_PROVIDER": "replay",
            "HANDOFF_DATA_DIR": str(tmp_path),
            "HANDOFF_DEMO_CASSETTE": str(tmp_path / "c.json"),
            "HANDOFF_DEMO_LLM_DAILY_CAP": "7",
        }
    )
    embedder, llm = build_providers(settings)
    assert isinstance(embedder, FakeEmbedder)
    assert isinstance(llm, ReplayLLM)
    assert llm.stats()["live_cap"] == 7
    assert llm._live is None


def test_build_providers_replay_with_key_has_live(tmp_path: Path) -> None:
    settings = load_settings(
        {
            "HANDOFF_PROVIDER": "replay",
            "HANDOFF_LLM_API_KEY": KEY,
            "HANDOFF_DATA_DIR": str(tmp_path),
            "HANDOFF_DEMO_CASSETTE": str(tmp_path / "c.json"),
        }
    )
    _, llm = build_providers(settings)
    assert isinstance(llm, ReplayLLM)
    assert isinstance(llm._live, QwenLLM)


def test_build_providers_record(tmp_path: Path) -> None:
    settings = load_settings(
        {
            "HANDOFF_PROVIDER": "record",
            "HANDOFF_LLM_API_KEY": KEY,
            "HANDOFF_DEMO_CASSETTE": str(tmp_path / "c.json"),
        }
    )
    embedder, llm = build_providers(settings)
    assert isinstance(embedder, FakeEmbedder)
    assert isinstance(llm, RecordingQwenLLM)
    assert llm.cassette.path == tmp_path / "c.json"
