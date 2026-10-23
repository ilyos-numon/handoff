"""HTTP routes for the successor unanswered-question loop."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, status

from handoff.contracts import PROVIDER_UNAVAILABLE, AskRequest, AskResponse, QueueItem
from handoff.kb import KnowledgeBase
from handoff.providers import ProviderError
from handoff.verify import SuccessorService

LeaverPath = Annotated[str, Path(pattern=r"^[a-z0-9_-]{1,40}$")]

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
