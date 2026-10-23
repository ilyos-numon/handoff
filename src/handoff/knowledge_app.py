"""Assemble knowledge-loop services and routers for the FastAPI app."""

from dataclasses import dataclass

from fastapi import FastAPI

from handoff import (
    routes_artifacts,
    routes_cards,
    routes_depth,
    routes_drill,
    routes_fence,
    routes_gaps,
    routes_premortem,
    routes_sources,
    routes_successor,
)
from handoff.artifacts import ArtifactService
from handoff.cards import CardService
from handoff.config import Settings
from handoff.depth import DepthService
from handoff.drill import DrillService
from handoff.fence import FenceService
from handoff.gaps import GapService
from handoff.knowledge_store import KnowledgeStore
from handoff.loop import KnowledgeHooks
from handoff.premortem import PremortemService
from handoff.providers import LLM
from handoff.routing import QuestionRouter
from handoff.sources import SourceService
from handoff.store import SessionStore
from handoff.verify import ClaimVerifier, SuccessorService


@dataclass
class KnowledgeServices:
    store: KnowledgeStore
    sources: SourceService
    depth: DepthService
    cards: CardService
    gaps: GapService
    verifier: ClaimVerifier | None
    successor: SuccessorService
    artifacts: ArtifactService
    drills: DrillService
    hooks: KnowledgeHooks
    fence: FenceService
    router: QuestionRouter
    premortem: PremortemService


def build_knowledge(settings: Settings, llm: LLM, session_store: SessionStore) -> KnowledgeServices:
    store = KnowledgeStore.from_session_store(session_store)
    depth = DepthService(
        llm,
        store,
        session_store,
        settings.depth_max_followups,
        tacit_followups=settings.tacit_followups,
    )
    cards = CardService(llm, store, session_store)
    router = QuestionRouter(llm, store, session_store)
    return KnowledgeServices(
        store=store,
        sources=SourceService(llm, store),
        depth=depth,
        cards=cards,
        gaps=GapService(llm, store, session_store),
        verifier=ClaimVerifier(llm) if settings.verify_claims else None,
        successor=SuccessorService(store, router=router),
        artifacts=ArtifactService(llm, store),
        drills=DrillService(llm, store),
        hooks=KnowledgeHooks(store, session_store, depth, cards, settings.queue_per_session),
        fence=FenceService(llm, store),
        router=router,
        premortem=PremortemService(store),
    )


def include_knowledge_routers(app: FastAPI) -> None:
    for module in (
        routes_sources,
        routes_depth,
        routes_cards,
        routes_gaps,
        routes_successor,
        routes_artifacts,
        routes_drill,
        routes_fence,
        routes_premortem,
    ):
        app.include_router(module.router)
