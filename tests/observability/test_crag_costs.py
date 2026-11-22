import json
import logging
import socket
from collections import Counter
from threading import Barrier, get_ident
from types import SimpleNamespace

import pytest
from google.genai import errors, types
from haystack import Document
from haystack.core.errors import PipelineRuntimeError
from haystack.telemetry import _telemetry as haystack_telemetry
from haystack_integrations.document_stores.qdrant import QdrantDocumentStore
from tenacity import wait_none

from crag import llm, run
from crag.decomposer import Decomposition
from crag.generator import GeneratedAnswer
from crag.grader import CoverageReply, RelevanceReply
from crag.refinement import ChunkGrades, PageExtraction
from data_ingestion import qdrant_store
from data_ingestion.chunk_snapshot import write_chunks
from retrieval import hybrid, voyage_usage


@pytest.fixture
def providers(monkeypatch, tmp_path):
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(haystack_telemetry, "telemetry", None)
    monkeypatch.setattr(run, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(qdrant_store, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(logging, "basicConfig", lambda *a, **k: None)
    monkeypatch.setattr(logging.getLogger("httpx"), "setLevel", lambda level: None)
    monkeypatch.setattr(llm.StructuredLLM._generate.retry, "wait", wait_none())
    for name in run.REQUIRED_SETTINGS:
        monkeypatch.setenv(name, "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-pro-latest")
    monkeypatch.setenv("GEMINI_LIGHT_MODEL", "gemini-flash-latest")
    monkeypatch.delenv("GEMINI_FALLBACK_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_LIGHT_FALLBACK_MODEL", raising=False)
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.invalid:6333")
    monkeypatch.setenv("QDRANT_API_KEY", "test-key")
    monkeypatch.delenv("QDRANT_COLLECTION", raising=False)
    monkeypatch.delenv("EMBEDDING_DIMENSION", raising=False)
    state = SimpleNamespace(
        directory=tmp_path / "costs",
        calls=[],
        route="ambiguous",
        retry_first=False,
        fail_generation=False,
        fail_extraction=False,
        extraction_threads=set(),
    )
    chunks = [
        Document(content="private path parameters", meta={"source_path": "a.md", "chunk_index": 0}),
        Document(content="private extra details", meta={"source_path": "b.md", "chunk_index": 0}),
    ]
    chunk_dir = tmp_path / "chunks"
    write_chunks(chunk_dir, "markdown_ast", chunks)
    monkeypatch.setattr(hybrid, "resolve_run_dir", lambda run_id: chunk_dir)
    monkeypatch.setattr(hybrid, "collection_exists", lambda settings: True)
    store = QdrantDocumentStore(location=":memory:", index="cost_test", embedding_dim=3)
    store.write_documents(
        [Document(content=doc.content, meta=doc.meta, embedding=[1.0, 0.0, 0.0]) for doc in chunks]
    )
    monkeypatch.setattr(hybrid, "build_document_store", lambda settings: store)

    def embed(**kwargs):
        assert (state.directory / "calls.jsonl").exists()
        state.calls.append(("voyage", "embedding", kwargs["texts"][0]))
        return SimpleNamespace(embeddings=[[1.0, 0.0, 0.0]], total_tokens=7)

    def rerank(**kwargs):
        state.calls.append(("voyage", "rerank", kwargs["query"]))
        wanted = (
            "private path parameters"
            if kwargs["query"] == "private question"
            else "private extra details"
        )
        index = kwargs["documents"].index(wanted)
        return SimpleNamespace(
            results=[SimpleNamespace(index=index, relevance_score=0.9)], total_tokens=20
        )

    monkeypatch.setattr(
        voyage_usage, "Client", lambda **kwargs: SimpleNamespace(embed=embed, rerank=rerank)
    )
    barrier = Barrier(3, timeout=10)

    def generate_content(*, model, contents, config):
        schema = config["response_schema"]
        state.calls.append(("gemini", schema.__name__, model))
        if schema is GeneratedAnswer and state.fail_generation:
            raise errors.ClientError(400, {"error": {"message": "private provider error"}})
        if schema is RelevanceReply:
            body = {
                "grades": [
                    {
                        "doc_index": 1,
                        "reasoning": "private reason",
                        "verbatim_quote": "private quote",
                        "verdict": "not_relevant" if state.route == "incorrect" else "relevant",
                    }
                ]
            }
        elif schema is CoverageReply:
            body = {
                "aspects": [
                    {
                        "aspect": "missing detail",
                        "covered": state.route == "correct",
                        "verbatim_quote": "",
                        "source_doc": 0,
                    }
                ],
                "all_covered": state.route == "correct",
            }
        elif schema is Decomposition:
            body = {
                "analyses": [
                    {
                        "missing_aspect": "missing detail",
                        "user_need": "detail",
                        "retrieval_gap_reason": "missing",
                        "target_document_type": "tutorial",
                        "vector_query": f"extra {i}",
                        "web_query": f"web {i}",
                    }
                    for i in range(2)
                ]
            }
        elif schema is ChunkGrades:
            body = {
                "grades": [
                    {
                        "doc_index": 1,
                        "relevant": True,
                        "addresses_aspect": "missing detail",
                        "verbatim_quote": "private extra details",
                        "reasoning": "useful",
                    }
                ]
            }
        elif schema is PageExtraction:
            state.extraction_threads.add(get_ident())
            barrier.wait()
            if state.fail_extraction and "https://example.com/0" in contents:
                raise errors.ClientError(400, {"error": {"message": "private extraction error"}})
            body = {
                "extracts": [
                    {
                        "verbatim_passage": "private extract",
                        "aspects_addressed": ["missing detail"],
                        "reasoning": "useful",
                    }
                ],
                "document_useful": True,
            }
        elif schema is GeneratedAnswer:
            body = {"answer": "private answer [1]"}
        else:
            raise AssertionError(f"Unexpected schema {schema}")
        text = json.dumps(body)
        if state.retry_first and schema is RelevanceReply and model == "gemini-pro-latest":
            text = "invalid JSON"
        version = {
            "gemini-pro-latest": "gemini-3.1-pro-preview",
            "gemini-flash-latest": "gemini-3.8-flash",
            llm.FALLBACK_MODEL: "gemini-3.5-flash-lite",
        }[model]
        return types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(parts=[types.Part(text=text)]))],
            model_version=version,
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=100,
                cached_content_token_count=20,
                candidates_token_count=10,
                thoughts_token_count=5,
                total_token_count=115,
            ),
        )

    def search(**kwargs):
        state.calls.append(("tavily", "search", kwargs["query"]))
        assert kwargs["include_usage"] is True
        return {
            "results": [
                {
                    "url": f"https://example.com/{i}",
                    "title": "private title",
                    "raw_content": "private web page",
                    "score": 0.8,
                }
                for i in range(3)
            ],
            "usage": {"credits": 2},
        }

    monkeypatch.setattr(
        run.genai,
        "Client",
        lambda **kwargs: SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)),
    )
    monkeypatch.setattr(run, "TavilyClient", lambda **kwargs: SimpleNamespace(search=search))
    yield state
    store.close()


@pytest.mark.parametrize(
    "route, counts, total",
    [
        ("correct", {"voyage": 2, "gemini": 3}, 0.00103314),
        ("incorrect", {"voyage": 2, "gemini": 5, "tavily": 1}, 0.01704239),
        ("ambiguous", {"voyage": 6, "gemini": 8, "tavily": 2}, 0.03385042),
    ],
)
def test_recorded_crag_routes_use_real_hybrid_and_concurrent_refinement(
    providers, caplog, route, counts, total
):
    providers.route = route
    caplog.set_level(logging.INFO, logger=run.__name__)
    run.main(["--question", "private question", "--costs-dir", str(providers.directory)])

    text = (providers.directory / "calls.jsonl").read_text()
    rows = [json.loads(line) for line in text.splitlines()]
    summary = json.loads((providers.directory / "summary.json").read_text())
    assert Counter(row["provider"] for row in rows) == counts
    assert len(rows) == len(providers.calls) == summary["call_count"]
    assert summary["known_estimated_usd"] == pytest.approx(total)
    assert sum(row["usd"] for row in rows) == pytest.approx(total)
    assert summary["unknown_cost_count"] == 0
    assert summary["run_failed"] is False
    assert summary["all_observed_costs_known"] is True
    assert "private" not in text
    assert all(
        set(row) == {"provider", "operation", "model", "outcome", "usage", "usd", "reason"}
        for row in rows
    )
    if route != "correct":
        assert len(providers.extraction_threads) == 3
    result = json.loads(
        next(record.message for record in caplog.records if record.name == run.__name__)
    )
    assert result["route"] == route and result["answer"] == "private answer [1]"


def test_cli_records_each_invalid_primary_attempt_and_fallback(providers):
    providers.retry_first = True
    run.main(["--question", "private question", "--costs-dir", str(providers.directory)])
    rows = [
        json.loads(line) for line in (providers.directory / "calls.jsonl").read_text().splitlines()
    ]
    summary = json.loads((providers.directory / "summary.json").read_text())
    assert len(rows) == len(providers.calls) == summary["call_count"] == 19
    assert Counter(row["model"] for row in rows if row["provider"] == "gemini") == {
        "gemini-3.1-pro-preview": 6,
        "gemini-3.5-flash-lite": 1,
        "gemini-3.8-flash": 4,
    }
    assert summary["known_estimated_usd"] == pytest.approx(0.03460052)
    assert summary["unknown_cost_count"] == 0
    assert summary["run_failed"] is False


@pytest.mark.parametrize("failure, count", [("fail_generation", 16), ("fail_extraction", 15)])
def test_cli_finalizes_failed_runs_after_refinement_workers_finish(providers, failure, count):
    setattr(providers, failure, True)
    with pytest.raises(errors.ClientError, match="private"):
        run.main(["--question", "private question", "--costs-dir", str(providers.directory)])
    text = (providers.directory / "calls.jsonl").read_text()
    rows = [json.loads(line) for line in text.splitlines()]
    summary = json.loads((providers.directory / "summary.json").read_text())
    assert summary["call_count"] == len(rows) == len(providers.calls) == count
    assert summary["run_failed"] is True
    assert summary["unknown_cost_count"] == 1
    assert summary["all_observed_costs_known"] is False
    assert summary["known_estimated_usd"] == pytest.approx(sum(row["usd"] or 0 for row in rows))
    assert len(providers.extraction_threads) == 3
    assert len([row for row in rows if row["outcome"] == "error"]) == 1
    assert "private" not in text


def test_cli_record_failure_does_not_repeat_the_paid_embedding(providers, monkeypatch):
    original_create = run.create_recorder

    def create_failing_recorder(directory):
        _, finish = original_create(directory)

        def fail_record(**kwargs):
            raise OSError("cost append failed")

        return fail_record, finish

    monkeypatch.setattr(run, "create_recorder", create_failing_recorder)
    with pytest.raises(PipelineRuntimeError, match="cost append failed"):
        run.main(["--question", "private question", "--costs-dir", str(providers.directory)])
    assert providers.calls == [("voyage", "embedding", "private question")]
    summary = json.loads((providers.directory / "summary.json").read_text())
    assert summary["run_failed"] is True
