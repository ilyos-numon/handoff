"""Card-based handoff artifacts (implemented in stage S9)."""

from __future__ import annotations

from handoff.contracts import ArtifactKind, ArtifactSection, ChatFn, KnowledgeCard


def fake_write_artifact(kind: ArtifactKind, cards: list[KnowledgeCard]) -> list[ArtifactSection]:
    raise NotImplementedError("artifacts: implemented in stage S9")


def qwen_write_artifact(
    chat: ChatFn, kind: ArtifactKind, cards: list[KnowledgeCard]
) -> list[ArtifactSection]:
    raise NotImplementedError("artifacts: implemented in stage S9")
