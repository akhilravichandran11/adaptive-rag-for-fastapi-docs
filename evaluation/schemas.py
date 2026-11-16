from typing import Literal

from pydantic import BaseModel, Field


# For the evaluation question sets; why these fields: evaluation/DECISIONS.md, 2026-11-09
class EvalQuestion(BaseModel):
    question_id: str
    question: str
    reference_answer: str
    gold_sources: list[str] = Field(min_length=1)
    origin: Literal["handwritten", "synthetic"]
    paraphrase: str | None = None
