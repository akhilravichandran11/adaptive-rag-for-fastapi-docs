"""
Request and response bodies for ``POST /query``; the response mirrors ``CragResult`` (S006).
"""

from typing import Annotated

from pydantic import BaseModel, StringConstraints

Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class QueryRequest(BaseModel):
    question: Question


class Source(BaseModel):
    rank: int
    origin: str
    source: str
    content: str | None
    score: float | None


class Step(BaseModel):
    step: str
    started: str
    ended: str
    counts: dict[str, int]
    model: str | None = None


class QueryResponse(BaseModel):
    question: str
    route: str
    answer: str
    sources: list[Source]
    missing_aspects: list[str]
    steps: list[Step]
