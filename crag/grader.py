"""
Grade the retrieved chunks for one question: which hold a passage a correct answer would use,
then whether those chunks cover every aspect of the question (D7). Errors from ``llm.ask``
propagate; ``StructuredLLM`` already retries and falls back (D5).
"""

import logging
from typing import Any

from haystack import Document
from pydantic import BaseModel

RELEVANT_VERDICT = "relevant"

RELEVANCE_PROMPT = """You are checking search results before they are used to answer a \
question about FastAPI.

Question: {query}

For every document below, decide whether it contains a specific passage that would appear in \
a correct answer to the question. Judge only from the document's text, never from what you \
already know. If you call a document relevant, copy that passage word for word into \
verbatim_quote; otherwise leave verbatim_quote empty. A document that is merely on the same \
topic, or only mentions the question's terms, is not relevant.

Reply with one grade per document: doc_index (the number in its [Document N] header), a short \
reasoning, verbatim_quote, and verdict, which is exactly "relevant" or "not_relevant".

{documents}"""

COVERAGE_PROMPT = """You are checking whether some FastAPI documentation is enough to answer a \
question in full.

Question: {query}

First split the question into its separate aspects: each thing a complete answer has to \
address. Then, for each aspect, decide whether the documents below give specific detail on \
it, judging only from their text and never from what you already know. When an aspect is \
covered, copy the supporting passage word for word into verbatim_quote and give the number \
from its [Document N] header as source_doc. When it is not covered, set verbatim_quote to an \
empty string and source_doc to 0.

Reply with the list of aspects, each with aspect, covered, verbatim_quote and source_doc, and \
all_covered: true only if every aspect is covered.

{documents}"""

logger = logging.getLogger(__name__)


class DocumentGrade(BaseModel):
    doc_index: int
    reasoning: str
    verbatim_quote: str
    verdict: str


class RelevanceReply(BaseModel):
    grades: list[DocumentGrade]


class AspectCoverage(BaseModel):
    aspect: str
    covered: bool
    verbatim_quote: str
    source_doc: int


class CoverageReply(BaseModel):
    aspects: list[AspectCoverage]
    all_covered: bool


def format_documents(documents: list[Document]) -> str:
    return "\n\n".join(
        f"[Document {i}] Source: {doc.meta['source_path']}\n{doc.content}"
        for i, doc in enumerate(documents, start=1)
    )


class DocumentGrader:
    def __init__(self, llm: Any) -> None:
        self.llm = llm

    def run(self, query: str, documents: list[Document]) -> dict[str, Any]:
        prompt = RELEVANCE_PROMPT.format(query=query, documents=format_documents(documents))
        relevance, relevance_model = self.llm.ask(prompt, RelevanceReply)
        relevant_indices = {
            grade.doc_index for grade in relevance.grades if grade.verdict == RELEVANT_VERDICT
        }
        relevant = [doc for i, doc in enumerate(documents, start=1) if i in relevant_indices]
        logger.info("relevance: %d of %d chunks relevant", len(relevant), len(documents))

        if not relevant:
            return {
                "decision": "incorrect",
                "relevant_documents": [],
                "aspects": [],
                "missing_aspects": [],
                "model": relevance_model,
            }

        # source_doc is 1-based into relevant_documents: this prompt renumbers them.
        prompt = COVERAGE_PROMPT.format(query=query, documents=format_documents(relevant))
        coverage, coverage_model = self.llm.ask(prompt, CoverageReply)
        aspects = [aspect.model_dump() for aspect in coverage.aspects]
        missing = [aspect.aspect for aspect in coverage.aspects if not aspect.covered]
        # all_covered is trusted even when an aspect disagrees with it (D7, ported as is).
        decision = "correct" if coverage.all_covered else "ambiguous"
        logger.info("coverage: %d of %d aspects missing", len(missing), len(aspects))
        return {
            "decision": decision,
            "relevant_documents": relevant,
            "aspects": aspects,
            "missing_aspects": missing,
            "model": ", ".join(dict.fromkeys([relevance_model, coverage_model])),
        }
