"""
Keep what helps from the second retrieval and the web search (D7): relevant chunks pass
through, an LLM re-grades the re-retrieved chunks and extracts passages from each web page.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from haystack import Document
from pydantic import BaseModel

logger = logging.getLogger(__name__)

EXTRACT_WORKERS = 3

REGRADE_PROMPT = """You are checking extra documentation chunks found for a question.
An earlier search answered part of the question; these chunks were fetched to fill the gaps.

Question: {query}

Aspects still missing:
{missing}

For each chunk, decide whether it contains a specific passage that covers one of the missing
aspects. Judge only from the chunk text, not from your own knowledge. A chunk that is merely on
the same topic is not relevant. For each chunk give its doc_index (the number shown), whether
it is relevant, which missing aspect it addresses (empty if none), the passage quoted word for
word (empty if none), and a short reason.

{chunks}"""

EXTRACT_PROMPT = """You are pulling the useful parts out of a web page found for a question.

Question: {query}

Aspects the documentation already covers:
{covered}

Aspects still missing:
{missing}

Copy, word for word, the passages of the page that address a missing aspect, or a covered
aspect where the page gives more specific detail than the documentation. Keep every code block
whole. If no aspects are listed, keep the passages that help answer the question itself. For
each passage, list the aspects it addresses and give a short reason. Set document_useful to
false when nothing on the page helps.

Page url: {url}
Page title: {title}
Page content:
{content}"""


class ChunkGrade(BaseModel):
    doc_index: int
    relevant: bool
    addresses_aspect: str
    verbatim_quote: str
    reasoning: str


class ChunkGrades(BaseModel):
    grades: list[ChunkGrade]


class Extract(BaseModel):
    verbatim_passage: str
    aspects_addressed: list[str]
    reasoning: str


class PageExtraction(BaseModel):
    extracts: list[Extract]
    document_useful: bool


def bullet_list(items: list[str]) -> str:
    if not items:
        return "(none identified)"
    return "\n".join(f"- {item}" for item in items)


class KnowledgeRefinement:
    def __init__(self, llm: Any) -> None:
        self.llm = llm

    def run(
        self,
        query: str,
        aspects: list[dict[str, Any]],
        missing_aspects: list[str],
        relevant_documents: list[Document],
        reretrieval_documents: list[Document],
        web_documents: list[Document],
    ) -> dict[str, Any]:
        models = []

        kept_chunks = []
        if reretrieval_documents:
            kept_chunks, model = self._regrade(query, missing_aspects, reretrieval_documents)
            models.append(model)

        covered = [aspect["aspect"] for aspect in aspects if aspect["covered"]]
        with ThreadPoolExecutor(max_workers=EXTRACT_WORKERS) as executor:
            extractions = list(
                executor.map(
                    lambda doc: self._extract(query, covered, missing_aspects, doc),
                    web_documents,
                )
            )
        models.extend(model for _, model in extractions)
        kept_pages = [page for page, _ in extractions if page is not None]

        logger.info(
            "refine: kept %d of %d re-retrieved chunks and %d of %d web pages",
            len(kept_chunks),
            len(reretrieval_documents),
            len(kept_pages),
            len(web_documents),
        )
        distinct_models = list(dict.fromkeys(models))
        return {
            "refined_context": [*relevant_documents, *kept_chunks, *kept_pages],
            "internal_kept": len(relevant_documents),
            "reretrieval_kept": len(kept_chunks),
            "web_kept": len(kept_pages),
            "model": ", ".join(distinct_models) if distinct_models else None,
        }

    def _regrade(
        self, query: str, missing_aspects: list[str], documents: list[Document]
    ) -> tuple[list[Document], str]:
        chunks = "\n\n".join(
            f"[Document {i}] Source: {doc.meta['source_path']}\n{doc.content}"
            for i, doc in enumerate(documents, start=1)
        )
        prompt = REGRADE_PROMPT.format(
            query=query, missing=bullet_list(missing_aspects), chunks=chunks
        )
        reply, model = self.llm.ask(prompt, ChunkGrades)
        relevant_indexes = {grade.doc_index for grade in reply.grades if grade.relevant}
        kept = [doc for i, doc in enumerate(documents, start=1) if i in relevant_indexes]
        return kept, model

    def _extract(
        self, query: str, covered: list[str], missing_aspects: list[str], doc: Document
    ) -> tuple[Document | None, str]:
        prompt = EXTRACT_PROMPT.format(
            query=query,
            covered=bullet_list(covered),
            missing=bullet_list(missing_aspects),
            url=doc.meta["url"],
            title=doc.meta["title"],
            content=doc.content,
        )
        reply, model = self.llm.ask(prompt, PageExtraction)
        extracts = [extract for extract in reply.extracts if extract.verbatim_passage.strip()]
        if not reply.document_useful or not extracts:
            return None, model
        content = "\n\n".join(
            f"[Extract {i}] Addresses: {', '.join(extract.aspects_addressed)}\n"
            f"{extract.verbatim_passage}"
            for i, extract in enumerate(extracts, start=1)
        )
        page = Document(
            content=content,
            meta={"url": doc.meta["url"], "title": doc.meta["title"]},
            score=doc.score,
        )
        return page, model
