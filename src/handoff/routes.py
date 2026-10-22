"""HTTP routes."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse

from handoff.coverage import CoverageReport
from handoff.kb import KBAnswer, KnowledgeBase
from handoff.providers import ProviderError
from handoff.question_tree import QuestionTree, UnknownRoleError, load_tree
from handoff.schemas import (
    AnswerRequest,
    AnswerResponse,
    EndSessionResponse,
    KBQueryRequest,
    SessionStateResponse,
    StartSessionRequest,
    StartSessionResponse,
)
from handoff.session import (
    LEAVER_PATTERN,
    SessionClosedError,
    SessionLimitError,
    SessionNotFoundError,
    SessionService,
    SessionTimeOverError,
    TreeExhaustedError,
)

UNKNOWN_ROLE = "unknown role"
SESSION_NOT_FOUND = "session not found"
SESSION_CLOSED = "session closed"
SESSION_LIMIT = "session limit reached"
TREE_EXHAUSTED = "question tree exhausted"
TIME_OVER = "session time over"
PROVIDER_UNAVAILABLE = "provider unavailable"
STATIC_DIR = Path(__file__).parent / "static"


@dataclass
class Services:
    sessions: SessionService
    kb: KnowledgeBase
    trees: Callable[[str], QuestionTree] = field(default=load_tree)


router = APIRouter()


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/question-trees/{role}", response_model=QuestionTree)
def get_question_tree(role: str, services: ServicesDep) -> QuestionTree:
    try:
        return services.trees(role)
    except UnknownRoleError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=UNKNOWN_ROLE) from None


@router.post(
    "/sessions", response_model=StartSessionResponse, status_code=status.HTTP_201_CREATED
)
def start_session(body: StartSessionRequest, services: ServicesDep) -> StartSessionResponse:
    try:
        session_id, question = services.sessions.start(body.role, body.leaver)
    except UnknownRoleError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=UNKNOWN_ROLE) from None
    except SessionLimitError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=SESSION_LIMIT) from None
    except TreeExhaustedError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=TREE_EXHAUSTED) from None
    except ProviderError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail=PROVIDER_UNAVAILABLE
        ) from None
    view = services.sessions.view(session_id)
    return StartSessionResponse(
        session_id=session_id,
        question=question,
        leaver=view.leaver,
        number=view.number,
        planned=view.planned,
        remaining_seconds=view.remaining_seconds,
        items_today=view.items_today,
        items_total=view.items_total,
    )


@router.get("/sessions/{session_id}", response_model=SessionStateResponse)
def get_session(session_id: str, services: ServicesDep) -> SessionStateResponse:
    try:
        view = services.sessions.view(session_id)
    except SessionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=SESSION_NOT_FOUND) from None
    return SessionStateResponse(
        session_id=view.session_id,
        leaver=view.leaver,
        role=view.role,
        number=view.number,
        planned=view.planned,
        status=view.status,
        question=view.question,
        remaining_seconds=view.remaining_seconds,
        items_today=view.items_today,
        items_total=view.items_total,
        summary=view.summary,
    )


@router.post("/sessions/{session_id}/answers", response_model=AnswerResponse)
def answer_session(
    session_id: str, body: AnswerRequest, services: ServicesDep
) -> AnswerResponse:
    try:
        nxt = services.sessions.answer(session_id, body.text)
    except SessionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=SESSION_NOT_FOUND) from None
    except SessionClosedError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=SESSION_CLOSED) from None
    except SessionTimeOverError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=TIME_OVER) from None
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from None
    view = services.sessions.view(session_id)
    return AnswerResponse(
        next_question=nxt,
        remaining_seconds=view.remaining_seconds,
        items_today=view.items_today,
        items_total=view.items_total,
    )


@router.post("/sessions/{session_id}/end", response_model=EndSessionResponse)
def end_session(session_id: str, services: ServicesDep) -> EndSessionResponse:
    try:
        ingested = services.sessions.end(session_id)
    except SessionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=SESSION_NOT_FOUND) from None
    except SessionClosedError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=SESSION_CLOSED) from None
    except ProviderError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail=PROVIDER_UNAVAILABLE
        ) from None
    view = services.sessions.view(session_id)
    return EndSessionResponse(
        session_id=session_id,
        ingested=ingested,
        number=view.number,
        summary=view.summary,
        items_total=view.items_total,
    )


@router.get("/coverage", response_model=CoverageReport)
def get_coverage(
    services: ServicesDep,
    leaver: Annotated[str, Query(pattern=LEAVER_PATTERN)] = "default",
    role: str = "backend_developer",
) -> CoverageReport:
    try:
        return services.sessions.coverage(leaver, role)
    except UnknownRoleError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=UNKNOWN_ROLE) from None


@router.post("/kb/query", response_model=KBAnswer)
def query_kb(body: KBQueryRequest, services: ServicesDep) -> KBAnswer:
    try:
        return services.kb.query(body.question)
    except ProviderError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail=PROVIDER_UNAVAILABLE
        ) from None


def _page(name: str) -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / name).read_text(encoding="utf-8"))


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def session_page() -> HTMLResponse:
    return _page("session.html")


@router.get("/chat", response_class=HTMLResponse, include_in_schema=False)
def chat_page() -> HTMLResponse:
    return _page("chat.html")


@router.get("/report", response_class=HTMLResponse, include_in_schema=False)
def report_page() -> HTMLResponse:
    return _page("report.html")
