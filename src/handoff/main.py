"""Application factory entry point (run with ``uvicorn --factory``)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from handoff.config import Settings, api_docs_kwargs, collection_name, load_settings
from handoff.kb import KnowledgeBase
from handoff.knowledge_app import build_knowledge, include_knowledge_routers
from handoff.providers import build_providers
from handoff.question_tree import load_tree
from handoff.routes import STATIC_DIR, Services, router
from handoff.session import SessionService
from handoff.store import SessionStore
from handoff.web import install_web


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    embedder, llm = build_providers(settings)
    store = SessionStore(settings.data_dir / "handoff.db")
    knowledge = build_knowledge(settings, llm, store)
    kb = KnowledgeBase(
        settings.data_dir / "qdrant",
        embedder,
        llm,
        settings.min_score,
        collection_name(settings),
        embed_with_question=(settings.provider != "fake"),
        verifier=knowledge.verifier,
    )
    sessions = SessionService(
        kb,
        llm,
        store=store,
        session_seconds=settings.session_seconds,
        grace_seconds=settings.session_grace_seconds,
        planned=settings.sessions_planned,
        hooks=knowledge.hooks,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            sessions.close()
            kb.close()

    docs = api_docs_kwargs(settings)
    app = FastAPI(
        title="handoff",
        lifespan=lifespan,
        docs_url=docs.get("docs_url", "/docs"),
        redoc_url=docs.get("redoc_url", "/redoc"),
        openapi_url=docs.get("openapi_url", "/openapi.json"),
    )
    app.state.services = Services(sessions=sessions, kb=kb, trees=load_tree)
    app.state.knowledge = knowledge
    app.include_router(router)
    include_knowledge_routers(app)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    install_web(app, settings.web_dist)
    return app
