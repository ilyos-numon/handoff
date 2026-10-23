"""Assemble knowledge-loop services and routers for the FastAPI app."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI
from starlette.middleware.sessions import SessionMiddleware

from handoff import (
    admin,
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
from handoff.admin import LeaverRegistry
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
from handoff.rbac import routes_auth
from handoff.rbac.deps import authorize
from handoff.rbac.policy import SESSION_COOKIE, SESSION_MAX_AGE_S, AuthConfig, build_auth
from handoff.routing import QuestionRouter
from handoff.sources import SourceService
from handoff.store import SessionStore
from handoff.verify import ClaimVerifier, SuccessorService

if TYPE_CHECKING:
    from handoff.demo.routes import DemoRuntime


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
    auth: AuthConfig | None = None
    admin: LeaverRegistry | None = None
    public_url: str = ""
    demo: "DemoRuntime | None" = None


def _build_demo(settings: Settings, llm: LLM) -> "DemoRuntime | None":
    if not settings.demo:
        return None
    # Imported here: handoff.demo.sandbox imports this module.
    from handoff.demo.routes import DemoRuntime
    from handoff.demo.sandbox import DemoSandboxes

    stats = getattr(llm, "stats", None)
    return DemoRuntime(
        sandboxes=DemoSandboxes(
            settings.data_dir / "demo.db",
            settings.demo_ttl_hours * 3600,
            settings.demo_max_sandboxes,
        ),
        llm_stats=stats if callable(stats) else None,
    )


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
        auth=build_auth(settings),
        admin=LeaverRegistry(settings.data_dir / "admin.db") if settings.auth else None,
        public_url=settings.public_url,
        demo=_build_demo(settings, llm),
    )


def include_knowledge_routers(app: FastAPI) -> None:
    from handoff.demo import routes as demo_routes  # see _build_demo

    auth: AuthConfig | None = getattr(getattr(app.state, "knowledge", None), "auth", None)
    if auth is not None:
        app.add_middleware(
            SessionMiddleware,
            secret_key=auth.secret,
            session_cookie=SESSION_COOKIE,
            max_age=SESSION_MAX_AGE_S,
            same_site="lax",
            https_only=False,
        )
    app.include_router(routes_auth.router)
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
        admin,
        demo_routes,
    ):
        app.include_router(module.router, dependencies=[Depends(authorize)])
