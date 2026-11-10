"""
Hybrid retrieval: BM25 matches exact words (API names) that dense search can rank low, and
Voyage reranks the fused lists. CRAG imports ``build_hybrid_pipeline`` and ``retrieve`` (C011).
"""

import argparse
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from haystack import Document, Pipeline
from haystack.components.joiners import DocumentJoiner
from haystack.components.retrievers.in_memory import InMemoryBM25Retriever
from haystack.document_stores.in_memory import InMemoryDocumentStore
from haystack_integrations.components.rankers.voyage import VoyageRanker
from haystack_integrations.components.retrievers.qdrant import QdrantEmbeddingRetriever

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))  # lets the script run by path from any directory

from data_ingestion.chunk_snapshot import read_chunks  # noqa: E402  (needs the path set above)
from data_ingestion.chunkers import STRATEGIES  # noqa: E402
from data_ingestion.embedders import (  # noqa: E402
    EMBEDDERS,
    build_query_embedder,
    cached_tokenizer_path,
)
from data_ingestion.pipeline import resolve_run_dir  # noqa: E402
from data_ingestion.qdrant_store import (  # noqa: E402
    build_document_store,
    collection_exists,
    load_settings,
)
from data_ingestion.variants import (  # noqa: E402
    DEFAULT_PREFIX,
    Variant,
    check_prefix,
    select_variants,
)

logger = logging.getLogger(__name__)

DEFAULT_CHUNKS_RUN = "full_chunks_20261015"
CANDIDATES = 50  # from each retriever, before fusion
TOP_K = 10
RERANK_MODEL = "rerank-2.5"


def build_hybrid_pipeline(
    variant: Variant,
    *,
    chunks_run: str = DEFAULT_CHUNKS_RUN,
    prefix: str = DEFAULT_PREFIX,
    candidates: int = CANDIDATES,
    top_k: int = TOP_K,
) -> Pipeline:
    """Build the BM25 + dense pipeline for ``variant``; makes one free Qdrant call, no paid one.

    Raises ``ValueError`` for missing config, chunk file or collection, which ``main`` exits 1 on.
    """
    settings = load_settings(
        collection_override=variant.collection(prefix),
        dimension_override=variant.embedder.dimension,
    )
    try:
        chunks = read_chunks(resolve_run_dir(chunks_run), variant.strategy)
    except FileNotFoundError as exc:
        raise ValueError(
            f"Chunk run {chunks_run} has no {variant.strategy} chunks: {exc.filename} not found"
        ) from None
    # The Haystack store creates a missing collection when it first connects, so a
    # mistyped prefix would otherwise search a new, empty collection.
    if not collection_exists(settings):
        raise ValueError(
            f"Collection {settings.collection} does not exist; index it with stage 3 first"
        )

    # Stage 3's point key (D34), so fusion merges a chunk that both retrievers return.
    keyed_chunks = [
        replace(
            chunk,
            id=f"{variant.strategy}:{variant.embedder.name}:"
            f"{chunk.meta['source_path']}:{chunk.meta['chunk_index']}",
        )
        for chunk in chunks
    ]
    chunk_store = InMemoryDocumentStore()
    chunk_store.write_documents(keyed_chunks)

    pipeline = Pipeline(
        metadata={"collection": settings.collection, "candidates": candidates, "top_k": top_k}
    )
    pipeline.add_component(
        "bm25", InMemoryBM25Retriever(document_store=chunk_store, top_k=candidates)
    )
    pipeline.add_component("query_embedder", build_query_embedder(variant.embedder.name))
    pipeline.add_component(
        "dense",
        QdrantEmbeddingRetriever(document_store=build_document_store(settings), top_k=candidates),
    )
    pipeline.add_component("joiner", DocumentJoiner(join_mode="reciprocal_rank_fusion"))
    pipeline.add_component("ranker", VoyageRanker(model=RERANK_MODEL, top_k=top_k))
    pipeline.connect("query_embedder.embedding", "dense.query_embedding")
    pipeline.connect("bm25.documents", "joiner.documents")
    pipeline.connect("dense.documents", "joiner.documents")
    pipeline.connect("joiner.documents", "ranker.documents")
    return pipeline


def _run_pipeline(
    pipeline: Pipeline, query: str, top_k: int | None, include_outputs_from: set[str]
) -> dict[str, Any]:
    inputs = {
        "bm25": {"query": query},
        "query_embedder": {"text": query},
        "ranker": {"query": query, "top_k": top_k},
    }
    return pipeline.run(inputs, include_outputs_from=include_outputs_from)


def retrieve(pipeline: Pipeline, query: str, top_k: int | None = None) -> list[Document]:
    """The reranked chunks for ``query``, best first; a ``top_k`` given wins for this call."""
    return _run_pipeline(pipeline, query, top_k, set())["ranker"]["documents"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search one indexed strategy × embedder variant with BM25 and dense "
        "retrieval, fused and reranked. Embedding the question and reranking are PAID "
        "Voyage calls.",
    )
    parser.add_argument("--question", required=True, help="The question to search for")
    parser.add_argument(
        "--strategy",
        default="markdown_ast",
        help=f"One of: {', '.join(STRATEGIES)} (default: %(default)s)",
    )
    parser.add_argument(
        "--embedder",
        default="voyage",
        help=f"One of: {', '.join(EMBEDDERS)} (default: %(default)s; bge needs "
        "OPENROUTER_API_KEY; reranking always needs VOYAGE_API_KEY)",
    )
    parser.add_argument(
        "--chunks-run",
        default=DEFAULT_CHUNKS_RUN,
        help="The stage 2 run whose chunks BM25 searches; it must be the run the "
        "collection was indexed from (default: %(default)s)",
    )
    parser.add_argument(
        "--collection-prefix",
        default=DEFAULT_PREFIX,
        help="Collections are named <prefix>__<strategy>__<embedder> (default: %(default)s)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=TOP_K,
        help="Chunks returned after reranking (default: %(default)s)",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Also log the BM25 and dense lists and the ids found in both",
    )
    args = parser.parse_args(argv)
    # BM25 refuses an empty query only inside the run, after the paid query embedding.
    if not args.question.strip():
        parser.error("--question must not be empty")
    if "all" in (args.strategy, args.embedder):
        parser.error("--strategy and --embedder take one name each, not 'all'")
    try:
        select_variants(args.strategy, args.embedder)  # names every unknown name at once (C008)
        check_prefix(args.collection_prefix)
        resolve_run_dir(args.chunks_run)
    except ValueError as exc:
        parser.error(str(exc))
    if args.top_k < 1:
        parser.error(f"--top-k must be at least 1; got {args.top_k}")
    return args


def log_documents(label: str, documents: list[Document]) -> None:
    logger.info("%s: %d chunks", label, len(documents))
    for rank, doc in enumerate(documents, start=1):
        logger.info("#%d  score %.4f  %s  %s", rank, doc.score, doc.id, doc.meta["source_path"])


def _check_prerequisites(embedder: str) -> None:
    # The ranker reads its key only when it first runs, after the paid query embedding.
    if not os.getenv("VOYAGE_API_KEY"):
        logger.error("VOYAGE_API_KEY is not set; reranking needs it. Set it in .env")
        raise SystemExit(1)
    if embedder == "bge" and not os.getenv("OPENROUTER_API_KEY"):
        logger.error("OPENROUTER_API_KEY is not set; the bge query embedder needs it")
        raise SystemExit(1)
    # BGE's query embedder cuts the question to 512 tokens with its local tokenizer.
    if embedder == "bge" and cached_tokenizer_path("bge") is None:
        logger.error(
            "BGE's tokenizer is not in the project model cache. "
            "Run 'uv run python scripts/download_models.py' from the project root first."
        )
        raise SystemExit(1)


def _retrieve_and_log_lists(pipeline: Pipeline, question: str) -> list[Document]:
    outputs = _run_pipeline(pipeline, question, None, {"bm25", "dense"})
    bm25 = outputs["bm25"]["documents"]
    dense = outputs["dense"]["documents"]
    log_documents("BM25", bm25)
    log_documents("Dense", dense)
    dense_ids = {doc.id for doc in dense}
    in_both = [doc.id for doc in bm25 if doc.id in dense_ids]
    logger.info("In both lists: %d chunks: %s", len(in_both), ", ".join(in_both))
    return outputs["ranker"]["documents"]


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    # Configure once from the entry point, in the format the stages use.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    # httpx (under the Qdrant, OpenAI and Voyage clients) logs one INFO line per request.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv(PROJECT_ROOT / ".env")

    _check_prerequisites(args.embedder)

    [variant] = select_variants(args.strategy, args.embedder)
    try:
        pipeline = build_hybrid_pipeline(
            variant,
            chunks_run=args.chunks_run,
            prefix=args.collection_prefix,
            top_k=args.top_k,
        )
    except ValueError as exc:  # missing Qdrant config, chunk file or collection
        logger.error("%s", exc)
        raise SystemExit(1) from None

    logger.info("%s: %r", pipeline.metadata["collection"], args.question)
    if args.compare:
        reranked = _retrieve_and_log_lists(pipeline, args.question)
    else:
        reranked = retrieve(pipeline, args.question)
    log_documents("Reranked", reranked)


if __name__ == "__main__":
    main()
