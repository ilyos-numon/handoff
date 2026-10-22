"""Application factory entry point (run with ``uvicorn --factory``)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from handoff.config import Settings, load_settings
from handoff.kb import KnowledgeBase
from handoff.providers import build_providers
from handoff.question_tree import load_tree
from handoff.routes import Services, router
from handoff.session import SessionService


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = load_settings()
    embedder, llm = build_providers(settings)
    kb = KnowledgeBase(
        settings.data_dir / "qdrant", embedder, llm, settings.min_score, settings.collection
    )
    sessions = SessionService(kb, llm)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            kb.close()

    app = FastAPI(title="handoff", lifespan=lifespan)
    app.state.services = Services(sessions=sessions, kb=kb, trees=load_tree)
    app.include_router(router)
    return app
