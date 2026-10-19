"""
Answer one question through the CRAG routes: retrieve, grade, then CORRECT, AMBIGUOUS or
INCORRECT, then generate. Every adapter is built by the caller and passed in (D4).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from haystack import Document

logger = logging.getLogger(__name__)

RETRIEVE_TOP_K = 10
NO_DOCUMENTS_ROUTE = "no_documents"
FALLBACK_ANSWER = (
    "Nothing was found to answer this question from. Try rephrasing it with more specific terms."
)


@dataclass
class CragResult:
    question: str
    route: str
    answer: str
    sources: list[dict[str, Any]]
    missing_aspects: list[str]
    steps: list[dict[str, Any]]


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def record_step(
    steps: list[dict[str, Any]],
    step: str,
    started: str,
    counts: dict[str, int],
    model: str | None = None,
) -> None:
    entry: dict[str, Any] = {
        "step": step,
        "started": started,
        "ended": utc_now_iso(),
        "counts": counts,
    }
    if model is not None:
        entry["model"] = model
    steps.append(entry)
    logger.info("%s: %s", step, counts)


def sources_of(documents: list[Document]) -> list[dict[str, Any]]:
    sources = []
    for rank, doc in enumerate(documents, start=1):
        # Indexed chunks carry a "url" too (metadata.attach_metadata); only they have source_path.
        is_internal = "source_path" in doc.meta
        sources.append(
            {
                "rank": rank,
                "origin": "internal" if is_internal else "web",
                "source": doc.meta["source_path"] if is_internal else doc.meta["url"],
                "content": doc.content,
                "score": doc.score,
            }
        )
    return sources


class CragOrchestrator:
    def __init__(
        self,
        retrieve: Callable[[str, int], list[Document]],
        grader: Any,
        decomposer: Any,
        web_search: Any,
        refinement: Any,
        generator: Any,
    ) -> None:
        self.retrieve = retrieve
        self.grader = grader
        self.decomposer = decomposer
        self.web_search = web_search
        self.refinement = refinement
        self.generator = generator

    def run(self, question: str) -> CragResult:
        steps: list[dict[str, Any]] = []

        started = utc_now_iso()
        documents = self.retrieve(question, RETRIEVE_TOP_K)
        record_step(steps, "retrieve", started, {"documents": len(documents)})
        if not documents:
            return self._fallback(question, NO_DOCUMENTS_ROUTE, [], steps)

        started = utc_now_iso()
        graded = self.grader.run(query=question, documents=documents)
        relevant = graded["relevant_documents"]
        record_step(
            steps,
            "grade",
            started,
            {"relevant": len(relevant), "total": len(documents)},
            graded["model"],
        )
        route = graded["decision"]
        missing = graded["missing_aspects"]

        if route == "correct":
            context = relevant
        elif route == "ambiguous":
            context = self._ambiguous(question, documents, graded, steps)
        elif route == "incorrect":
            context = self._incorrect(question, steps)
        else:
            raise ValueError(f"grade: unknown decision {route!r} for question {question!r}")

        if not context:
            return self._fallback(question, route, missing, steps)
        return self._generate(question, route, context, missing, steps)

    def _ambiguous(
        self,
        question: str,
        documents: list[Document],
        graded: dict[str, Any],
        steps: list[dict[str, Any]],
    ) -> list[Document]:
        started = utc_now_iso()
        decomposed = self.decomposer.run(
            query=question,
            missing_aspects=graded["missing_aspects"],
            relevant_documents=graded["relevant_documents"],
        )
        vector_queries = decomposed["vector_queries"]
        web_queries = decomposed["web_queries"]
        record_step(
            steps,
            "decompose",
            started,
            {"vector_queries": len(vector_queries), "web_queries": len(web_queries)},
            decomposed["model"],
        )

        reretrieved = []
        if vector_queries:
            started = utc_now_iso()
            held_ids = {doc.id for doc in documents}
            retrieved = 0
            for query in vector_queries:
                new_documents = self.retrieve(query, RETRIEVE_TOP_K)
                retrieved += len(new_documents)
                for doc in new_documents:
                    if doc.id not in held_ids:
                        held_ids.add(doc.id)
                        reretrieved.append(doc)
            record_step(
                steps,
                "reretrieve",
                started,
                {"queries": len(vector_queries), "retrieved": retrieved, "new": len(reretrieved)},
            )

        web_documents = []
        if web_queries:
            started = utc_now_iso()
            web_documents = self.web_search.run(queries=web_queries)["documents"]
            record_step(steps, "web_search", started, {"documents": len(web_documents)})

        started = utc_now_iso()
        refined = self.refinement.run(
            query=question,
            aspects=graded["aspects"],
            missing_aspects=graded["missing_aspects"],
            relevant_documents=graded["relevant_documents"],
            reretrieval_documents=reretrieved,
            web_documents=web_documents,
        )
        record_step(
            steps,
            "refine",
            started,
            {
                "relevant": len(graded["relevant_documents"]),
                "reretrieved": len(reretrieved),
                "web": len(web_documents),
                "refined": len(refined["refined_context"]),
            },
            refined["model"],
        )
        return refined["refined_context"]

    def _incorrect(self, question: str, steps: list[dict[str, Any]]) -> list[Document]:
        started = utc_now_iso()
        web_documents = self.web_search.run(queries=[question])["documents"]
        record_step(steps, "web_search", started, {"documents": len(web_documents)})
        if not web_documents:
            return []

        started = utc_now_iso()
        refined = self.refinement.run(
            query=question,
            aspects=[],
            missing_aspects=[],
            relevant_documents=[],
            reretrieval_documents=[],
            web_documents=web_documents,
        )
        record_step(
            steps,
            "refine",
            started,
            {"web": len(web_documents), "refined": len(refined["refined_context"])},
            refined["model"],
        )
        return refined["refined_context"]

    def _generate(
        self,
        question: str,
        route: str,
        documents: list[Document],
        missing: list[str],
        steps: list[dict[str, Any]],
    ) -> CragResult:
        sources = sources_of(documents)  # fails before the paid call on a bad document
        started = utc_now_iso()
        generated = self.generator.run(query=question, documents=documents)
        record_step(steps, "generate", started, {"documents": len(documents)}, generated["model"])
        return CragResult(
            question=question,
            route=route,
            answer=generated["answer"],
            sources=sources,
            missing_aspects=missing,
            steps=steps,
        )

    def _fallback(
        self, question: str, route: str, missing: list[str], steps: list[dict[str, Any]]
    ) -> CragResult:
        started = utc_now_iso()
        record_step(steps, "fallback", started, {"documents": 0})
        logger.warning("no documents to answer from, route %s: %r", route, question)
        return CragResult(
            question=question,
            route=route,
            answer=FALLBACK_ANSWER,
            sources=[],
            missing_aspects=missing,
            steps=steps,
        )
