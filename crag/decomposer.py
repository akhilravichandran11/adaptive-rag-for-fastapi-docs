"""
Turn the aspects the grader found missing into one retrieval query and one web query each,
with a single structured LLM call over all of them (D7).
"""

import logging
from typing import Any

from haystack import Document
from pydantic import BaseModel

logger = logging.getLogger(__name__)

VECTOR_QUERY_MAX_WORDS = 25
WEB_QUERY_KEYWORDS = "3-5"
PROMPT = """You help a documentation search system recover what its first search missed.

A user asked a question. The search found the passages below, but a reviewer judged that they
do not cover some aspects of the question. For each missing aspect, reason step by step:
1. user_need: what exactly the user needs to know about this aspect.
2. retrieval_gap_reason: why the search most likely did not find it (vocabulary, a related but
   different topic, a detail buried in a longer page, and so on).
3. target_document_type: what the passage that answers it would look like (a tutorial
   section, an API reference entry, a configuration example, ...).
4. vector_query: a search query of fewer than {max_words} words, phrased the way the
   documentation itself would describe the answer, not the way the user asked.
5. web_query: {keywords} keywords for a web search engine.

Return one analysis per missing aspect, copying the aspect into missing_aspect.

Question:
{query}

Passages already found:
{passages}

Missing aspects:
{aspects}
"""


class AspectAnalysis(BaseModel):
    missing_aspect: str
    user_need: str
    retrieval_gap_reason: str
    target_document_type: str
    vector_query: str
    web_query: str


class Decomposition(BaseModel):
    analyses: list[AspectAnalysis]


def format_passages(documents: list[Document]) -> str:
    if not documents:
        return "(none)"
    return "\n\n".join(f"[{doc.meta['source_path']}]\n{doc.content}" for doc in documents)


class QueryDecomposer:
    def __init__(self, llm: Any) -> None:
        self.llm = llm

    def run(
        self, query: str, missing_aspects: list[str], relevant_documents: list[Document]
    ) -> dict[str, Any]:
        if not missing_aspects:
            return {"vector_queries": [], "web_queries": [], "model": None}

        prompt = PROMPT.format(
            max_words=VECTOR_QUERY_MAX_WORDS,
            keywords=WEB_QUERY_KEYWORDS,
            query=query,
            passages=format_passages(relevant_documents),
            aspects="\n".join(f"- {aspect}" for aspect in missing_aspects),
        )
        # The analyses are not matched to the aspects: the model may merge or add some (D7).
        reply, model = self.llm.ask(prompt, Decomposition)
        logger.info(
            "decompose: %d missing aspects -> %d analyses (%s)",
            len(missing_aspects),
            len(reply.analyses),
            model,
        )
        return {
            "vector_queries": [analysis.vector_query for analysis in reply.analyses],
            "web_queries": [analysis.web_query for analysis in reply.analyses],
            "model": model,
        }
