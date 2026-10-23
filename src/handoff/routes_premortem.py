"""HTTP routes for premortem questions."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request

from handoff.contracts import DEFAULT_ROLE, QueueItem
from handoff.premortem import PremortemService
from handoff.session import LEAVER_PATTERN

router = APIRouter()


def get_premortem(request: Request) -> PremortemService:
    service: PremortemService = request.app.state.knowledge.premortem
    return service


PremortemDep = Annotated[PremortemService, Depends(get_premortem)]
LeaverPath = Annotated[str, Path(pattern=LEAVER_PATTERN)]


@router.post("/leavers/{leaver}/premortem/questions", response_model=list[QueueItem])
def enqueue_premortem(
    leaver: LeaverPath, service: PremortemDep, role: str = DEFAULT_ROLE
) -> list[QueueItem]:
    return service.enqueue(leaver, role)
