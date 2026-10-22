"""Environment-based settings."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_PROVIDERS = frozenset({"fake"})


@dataclass(frozen=True)
class Settings:
    provider: str
    data_dir: Path
    min_score: float
    collection: str


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Load settings from ``env`` (defaults to ``os.environ``)."""
    source: Mapping[str, str] = os.environ if env is None else env
    provider = source.get("HANDOFF_PROVIDER", "fake")
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"unsupported HANDOFF_PROVIDER: {provider}")
    return Settings(
        provider=provider,
        data_dir=Path(source.get("HANDOFF_DATA_DIR", "./data")),
        min_score=float(source.get("HANDOFF_MIN_SCORE", "0.25")),
        collection=source.get("HANDOFF_COLLECTION", "handoff_kb"),
    )
