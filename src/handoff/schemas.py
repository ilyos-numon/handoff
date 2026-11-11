"""HTTP request/response models."""

from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints

from handoff.session import Question

NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
LeaverStr = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_-]{1,40}$")]


class StartSessionRequest(BaseModel):
    role: NonBlankStr
    leaver: LeaverStr = "default"


class StartSessionResponse(BaseModel):
    session_id: str
    question: Question
    leaver: str
    number: int
    planned: int
    remaining_seconds: int
    items_today: int
    items_total: int


class AnswerRequest(BaseModel):
    text: NonBlankStr


class AnswerResponse(BaseModel):
    next_question: Question | None
    remaining_seconds: int
    items_today: int
    items_total: int


class EndSessionResponse(BaseModel):
    session_id: str
    ingested: int
    number: int
    summary: list[str]
    items_total: int


class SessionStateResponse(BaseModel):
    session_id: str
    leaver: str
    role: str
    number: int
    planned: int
    status: Literal["open", "ended"]
    question: Question | None
    remaining_seconds: int
    items_today: int
    items_total: int
    summary: list[str]


class KBQueryRequest(BaseModel):
    question: NonBlankStr
