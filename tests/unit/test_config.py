from pathlib import Path

import pytest

from handoff.config import Settings, load_settings


def test_defaults_when_env_empty() -> None:
    settings = load_settings({})
    assert settings == Settings(
        provider="fake",
        data_dir=Path("./data"),
        min_score=0.25,
        collection="handoff_kb",
    )
    assert isinstance(settings.data_dir, Path)


def test_unknown_provider_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unsupported HANDOFF_PROVIDER: unknown"):
        load_settings({"HANDOFF_PROVIDER": "unknown"})


def test_min_score_parsed_as_float() -> None:
    settings = load_settings({"HANDOFF_MIN_SCORE": "0.5"})
    assert settings.min_score == 0.5
    assert isinstance(settings.min_score, float)


def test_overrides_data_dir_and_collection() -> None:
    settings = load_settings({"HANDOFF_DATA_DIR": "/tmp/x", "HANDOFF_COLLECTION": "c1"})
    assert settings.data_dir == Path("/tmp/x")
    assert settings.collection == "c1"


def test_none_uses_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HANDOFF_MIN_SCORE", "0.75")
    monkeypatch.delenv("HANDOFF_PROVIDER", raising=False)
    assert load_settings().min_score == 0.75


def test_settings_is_frozen() -> None:
    settings = load_settings({})
    with pytest.raises(AttributeError):
        settings.provider = "other"  # type: ignore[misc]
