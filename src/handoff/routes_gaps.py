"""HTTP routes for knowledge gaps and the question queue."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status

from handoff.contracts import (
    DEFAULT_ROLE,
    MANAGER_REASON,
    PROVIDER_UNAVAILABLE,
    GapReport,
    PriorityQuestionRequest,
    QueueItem,
    QueueSource,
    QueueStatus,
)
from handoff.gaps import GapService, utc_now
from handoff.knowledge_store import KnowledgeStore
from handoff.providers import ProviderError
from handoff.verify import guess_axis

LEAVER_PATH = r"^[a-z0-9_-]{1,40}$"

router = APIRouter()


def get_gaps(request: Request) -> GapService:
    service: GapService = request.app.state.knowledge.gaps
    return service


def get_store(request: Request) -> KnowledgeStore:
    store: KnowledgeStore = request.app.state.knowledge.store
    return store


GapsDep = Annotated[GapService, Depends(get_gaps)]
StoreDep = Annotated[KnowledgeStore, Depends(get_store)]
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


@router.post(
    "/leavers/{leaver}/priority-questions",
    response_model=QueueItem,
    status_code=status.HTTP_201_CREATED,
)
def add_priority_question(
    leaver: LeaverPath, body: PriorityQuestionRequest, store: StoreDep, response: Response
) -> QueueItem:
    item, created = store.enqueue(
        leaver,
        body.role,
        "manager",
        body.axis or guess_axis(body.text),
        body.text,
        MANAGER_REASON,
        None,
        utc_now(),
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return item
