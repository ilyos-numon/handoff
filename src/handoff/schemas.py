"""HTTP request/response models."""

from typing import Annotated

from pydantic import BaseModel, StringConstraints

from handoff.session import Question

NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class StartSessionRequest(BaseModel):
    role: NonBlankStr


class StartSessionResponse(BaseModel):
    session_id: str
    question: Question


class AnswerRequest(BaseModel):
    text: NonBlankStr


class AnswerResponse(BaseModel):
    next_question: Question | None


class EndSessionResponse(BaseModel):
    session_id: str
    ingested: int


class KBQueryRequest(BaseModel):
    question: NonBlankStr
