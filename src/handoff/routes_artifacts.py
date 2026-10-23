"""HTTP routes for card-based artifacts."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from handoff.artifacts import ArtifactService
from handoff.contracts import (
    PROVIDER_UNAVAILABLE,
    Artifact,
    ArtifactKind,
    KnowledgeConflict,
    KnowledgeNotFound,
)
from handoff.providers import ProviderError
from handoff.session import LEAVER_PATTERN

router = APIRouter()


def get_artifacts(request: Request) -> ArtifactService:
    service: ArtifactService = request.app.state.knowledge.artifacts
    return service


ArtifactsDep = Annotated[ArtifactService, Depends(get_artifacts)]
LeaverPath = Annotated[str, Path(pattern=LEAVER_PATTERN)]


@router.post("/leavers/{leaver}/artifacts/{kind}", response_model=Artifact)
def generate_artifact(leaver: LeaverPath, kind: ArtifactKind, service: ArtifactsDep) -> Artifact:
    try:
        return service.generate(leaver, kind)
    except KnowledgeConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except ProviderError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail=PROVIDER_UNAVAILABLE
        ) from None


@router.get("/leavers/{leaver}/artifacts/{kind}", response_model=Artifact)
def get_artifact(leaver: LeaverPath, kind: ArtifactKind, service: ArtifactsDep) -> Artifact:
    try:
        return service.latest(leaver, kind)
    except KnowledgeNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
