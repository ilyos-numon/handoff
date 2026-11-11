"""HTTP route for Chesterton's fence questions."""

from fastapi import APIRouter, HTTPException, Request, status

from handoff.contracts import PROVIDER_UNAVAILABLE, FenceRequest, FenceResponse
from handoff.fence import FenceService
from handoff.providers import ProviderError

router = APIRouter()


def _fence(request: Request) -> FenceService:
    service: FenceService = request.app.state.knowledge.fence
    return service


@router.post("/kb/fence", response_model=FenceResponse)
def fence(body: FenceRequest, request: Request) -> FenceResponse:
    try:
        return _fence(request).ask(body)
    except ProviderError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail=PROVIDER_UNAVAILABLE
        ) from None
