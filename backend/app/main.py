"""
Run from the project root: ``uv run uvicorn backend.app.main:app``. Every query makes PAID calls
to Voyage, Gemini and Tavily.
"""

import dataclasses
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from google import genai
from tavily import TavilyClient

from backend.app.models import QueryRequest, QueryResponse
from crag.decomposer import QueryDecomposer
from crag.generator import AnswerGenerator
from crag.grader import DocumentGrader
from crag.llm import FALLBACK_MODEL, StructuredLLM
from crag.orchestrator import CragOrchestrator
from crag.refinement import KnowledgeRefinement
from crag.web_search import WebSearch
from data_ingestion.variants import select_variants
from retrieval import hybrid

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STRATEGY = "markdown_ast"
EMBEDDER = "voyage"
# VOYAGE_API_KEY too: the query embedder and the reranker need it.
REQUIRED_SETTINGS = (
    "GOOGLE_API_KEY",
    "TAVILY_API_KEY",
    "GEMINI_MODEL",
    "GEMINI_LIGHT_MODEL",
    "VOYAGE_API_KEY",
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Same wiring as crag/run.py's main, on purpose while Q09 (factory functions) is open.
    load_dotenv(PROJECT_ROOT / ".env")
    missing = [name for name in REQUIRED_SETTINGS if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"startup: missing settings {', '.join(missing)}; set them in .env")

    pro_fallback = os.getenv("GEMINI_FALLBACK_MODEL") or FALLBACK_MODEL
    light_fallback = os.getenv("GEMINI_LIGHT_FALLBACK_MODEL") or FALLBACK_MODEL

    [variant] = select_variants(STRATEGY, EMBEDDER)
    pipeline = hybrid.build_hybrid_pipeline(variant)

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    pro = StructuredLLM(client, primary=os.environ["GEMINI_MODEL"], fallback=pro_fallback)
    light = StructuredLLM(client, primary=os.environ["GEMINI_LIGHT_MODEL"], fallback=light_fallback)

    app.state.crag = CragOrchestrator(
        lambda query, top_k: hybrid.retrieve(pipeline, query, top_k),
        DocumentGrader(pro),
        QueryDecomposer(pro),
        WebSearch(TavilyClient(api_key=os.environ["TAVILY_API_KEY"])),
        KnowledgeRefinement(light),
        AnswerGenerator(pro),
    )
    logger.info("CRAG ready on %s", pipeline.metadata["collection"])
    yield


app = FastAPI(title="Adaptive RAG for the FastAPI docs", lifespan=lifespan)


def get_crag(request: Request) -> CragOrchestrator:
    return request.app.state.crag


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/query")
def query(
    body: QueryRequest, crag: Annotated[CragOrchestrator, Depends(get_crag)]
) -> QueryResponse:
    crag_result = crag.run(body.question)
    return QueryResponse(**dataclasses.asdict(crag_result))
