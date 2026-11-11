"""HTTP routes for work sources, entities and tailored question generation."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from handoff.contracts import (
    PROVIDER_UNAVAILABLE,
    Entity,
    GenerateQuestionsRequest,
    GenerateQuestionsResponse,
    KnowledgeConflict,
    SourceDoc,
    SourceUploadRequest,
)
from handoff.providers import ProviderError
from handoff.sources import SourceService

LEAVER_PATTERN = r"^[a-z0-9_-]{1,40}$"

router = APIRouter()


def get_sources(request: Request) -> SourceService:
    service: SourceService = request.app.state.knowledge.sources
    return service


SourcesDep = Annotated[SourceService, Depends(get_sources)]
LeaverPath = Annotated[str, Path(pattern=LEAVER_PATTERN)]


@router.post(
    "/leavers/{leaver}/sources", response_model=SourceDoc, status_code=status.HTTP_201_CREATED
)
def upload_source(leaver: LeaverPath, body: SourceUploadRequest, sources: SourcesDep) -> SourceDoc:
    return sources.upload(leaver, body)


@router.post(
    "/leavers/{leaver}/sources/mock",
    response_model=list[SourceDoc],
    status_code=status.HTTP_201_CREATED,
)
def load_mock_sources(leaver: LeaverPath, sources: SourcesDep) -> list[SourceDoc]:
    return sources.load_mock(leaver)


@router.get("/leavers/{leaver}/sources", response_model=list[SourceDoc])
def list_sources(leaver: LeaverPath, sources: SourcesDep) -> list[SourceDoc]:
    return sources.list(leaver)


@router.get("/leavers/{leaver}/entities", response_model=list[Entity])
def list_entities(leaver: LeaverPath, sources: SourcesDep) -> list[Entity]:
    return sources.entities(leaver)


@router.post("/leavers/{leaver}/questions/generate", response_model=GenerateQuestionsResponse)
def generate_questions(
    leaver: LeaverPath, body: GenerateQuestionsRequest, sources: SourcesDep
) -> GenerateQuestionsResponse:
    try:
        return sources.generate(leaver, body)
    except KnowledgeConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except ProviderError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail=PROVIDER_UNAVAILABLE
        ) from None
