"""HTTP routes."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from handoff.kb import KBAnswer, KnowledgeBase
from handoff.question_tree import QuestionTree, UnknownRoleError, load_tree
from handoff.schemas import (
    AnswerRequest,
    AnswerResponse,
    EndSessionResponse,
    KBQueryRequest,
    StartSessionRequest,
    StartSessionResponse,
)
from handoff.session import SessionClosedError, SessionNotFoundError, SessionService

UNKNOWN_ROLE = "unknown role"
SESSION_NOT_FOUND = "session not found"
SESSION_CLOSED = "session closed"


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
        session_id, question = services.sessions.start(body.role)
    except UnknownRoleError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=UNKNOWN_ROLE) from None
    return StartSessionResponse(session_id=session_id, question=question)


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
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from None
    return AnswerResponse(next_question=nxt)


@router.post("/sessions/{session_id}/end", response_model=EndSessionResponse)
def end_session(session_id: str, services: ServicesDep) -> EndSessionResponse:
    try:
        ingested = services.sessions.end(session_id)
    except SessionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=SESSION_NOT_FOUND) from None
    except SessionClosedError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=SESSION_CLOSED) from None
    return EndSessionResponse(session_id=session_id, ingested=ingested)


@router.post("/kb/query", response_model=KBAnswer)
def query_kb(body: KBQueryRequest, services: ServicesDep) -> KBAnswer:
    return services.kb.query(body.question)
