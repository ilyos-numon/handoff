"""HTTP routes for incident drills."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, status

from handoff.contracts import (
    PROVIDER_UNAVAILABLE,
    DrillAnswerRequest,
    DrillCreateRequest,
    DrillFeedback,
    DrillScenario,
    KnowledgeConflict,
    KnowledgeNotFound,
)
from handoff.drill import DrillService
from handoff.providers import ProviderError
from handoff.session import LEAVER_PATTERN

router = APIRouter()

LeaverPath = Annotated[str, Path(pattern=LEAVER_PATTERN)]


def _service(request: Request) -> DrillService:
    service: DrillService = request.app.state.knowledge.drills
    return service


def _unavailable() -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=PROVIDER_UNAVAILABLE)


@router.post(
    "/leavers/{leaver}/drills",
    response_model=DrillScenario,
    status_code=status.HTTP_201_CREATED,
)
def create_drill(leaver: LeaverPath, req: DrillCreateRequest, request: Request) -> DrillScenario:
    try:
        return _service(request).create(leaver, req)
    except KnowledgeNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except KnowledgeConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except ProviderError:
        raise _unavailable() from None


@router.get("/drills/{drill_id}", response_model=DrillScenario)
def get_drill(drill_id: str, request: Request) -> DrillScenario:
    try:
        return _service(request).get(drill_id)
    except KnowledgeNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from None


@router.post("/drills/{drill_id}/answers", response_model=DrillFeedback)
def answer_drill(drill_id: str, req: DrillAnswerRequest, request: Request) -> DrillFeedback:
    try:
        return _service(request).review(drill_id, req)
    except KnowledgeNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except ProviderError:
        raise _unavailable() from None
