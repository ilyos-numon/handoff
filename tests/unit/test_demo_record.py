"""Cassette record/verify driver with a fake Qwen chat (no network)."""

from pathlib import Path

import pytest

from handoff import providers_qwen
from handoff.demo import record
from handoff.providers_qwen import QwenLLM

KEY = "sk-record-test-secret"


def _fake_chat(
    self: QwenLLM, system: str, user: str, max_tokens: int, json_mode: bool = False
) -> str:
    if json_mode:
        return "{}"
    return "인수인계 기록에 따르면: 확인한 내용입니다."


@pytest.fixture(autouse=True)
def _fake_qwen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers_qwen, "_sleep", lambda _s: None)
    monkeypatch.setattr(QwenLLM, "_chat", _fake_chat)


def _env(**extra: str) -> dict[str, str]:
    env = {"HANDOFF_LLM_BASE_URL": "https://llm.example.test/v1", "HANDOFF_LLM_MODEL": "qwen-test"}
    env.update(extra)
    return env


def test_record_then_verify_has_no_misses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "cassette.json"
    assert record.main(["--out", str(out)], _env(HANDOFF_LLM_API_KEY=KEY)) == 0
    rec_last = capsys.readouterr().out.strip().splitlines()[-1]
    assert rec_last.startswith("RECORD entries=")
    entries = int(rec_last.split()[1].removeprefix("entries="))
    assert entries >= 1
    assert rec_last.endswith(f"path={out.resolve()}")
    assert KEY not in out.read_text(encoding="utf-8")

    assert record.main(["--out", str(out), "--verify"], _env(HANDOFF_LLM_API_KEY=KEY)) == 0
    verify_last = capsys.readouterr().out.strip().splitlines()[-1]
    parts = dict(p.split("=") for p in verify_last.removeprefix("REPLAY ").split())
    assert verify_last.startswith("REPLAY ")
    assert parts["misses"] == "0"
    assert parts["fake_fallbacks"] == "0"
    assert int(parts["hits"]) >= entries


def test_record_without_key_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "cassette.json"
    assert record.main(["--out", str(out)], _env(HANDOFF_LLM_API_KEY="  ")) == 2
    assert not out.exists()
    assert "RECORD" not in capsys.readouterr().out


def test_verify_with_empty_cassette_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "missing.json"
    assert record.main(["--out", str(out), "--verify"], _env()) == 1
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("REPLAY hits=0 misses=")
    assert int(last.split()[2].removeprefix("misses=")) > 0


def test_rerecord_replaces_stale_entries(tmp_path: Path) -> None:
    out = tmp_path / "cassette.json"
    out.write_text('{"version": 1, "entries": {"stale": "old"}}', encoding="utf-8")
    assert record.main(["--out", str(out)], _env(HANDOFF_LLM_API_KEY=KEY)) == 0
    assert "stale" not in out.read_text(encoding="utf-8")
