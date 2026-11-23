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


# For the golden set; why these fields and bounds: evaluation/DECISIONS.md, 2026-11-20
class ReferenceChunk(BaseModel):
    source_path: str
    strategy: str
    chunk_index: int
    content: str


class GoldenEntry(BaseModel):
    question_id: str
    reference_answer: str = Field(min_length=1)
    reference_chunks: list[ReferenceChunk] = Field(min_length=1, max_length=5)
    rationale: str = Field(min_length=1)


class GoldenSet(BaseModel):
    index_run: str
    source_runs: list[str]
    entries: list[GoldenEntry] = Field(min_length=1)
