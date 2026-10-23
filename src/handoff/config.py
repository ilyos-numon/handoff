"""Environment-based settings."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_PROVIDERS = frozenset({"fake", "qwen", "replay", "record"})
DEFAULT_LLM_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_CASSETTE: Path = Path(__file__).parent / "demo" / "cassette.json"

_DEFAULT_MIN_SCORE = {"fake": 0.25, "qwen": 0.40, "replay": 0.25, "record": 0.25}
_FAKE_EMBED_PROVIDERS = frozenset({"fake", "replay", "record"})


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
    tacit_followups: bool = True
    auth: bool = False
    auth_secret: str = field(default="", repr=False)
    invite_ttl_hours: int = 72
    demo: bool = False
    demo_ttl_hours: int = 24
    demo_max_sandboxes: int = 200
    demo_llm_daily_cap: int = 300
    demo_max_body_bytes: int = 8192
    demo_rate: str = "120/minute"
    demo_start_rate: str = "10/hour"
    demo_cassette: Path = DEFAULT_CASSETTE
    public_url: str = ""


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
    demo = _on_off(source, "HANDOFF_DEMO", False)
    provider = source.get("HANDOFF_PROVIDER", "replay" if demo else "fake")
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"unsupported HANDOFF_PROVIDER: {provider}")
    api_key = source.get("HANDOFF_LLM_API_KEY", "")
    if provider in ("qwen", "record") and not api_key.strip():
        raise ValueError(f"HANDOFF_LLM_API_KEY is required for HANDOFF_PROVIDER={provider}")
    auth = _on_off(source, "HANDOFF_AUTH", True)
    if demo and not auth:
        raise ValueError("HANDOFF_DEMO=on requires HANDOFF_AUTH=on")
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
        tacit_followups=_on_off(source, "HANDOFF_TACIT_FOLLOWUPS", True),
        auth=auth,
        auth_secret=source.get("HANDOFF_AUTH_SECRET", ""),
        invite_ttl_hours=_int(source, "HANDOFF_INVITE_TTL_HOURS", 72, 1),
        demo=demo,
        demo_ttl_hours=_int(source, "HANDOFF_DEMO_TTL_HOURS", 24, 1),
        demo_max_sandboxes=_int(source, "HANDOFF_DEMO_MAX_SANDBOXES", 200, 1),
        demo_llm_daily_cap=_int(source, "HANDOFF_DEMO_LLM_DAILY_CAP", 300, 0),
        demo_max_body_bytes=_int(source, "HANDOFF_DEMO_MAX_BODY_BYTES", 8192, 1024),
        demo_rate=source.get("HANDOFF_DEMO_RATE", "120/minute"),
        demo_start_rate=source.get("HANDOFF_DEMO_START_RATE", "10/hour"),
        demo_cassette=Path(source.get("HANDOFF_DEMO_CASSETTE", str(DEFAULT_CASSETTE))),
        public_url=source.get("HANDOFF_PUBLIC_URL", "").rstrip("/"),
    )


def collection_name(settings: Settings) -> str:
    """Return the Qdrant collection name; real-embedding providers get model/dim suffixes."""
    if settings.provider in _FAKE_EMBED_PROVIDERS:
        return settings.collection
    model = re.sub(r"[^a-z0-9]+", "_", settings.embed_model.lower()).strip("_")
    return f"{settings.collection}_{model}_{settings.embed_dim}"
