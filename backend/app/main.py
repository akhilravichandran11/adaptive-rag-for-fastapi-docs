"""
Run from the project root: ``uv run uvicorn backend.app.main:app``. Every query makes PAID calls
to Voyage, Gemini and Tavily.
"""

import dataclasses
import json
import logging
import os
import re
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from starlette.exceptions import HTTPException
from tavily import TavilyClient

from backend.app.models import QueryRequest, QueryResponse
from crag.decomposer import QueryDecomposer
from crag.generator import AnswerGenerator
from crag.grader import DocumentGrader
from crag.llm import FALLBACK_MODEL, StructuredLLM
from crag.orchestrator import CragOrchestrator, utc_now_iso
from crag.refinement import KnowledgeRefinement
from crag.web_search import WebSearch
from data_ingestion.variants import select_variants
from retrieval import hybrid

logger = logging.getLogger(__name__)
run_logger = logging.getLogger("crag_run")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = PROJECT_ROOT / "frontend"
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
    handler = logging.StreamHandler(sys.stdout)
    run_logger.setLevel(logging.INFO)
    run_logger.propagate = False
    run_logger.addHandler(handler)
    try:
        yield
    finally:
        run_logger.removeHandler(handler)
        handler.close()


app = FastAPI(title="Adaptive RAG for the FastAPI docs", lifespan=lifespan)


def get_crag(request: Request) -> CragOrchestrator:
    return request.app.state.crag


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
def query(
    body: QueryRequest,
    request: Request,
    response: Response,
    crag: Annotated[CragOrchestrator, Depends(get_crag)],
) -> QueryResponse | Response:
    request_id = str(uuid4())
    response.headers["X-Request-ID"] = request_id
    fields = {"message": "crag_run", "request_id": request_id}
    follow_id = request.headers.get("X-CRAG-Follow-ID", "")
    if re.fullmatch(r"crg-[0-9a-f]{32}", follow_id):
        fields["follow_id"] = follow_id

    def emit(stage: str, outcome: str) -> None:
        run_logger.info(
            json.dumps({**fields, "event_time": utc_now_iso(), "stage": stage, "outcome": outcome})
        )

    def start_stage(name: str) -> None:
        nonlocal stage
        stage = name
        emit(stage, "started")

    stage = "query"
    emit(stage, "started")
    try:
        crag_result = crag.run(body.question, on_stage=start_stage)
        stage = "query"
        result = QueryResponse(**dataclasses.asdict(crag_result))
    except HTTPException as exc:
        emit(stage, "failed")
        exc.headers = {**(exc.headers or {}), "X-Request-ID": request_id}
        raise
    except Exception:  # noqa: BLE001 — provider errors can carry prompts or credentials.
        emit(stage, "failed")
        return PlainTextResponse(
            "Internal Server Error", status_code=500, headers={"X-Request-ID": request_id}
        )
    emit("query", "succeeded")
    return result


# Mounted last: a mount at "/" only gets the requests no route above matched.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
