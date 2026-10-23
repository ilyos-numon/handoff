"""HTTP routes for answer depth (slot judgement, slot and tacit follow-ups, session fill rate)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from handoff.contracts import (
    PROVIDER_UNAVAILABLE,
    DepthJudgeRequest,
    KnowledgeNotFound,
    SessionDepthReport,
    SlotFollowUpResponse,
    SlotJudgement,
    TacitFollowUpResponse,
)
from handoff.depth import DepthService
from handoff.providers import ProviderError

router = APIRouter()


def get_depth(request: Request) -> DepthService:
    service: DepthService = request.app.state.knowledge.depth
    return service


DepthDep = Annotated[DepthService, Depends(get_depth)]


@router.post("/depth/judge", response_model=SlotJudgement)
def judge_depth(body: DepthJudgeRequest, depth: DepthDep) -> SlotJudgement:
    try:
        return depth.judge(body.question, body.answer)
    except ProviderError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=PROVIDER_UNAVAILABLE) from None


@router.post("/depth/follow-up", response_model=SlotFollowUpResponse)
def depth_follow_up(body: DepthJudgeRequest, depth: DepthDep) -> SlotFollowUpResponse:
    try:
        return depth.suggest(body.question, body.answer)
    except ProviderError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=PROVIDER_UNAVAILABLE) from None


@router.post("/depth/tacit", response_model=TacitFollowUpResponse)
def depth_tacit(body: DepthJudgeRequest, depth: DepthDep) -> TacitFollowUpResponse:
    return depth.tacit(body.question, body.answer)


@router.get("/sessions/{session_id}/depth", response_model=SessionDepthReport)
def session_depth(session_id: str, depth: DepthDep) -> SessionDepthReport:
    try:
        return depth.report(session_id)
    except KnowledgeNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
