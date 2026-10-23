"""Card-based artifacts: runbook, 30-day onboarding, pitfalls Top10, ADR."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from handoff.contracts import (
    ARTIFACT_CARD_TYPES,
    ARTIFACT_NOT_FOUND,
    ARTIFACT_TITLES,
    NO_CARDS,
    Artifact,
    ArtifactKind,
    ArtifactSection,
    KnowledgeConflict,
    KnowledgeNotFound,
    NewArtifact,
)
from handoff.knowledge_store import KnowledgeStore
from handoff.llm_tasks.artifacts import PITFALLS_MAX, WEEKS, fake_write_artifact
from handoff.providers import LLM


def utc_now() -> datetime:
    return datetime.now(UTC)


def _onboarding_ok(sections: list[ArtifactSection]) -> bool:
    return len(sections) == WEEKS and all(
        s.heading.startswith(f"{i}주차") for i, s in enumerate(sections, start=1)
    )


def _markdown(title: str, sections: list[ArtifactSection]) -> str:
    out = f"# {title}\n\n"
    for s in sections:
        out += f"## {s.heading}\n\n{s.body}\n\n근거 카드: {', '.join(s.card_ids)}\n\n"
    return out


class ArtifactService:
    def __init__(
        self, llm: LLM, store: KnowledgeStore, clock: Callable[[], datetime] = utc_now
    ) -> None:
        self._llm = llm
        self._store = store
        self._clock = clock

    def generate(self, leaver: str, kind: ArtifactKind) -> Artifact:
        types = ARTIFACT_CARD_TYPES[kind]
        cards = [c for c in self._store.list_cards(leaver) if c.type in types]
        if not cards:
            raise KnowledgeConflict(NO_CARDS)
        valid = {c.id for c in cards}
        sections = [
            s for s in self._llm.write_artifact(kind, cards) if set(s.card_ids) <= valid
        ]
        if kind == "pitfalls_top10":
            sections = sections[:PITFALLS_MAX]
        if not sections or (kind == "onboarding_30d" and not _onboarding_ok(sections)):
            sections = fake_write_artifact(kind, cards)
        card_ids: list[str] = []
        for s in sections:
            card_ids.extend(i for i in s.card_ids if i not in card_ids)
        title = ARTIFACT_TITLES[kind]
        artifact = NewArtifact(
            leaver=leaver,
            kind=kind,
            title=title,
            sections=sections,
            markdown=_markdown(title, sections),
            card_ids=card_ids,
        )
        return self._store.save_artifact(artifact, self._clock())

    def latest(self, leaver: str, kind: ArtifactKind) -> Artifact:
        artifact = self._store.latest_artifact(leaver, kind)
        if artifact is None:
            raise KnowledgeNotFound(ARTIFACT_NOT_FOUND)
        return artifact
