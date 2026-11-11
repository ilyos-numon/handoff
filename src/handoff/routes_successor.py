"""HTTP routes for the successor unanswered-question loop."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, status

from handoff.contracts import (
    PROVIDER_UNAVAILABLE,
    QUESTION_NOT_FOUND,
    AskRequest,
    AskResponse,
    QuestionThread,
    QueueItem,
)
from handoff.kb import KnowledgeBase
from handoff.providers import ProviderError
from handoff.rbac.deps import FORBIDDEN, current_principal, get_auth
from handoff.verify import SuccessorService

LeaverPath = Annotated[str, Path(pattern=r"^[a-z0-9_-]{1,40}$")]
QuestionIdPath = Annotated[str, Path(pattern=r"^q\d+$")]

router = APIRouter()


def _successor(request: Request) -> SuccessorService:
    service: SuccessorService = request.app.state.knowledge.successor
    return service


def _kb(request: Request) -> KnowledgeBase:
    kb: KnowledgeBase = request.app.state.services.kb
    return kb


@router.post("/kb/ask", response_model=AskResponse)
def ask(body: AskRequest, request: Request) -> AskResponse:
    try:
        return _successor(request).ask(_kb(request), body)
    except ProviderError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail=PROVIDER_UNAVAILABLE
        ) from None


@router.get("/leavers/{leaver}/successor-questions", response_model=list[QueueItem])
def successor_questions(leaver: LeaverPath, request: Request) -> list[QueueItem]:
    return _successor(request).list(leaver)


@router.get("/leavers/{leaver}/questions/{qid}/thread", response_model=QuestionThread)
def question_thread(leaver: LeaverPath, qid: QuestionIdPath, request: Request) -> QuestionThread:
    thread = _successor(request).thread(leaver, qid)
    if thread is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=QUESTION_NOT_FOUND)
    if get_auth(request) is not None:
        # Each asker sees only the answers to questions from their own source.
        principal = current_principal(request)
        role = principal.role if principal is not None else None
        if role not in ("successor", "manager") or thread.item.source != role:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=FORBIDDEN)
    return thread
