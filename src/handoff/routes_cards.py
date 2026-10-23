"""HTTP routes for typed knowledge cards."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from handoff.cards import CardService
from handoff.contracts import (
    PROVIDER_UNAVAILABLE,
    AtRiskReport,
    CardType,
    Certainty,
    ChangeEvent,
    KnowledgeCard,
    KnowledgeConflict,
    KnowledgeNotFound,
)
from handoff.providers import ProviderError
from handoff.session import LEAVER_PATTERN

router = APIRouter()


def get_card_service(request: Request) -> CardService:
    service: CardService = request.app.state.knowledge.cards
    return service


CardServiceDep = Annotated[CardService, Depends(get_card_service)]
LeaverPath = Annotated[str, Path(pattern=LEAVER_PATTERN)]


@router.get("/leavers/{leaver}/cards", response_model=list[KnowledgeCard])
def list_cards(
    leaver: LeaverPath,
    service: CardServiceDep,
    card_type: Annotated[CardType | None, Query(alias="type")] = None,
    min_confidence: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
    certainty: Annotated[Certainty | None, Query()] = None,
) -> list[KnowledgeCard]:
    return service.list(leaver, card_type, min_confidence, certainty)


@router.get("/leavers/{leaver}/cards/at-risk", response_model=AtRiskReport)
def cards_at_risk(
    leaver: LeaverPath,
    service: CardServiceDep,
    event: Annotated[ChangeEvent, Query()],
    target: Annotated[str | None, Query()] = None,
) -> AtRiskReport:
    return service.at_risk(leaver, event, target)


@router.post("/sessions/{session_id}/cards/extract", response_model=list[KnowledgeCard])
def extract_cards(session_id: str, service: CardServiceDep) -> list[KnowledgeCard]:
    try:
        return service.extract_for_session(session_id)
    except KnowledgeNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except KnowledgeConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except ProviderError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail=PROVIDER_UNAVAILABLE
        ) from None
