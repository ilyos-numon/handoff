"""HTTP routes for knowledge gaps and the question queue."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from handoff.contracts import (
    DEFAULT_ROLE,
    PROVIDER_UNAVAILABLE,
    GapReport,
    QueueItem,
    QueueSource,
    QueueStatus,
)
from handoff.gaps import GapService
from handoff.providers import ProviderError

LEAVER_PATH = r"^[a-z0-9_-]{1,40}$"

router = APIRouter()


def get_gaps(request: Request) -> GapService:
    service: GapService = request.app.state.knowledge.gaps
    return service


GapsDep = Annotated[GapService, Depends(get_gaps)]
LeaverPath = Annotated[str, Path(pattern=LEAVER_PATH)]


@router.post("/leavers/{leaver}/gaps/refresh", response_model=GapReport)
def refresh_gaps(leaver: LeaverPath, gaps: GapsDep, role: str = DEFAULT_ROLE) -> GapReport:
    try:
        return gaps.refresh(leaver, role)
    except ProviderError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail=PROVIDER_UNAVAILABLE
        ) from None


@router.get("/leavers/{leaver}/gaps", response_model=GapReport)
def get_gap_report(leaver: LeaverPath, gaps: GapsDep) -> GapReport:
    return gaps.report(leaver)


@router.get("/leavers/{leaver}/queue", response_model=list[QueueItem])
def list_queue(
    leaver: LeaverPath,
    gaps: GapsDep,
    status_: Annotated[QueueStatus | None, Query(alias="status")] = None,
    source: QueueSource | None = None,
) -> list[QueueItem]:
    return gaps.queue(leaver, status_, source)
