from pathlib import Path

import pytest

from handoff.config import (
    DEFAULT_CASSETTE,
    DEFAULT_LLM_BASE_URL,
    DEFAULT_WEB_DIST,
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
        auth=True,
        web_dist=DEFAULT_WEB_DIST,
        api_docs=False,
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


def test_auth_settings_defaults() -> None:
    settings = load_settings({})
    assert settings.auth is True
    assert settings.auth_secret == ""
    assert settings.invite_ttl_hours == 72
    direct = Settings(provider="fake", data_dir=Path("./data"), min_score=0.25, collection="c")
    assert direct.auth is False
    assert direct.invite_ttl_hours == 72


def test_auth_settings_overrides() -> None:
    settings = load_settings(
        {
            "HANDOFF_AUTH": "off",
            "HANDOFF_AUTH_SECRET": "s3cr3t-value",
            "HANDOFF_INVITE_TTL_HOURS": "1",
        }
    )
    assert settings.auth is False
    assert settings.auth_secret == "s3cr3t-value"
    assert settings.invite_ttl_hours == 1
    assert load_settings({"HANDOFF_AUTH": "on"}).auth is True


@pytest.mark.parametrize("value", ["yes", "true", "OFF", "0", ""])
def test_auth_rejects_non_on_off(value: str) -> None:
    with pytest.raises(ValueError, match="HANDOFF_AUTH must be on or off"):
        load_settings({"HANDOFF_AUTH": value})


@pytest.mark.parametrize("value", ["0", "-1", "abc", "1.5"])
def test_invalid_invite_ttl_names_variable(value: str) -> None:
    with pytest.raises(ValueError, match="HANDOFF_INVITE_TTL_HOURS"):
        load_settings({"HANDOFF_INVITE_TTL_HOURS": value})


def test_repr_hides_auth_secret() -> None:
    settings = load_settings({"HANDOFF_AUTH_SECRET": "s3cr3t-value"})
    assert "s3cr3t-value" not in repr(settings)
    assert "s3cr3t-value" not in str(settings)


def test_supported_providers_include_replay_and_record() -> None:
    assert {"fake", "qwen", "replay", "record"} <= SUPPORTED_PROVIDERS


def test_demo_setting_defaults() -> None:
    settings = load_settings({})
    assert settings.demo is False
    assert settings.demo_ttl_hours == 24
    assert settings.demo_max_sandboxes == 200
    assert settings.demo_llm_daily_cap == 300
    assert settings.demo_max_body_bytes == 8192
    assert settings.demo_rate == "120/minute"
    assert settings.demo_start_rate == "10/hour"
    assert settings.demo_cassette == DEFAULT_CASSETTE
    assert DEFAULT_CASSETTE.parts[-2:] == ("demo", "cassette.json")
    assert settings.public_url == ""


def test_demo_setting_overrides() -> None:
    settings = load_settings(
        {
            "HANDOFF_DEMO": "on",
            "HANDOFF_DEMO_TTL_HOURS": "2",
            "HANDOFF_DEMO_MAX_SANDBOXES": "5",
            "HANDOFF_DEMO_LLM_DAILY_CAP": "0",
            "HANDOFF_DEMO_MAX_BODY_BYTES": "1024",
            "HANDOFF_DEMO_RATE": "5/second",
            "HANDOFF_DEMO_START_RATE": "1/minute",
            "HANDOFF_DEMO_CASSETTE": "/tmp/c.json",
            "HANDOFF_PUBLIC_URL": "https://example.test/app/",
        }
    )
    assert settings.demo is True
    assert settings.demo_ttl_hours == 2
    assert settings.demo_max_sandboxes == 5
    assert settings.demo_llm_daily_cap == 0
    assert settings.demo_max_body_bytes == 1024
    assert settings.demo_rate == "5/second"
    assert settings.demo_start_rate == "1/minute"
    assert settings.demo_cassette == Path("/tmp/c.json")
    assert settings.public_url == "https://example.test/app"


def test_demo_defaults_provider_to_replay_without_key() -> None:
    settings = load_settings({"HANDOFF_DEMO": "on"})
    assert settings.provider == "replay"
    assert settings.min_score == 0.25
    assert collection_name(settings) == settings.collection


def test_demo_explicit_provider_wins() -> None:
    assert load_settings({"HANDOFF_DEMO": "on", "HANDOFF_PROVIDER": "fake"}).provider == "fake"


def test_demo_requires_auth() -> None:
    with pytest.raises(ValueError, match="HANDOFF_DEMO=on requires HANDOFF_AUTH=on"):
        load_settings({"HANDOFF_DEMO": "on", "HANDOFF_AUTH": "off"})


def test_record_without_key_raises() -> None:
    with pytest.raises(
        ValueError, match="HANDOFF_LLM_API_KEY is required for HANDOFF_PROVIDER=record"
    ):
        load_settings({"HANDOFF_PROVIDER": "record", "HANDOFF_LLM_API_KEY": " "})


def test_record_and_replay_collection_unchanged() -> None:
    for provider in ("replay", "record"):
        settings = load_settings({"HANDOFF_PROVIDER": provider, "HANDOFF_LLM_API_KEY": QWEN_KEY})
        assert collection_name(settings) == "handoff_kb"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("HANDOFF_DEMO_TTL_HOURS", "0"),
        ("HANDOFF_DEMO_MAX_SANDBOXES", "0"),
        ("HANDOFF_DEMO_LLM_DAILY_CAP", "-1"),
        ("HANDOFF_DEMO_MAX_BODY_BYTES", "1023"),
        ("HANDOFF_DEMO_TTL_HOURS", "x"),
    ],
)
def test_demo_integer_minimums_name_variable(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name):
        load_settings({name: value})


@pytest.mark.parametrize("value", ["yes", "ON", ""])
def test_demo_rejects_non_on_off(value: str) -> None:
    with pytest.raises(ValueError, match="HANDOFF_DEMO must be on or off"):
        load_settings({"HANDOFF_DEMO": value})
