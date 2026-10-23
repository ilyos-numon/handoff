"""Environment-based settings."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_PROVIDERS = frozenset({"fake", "qwen"})
DEFAULT_LLM_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

_DEFAULT_MIN_SCORE = {"fake": 0.25, "qwen": 0.40}


@dataclass(frozen=True)
class Settings:
    provider: str
    data_dir: Path
    min_score: float
    collection: str
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_model: str = "qwen3.7-flash"
    llm_api_key: str = field(default="", repr=False)
    llm_timeout_s: float = 20.0
    embed_model: str = "text-embedding-v4"
    embed_dim: int = 1024
    session_seconds: int = 900
    session_grace_seconds: int = 30
    sessions_planned: int = 10
    depth_max_followups: int = 1
    queue_per_session: int = 2
    verify_claims: bool = True


def _float(source: Mapping[str, str], name: str, default: float) -> float:
    raw = source.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number") from None


def _int(source: Mapping[str, str], name: str, default: int, minimum: int) -> int:
    raw = source.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _on_off(source: Mapping[str, str], name: str, default: bool) -> bool:
    raw = source.get(name)
    if raw is None:
        return default
    if raw == "on":
        return True
    if raw == "off":
        return False
    raise ValueError(f"{name} must be on or off")


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Load settings from ``env`` (defaults to ``os.environ``)."""
    source: Mapping[str, str] = os.environ if env is None else env
    provider = source.get("HANDOFF_PROVIDER", "fake")
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"unsupported HANDOFF_PROVIDER: {provider}")
    api_key = source.get("HANDOFF_LLM_API_KEY", "")
    if provider == "qwen" and not api_key.strip():
        raise ValueError("HANDOFF_LLM_API_KEY is required for HANDOFF_PROVIDER=qwen")
    return Settings(
        provider=provider,
        data_dir=Path(source.get("HANDOFF_DATA_DIR", "./data")),
        min_score=_float(source, "HANDOFF_MIN_SCORE", _DEFAULT_MIN_SCORE[provider]),
        collection=source.get("HANDOFF_COLLECTION", "handoff_kb"),
        llm_base_url=source.get("HANDOFF_LLM_BASE_URL", DEFAULT_LLM_BASE_URL),
        llm_model=source.get("HANDOFF_LLM_MODEL", "qwen3.7-flash"),
        llm_api_key=api_key,
        llm_timeout_s=_float(source, "HANDOFF_LLM_TIMEOUT", 20.0),
        embed_model=source.get("HANDOFF_EMBED_MODEL", "text-embedding-v4"),
        embed_dim=_int(source, "HANDOFF_EMBED_DIM", 1024, 1),
        session_seconds=_int(source, "HANDOFF_SESSION_SECONDS", 900, 1),
        session_grace_seconds=_int(source, "HANDOFF_SESSION_GRACE_SECONDS", 30, 0),
        sessions_planned=_int(source, "HANDOFF_SESSIONS_PLANNED", 10, 1),
        depth_max_followups=_int(source, "HANDOFF_DEPTH_MAX_FOLLOWUPS", 1, 0),
        queue_per_session=_int(source, "HANDOFF_QUEUE_PER_SESSION", 2, 0),
        verify_claims=_on_off(source, "HANDOFF_VERIFY_CLAIMS", True),
    )


def collection_name(settings: Settings) -> str:
    """Return the Qdrant collection name; non-fake providers get model/dim suffixes."""
    if settings.provider == "fake":
        return settings.collection
    model = re.sub(r"[^a-z0-9]+", "_", settings.embed_model.lower()).strip("_")
    return f"{settings.collection}_{model}_{settings.embed_dim}"
