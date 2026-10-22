"""Application factory entry point (run with ``uvicorn --factory``)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from handoff.config import Settings, collection_name, load_settings
from handoff.kb import KnowledgeBase
from handoff.providers import build_providers
from handoff.question_tree import load_tree
from handoff.routes import STATIC_DIR, Services, router
from handoff.session import SessionService
from handoff.store import SessionStore


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    embedder, llm = build_providers(settings)
    kb = KnowledgeBase(
        settings.data_dir / "qdrant",
        embedder,
        llm,
        settings.min_score,
        collection_name(settings),
        embed_with_question=(settings.provider != "fake"),
    )
    store = SessionStore(settings.data_dir / "handoff.db")
    sessions = SessionService(
        kb,
        llm,
        store=store,
        session_seconds=settings.session_seconds,
        grace_seconds=settings.session_grace_seconds,
        planned=settings.sessions_planned,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            sessions.close()
            kb.close()

    app = FastAPI(title="handoff", lifespan=lifespan)
    app.state.services = Services(sessions=sessions, kb=kb, trees=load_tree)
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
