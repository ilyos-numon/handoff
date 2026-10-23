from pathlib import Path

import pytest

from handoff.config import (
    DEFAULT_LLM_BASE_URL,
    SUPPORTED_PROVIDERS,
    Settings,
    collection_name,
    load_settings,
)


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


QWEN_KEY = "sk-test-secret-value-123"


def test_new_field_defaults() -> None:
    settings = load_settings({})
    assert settings.llm_base_url == DEFAULT_LLM_BASE_URL
    assert settings.llm_model == "qwen3.7-flash"
    assert settings.llm_api_key == ""
    assert settings.llm_timeout_s == 20.0
    assert settings.embed_model == "text-embedding-v4"
    assert settings.embed_dim == 1024
    assert settings.session_seconds == 900
    assert settings.session_grace_seconds == 30
    assert settings.sessions_planned == 10
    assert "qwen" in SUPPORTED_PROVIDERS


def test_qwen_without_key_raises_with_variable_name() -> None:
    with pytest.raises(
        ValueError, match="HANDOFF_LLM_API_KEY is required for HANDOFF_PROVIDER=qwen"
    ):
        load_settings({"HANDOFF_PROVIDER": "qwen"})


def test_qwen_empty_key_raises() -> None:
    with pytest.raises(ValueError, match="HANDOFF_LLM_API_KEY"):
        load_settings({"HANDOFF_PROVIDER": "qwen", "HANDOFF_LLM_API_KEY": ""})


def test_qwen_default_min_score() -> None:
    settings = load_settings({"HANDOFF_PROVIDER": "qwen", "HANDOFF_LLM_API_KEY": QWEN_KEY})
    assert settings.provider == "qwen"
    assert settings.min_score == 0.40
    assert settings.llm_api_key == QWEN_KEY


def test_explicit_min_score_wins_for_qwen() -> None:
    settings = load_settings(
        {
            "HANDOFF_PROVIDER": "qwen",
            "HANDOFF_LLM_API_KEY": QWEN_KEY,
            "HANDOFF_MIN_SCORE": "0.3",
        }
    )
    assert settings.min_score == 0.3


def test_overrides_llm_embed_and_session_values() -> None:
    settings = load_settings(
        {
            "HANDOFF_LLM_BASE_URL": "http://localhost:9999/v1",
            "HANDOFF_LLM_MODEL": "m1",
            "HANDOFF_LLM_TIMEOUT": "5.5",
            "HANDOFF_EMBED_MODEL": "e1",
            "HANDOFF_EMBED_DIM": "8",
            "HANDOFF_SESSION_SECONDS": "60",
            "HANDOFF_SESSION_GRACE_SECONDS": "0",
            "HANDOFF_SESSIONS_PLANNED": "3",
        }
    )
    assert settings.llm_base_url == "http://localhost:9999/v1"
    assert settings.llm_model == "m1"
    assert settings.llm_timeout_s == 5.5
    assert settings.embed_model == "e1"
    assert settings.embed_dim == 8
    assert settings.session_seconds == 60
    assert settings.session_grace_seconds == 0
    assert settings.sessions_planned == 3


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("HANDOFF_EMBED_DIM", "abc"),
        ("HANDOFF_EMBED_DIM", "0"),
        ("HANDOFF_SESSION_SECONDS", "x"),
        ("HANDOFF_SESSION_SECONDS", "0"),
        ("HANDOFF_SESSION_GRACE_SECONDS", "-1"),
        ("HANDOFF_SESSIONS_PLANNED", "0"),
        ("HANDOFF_LLM_TIMEOUT", "fast"),
        ("HANDOFF_MIN_SCORE", "high"),
    ],
)
def test_invalid_number_message_has_variable_name(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name):
        load_settings({name: value})


def test_error_messages_never_include_key() -> None:
    with pytest.raises(ValueError) as excinfo:
        load_settings(
            {
                "HANDOFF_PROVIDER": "qwen",
                "HANDOFF_LLM_API_KEY": QWEN_KEY,
                "HANDOFF_EMBED_DIM": "bad",
            }
        )
    assert QWEN_KEY not in str(excinfo.value)


def test_repr_hides_api_key() -> None:
    settings = load_settings({"HANDOFF_PROVIDER": "qwen", "HANDOFF_LLM_API_KEY": QWEN_KEY})
    assert QWEN_KEY not in repr(settings)
    assert QWEN_KEY not in str(settings)


def test_collection_name_fake_is_unchanged() -> None:
    assert collection_name(load_settings({})) == "handoff_kb"
    assert collection_name(load_settings({"HANDOFF_COLLECTION": "c1"})) == "c1"


def test_collection_name_qwen_includes_model_and_dim() -> None:
    settings = load_settings({"HANDOFF_PROVIDER": "qwen", "HANDOFF_LLM_API_KEY": QWEN_KEY})
    assert collection_name(settings) == "handoff_kb_text_embedding_v4_1024"
    custom = load_settings(
        {
            "HANDOFF_PROVIDER": "qwen",
            "HANDOFF_LLM_API_KEY": QWEN_KEY,
            "HANDOFF_EMBED_MODEL": "Text-Embedding.V3",
            "HANDOFF_EMBED_DIM": "512",
        }
    )
    assert collection_name(custom) == "handoff_kb_text_embedding_v3_512"


def test_loop_setting_defaults() -> None:
    settings = load_settings({})
    assert settings.depth_max_followups == 1
    assert settings.queue_per_session == 2
    assert settings.verify_claims is True


def test_loop_setting_overrides() -> None:
    settings = load_settings(
        {
            "HANDOFF_DEPTH_MAX_FOLLOWUPS": "0",
            "HANDOFF_QUEUE_PER_SESSION": "5",
            "HANDOFF_VERIFY_CLAIMS": "off",
        }
    )
    assert settings.depth_max_followups == 0
    assert settings.queue_per_session == 5
    assert settings.verify_claims is False
    assert load_settings({"HANDOFF_VERIFY_CLAIMS": "on"}).verify_claims is True


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("HANDOFF_DEPTH_MAX_FOLLOWUPS", "-1"),
        ("HANDOFF_DEPTH_MAX_FOLLOWUPS", "one"),
        ("HANDOFF_QUEUE_PER_SESSION", "-1"),
        ("HANDOFF_QUEUE_PER_SESSION", "1.5"),
    ],
)
def test_invalid_loop_integers_name_variable(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name):
        load_settings({name: value})


@pytest.mark.parametrize("value", ["yes", "true", "ON", "1", ""])
def test_verify_claims_rejects_non_on_off(value: str) -> None:
    with pytest.raises(ValueError, match="HANDOFF_VERIFY_CLAIMS must be on or off"):
        load_settings({"HANDOFF_VERIFY_CLAIMS": value})


def test_tacit_followups_default_and_override() -> None:
    assert load_settings({}).tacit_followups is True
    assert load_settings({"HANDOFF_TACIT_FOLLOWUPS": "off"}).tacit_followups is False
    assert load_settings({"HANDOFF_TACIT_FOLLOWUPS": "on"}).tacit_followups is True


@pytest.mark.parametrize("value", ["yes", "true", "OFF", "0", ""])
def test_tacit_followups_rejects_non_on_off(value: str) -> None:
    with pytest.raises(ValueError, match="HANDOFF_TACIT_FOLLOWUPS must be on or off"):
        load_settings({"HANDOFF_TACIT_FOLLOWUPS": value})
