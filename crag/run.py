"""
Answer a question through the CRAG routes from the command line, or check the route each of a
few fixed questions takes. Every run makes PAID calls to Voyage, Gemini and Tavily.
"""

import argparse
import dataclasses
import json
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from haystack import Document
from tavily import TavilyClient

from crag.decomposer import QueryDecomposer
from crag.generator import AnswerGenerator
from crag.grader import DocumentGrader
from crag.llm import FALLBACK_MODEL, StructuredLLM
from crag.orchestrator import CragOrchestrator
from crag.refinement import KnowledgeRefinement
from crag.web_search import WebSearch
from data_ingestion.variants import select_variants
from retrieval import hybrid
from retrieval.baseline_rag import build_rag_pipeline

PROJECT_ROOT = Path(__file__).resolve().parents[1]

logger = logging.getLogger(__name__)

STRATEGY = "markdown_ast"
EMBEDDER = "voyage"
REQUIRED_SETTINGS = (
    "GOOGLE_API_KEY",
    "TAVILY_API_KEY",
    "VOYAGE_API_KEY",
    "GEMINI_MODEL",
    "GEMINI_LIGHT_MODEL",
)

# The routes are LLM judgments, so a mismatch is reported, not raised.
ROUTE_CHECK = (
    (
        "How do I read a custom request header such as X-Token in a path operation?",
        "correct",
        "covered by the header-parameters tutorial",
    ),
    (
        "How do I accept an uploaded file in a FastAPI endpoint?",
        "correct",
        "covered by the request-files tutorial",
    ),
    (
        "How do I let a frontend on another origin call my API?",
        "correct",
        "covered by the CORS tutorial",
    ),
    (
        "How do I set a cookie on the response from a path operation?",
        "correct",
        "covered by the response-cookies page",
    ),
    (
        "How do I protect an endpoint with HTTP Basic auth and lock an account after five failed "
        "password attempts?",
        "ambiguous",
        "HTTP Basic auth is documented; account lockout is not",
    ),
    (
        "How do I stream a large file with StreamingResponse while reading it from an Amazon S3 "
        "bucket with boto3?",
        "ambiguous",
        "streaming responses are documented; S3 and boto3 are not",
    ),
    (
        "How do I send push notifications to iOS devices through Apple's APNs service?",
        "incorrect",
        "the docs don't cover iOS or APNs",
    ),
    (
        "How do I export Prometheus metrics and OpenTelemetry traces from a FastAPI app?",
        "incorrect",
        "the docs don't cover Prometheus or OpenTelemetry",
    ),
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Answer a FastAPI question through the CRAG routes. Every run makes PAID "
        "calls to Voyage, Gemini and Tavily.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--question", help="The question to answer")
    mode.add_argument(
        "--route-check",
        action="store_true",
        help=f"Run the {len(ROUTE_CHECK)} fixed questions and compare each route to the "
        "expected one",
    )
    parser.add_argument(
        "--retriever",
        choices=("hybrid", "dense"),
        default="hybrid",
        help="hybrid: BM25 + dense, fused and reranked; dense: Qdrant only (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    # BM25 refuses an empty query only inside the run, after the paid query embedding.
    if args.question is not None and not args.question.strip():
        parser.error("--question must not be empty")
    return args


def run_route_check(crag: CragOrchestrator) -> None:
    summaries = []
    matched = 0
    for question, expected, why in ROUTE_CHECK:
        started = time.monotonic()
        result = crag.run(question)
        seconds = time.monotonic() - started
        logger.info("%s", json.dumps(dataclasses.asdict(result), indent=2))
        models = ", ".join(
            f"{step['step']}={step['model']}" for step in result.steps if "model" in step
        )
        matched += result.route == expected
        summaries.append(
            f"{'match' if result.route == expected else 'MISMATCH'}: expected {expected} "
            f"({why}), got {result.route}; models {models or 'none'}; {seconds:.1f} s; "
            f"{question!r}"
        )

    for summary in summaries:
        logger.info("%s", summary)
    logger.info("route check: %d of %d routes as expected", matched, len(summaries))


def configure_logging() -> None:
    # Configure once from the entry point, in the format the stages use.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    # httpx (under the Qdrant, Voyage, Gemini and Tavily clients) logs one INFO line per request.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def exit_if_settings_missing() -> None:
    missing = [name for name in REQUIRED_SETTINGS if not os.getenv(name)]
    if missing:
        logger.error("Missing settings: %s; set them in .env", ", ".join(missing))
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    configure_logging()
    load_dotenv(PROJECT_ROOT / ".env")
    exit_if_settings_missing()

    pro_fallback = os.getenv("GEMINI_FALLBACK_MODEL") or FALLBACK_MODEL
    light_fallback = os.getenv("GEMINI_LIGHT_FALLBACK_MODEL") or FALLBACK_MODEL

    [variant] = select_variants(STRATEGY, EMBEDDER)
    try:
        if args.retriever == "hybrid":
            pipeline = hybrid.build_hybrid_pipeline(variant)
        else:
            pipeline = build_rag_pipeline(variant, retrieve_only=True)
    except ValueError as exc:  # a missing collection or chunk run, or missing Qdrant config
        logger.error("%s", exc)
        raise SystemExit(1) from None

    def retrieve(query: str, top_k: int) -> list[Document]:
        if args.retriever == "hybrid":
            return hybrid.retrieve(pipeline, query, top_k)
        outputs = pipeline.run({"query_embedder": {"text": query}, "retriever": {"top_k": top_k}})
        return outputs["retriever"]["documents"]

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    pro = StructuredLLM(client, primary=os.environ["GEMINI_MODEL"], fallback=pro_fallback)
    light = StructuredLLM(client, primary=os.environ["GEMINI_LIGHT_MODEL"], fallback=light_fallback)

    crag = CragOrchestrator(
        retrieve,
        DocumentGrader(pro),
        QueryDecomposer(pro),
        WebSearch(TavilyClient(api_key=os.environ["TAVILY_API_KEY"])),
        KnowledgeRefinement(light),
        AnswerGenerator(pro),
    )

    if args.route_check:
        run_route_check(crag)
    else:
        result = crag.run(args.question)
        logger.info("%s", json.dumps(dataclasses.asdict(result), indent=2))


if __name__ == "__main__":
    main()
