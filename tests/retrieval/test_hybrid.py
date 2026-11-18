"""
Tests ``retrieval/hybrid.py`` at its agreed seams with no network call (C006): the real
components, except fakes for the paid embedder and ranker and an in-memory Qdrant store.
"""

import dataclasses
import importlib.util
import logging
import socket
import sys
from pathlib import Path

import pytest
from haystack import Document, component
from haystack.components.joiners import DocumentJoiner
from haystack.components.retrievers.in_memory import InMemoryBM25Retriever
from haystack.telemetry import _telemetry as haystack_telemetry
from haystack_integrations.components.rankers.voyage import VoyageRanker
from haystack_integrations.components.retrievers.qdrant import QdrantEmbeddingRetriever
from haystack_integrations.document_stores.qdrant import QdrantDocumentStore

from data_ingestion import qdrant_store
from data_ingestion.chunk_snapshot import write_chunks
from data_ingestion.embedders import EMBEDDERS, build_query_embedder
from data_ingestion.stage3_index_chunks import point_key
from data_ingestion.variants import Variant
from retrieval import hybrid

PROJECT_ROOT = Path(__file__).resolve().parents[2]

VARIANT = Variant("markdown_ast", EMBEDDERS["voyage"])
COLLECTION = "fastapi_docs__markdown_ast__voyage"
CHUNKS_RUN = "chunks_test"
QUESTION = "How do I declare path parameters?"

# The chunk run's chunks, as stage 2 saves them. The fake query vector is closest to
# the first, then the second, and orthogonal to the other two; BM25 finds the words
# "path" and "parameters" in the first and third.
CHUNKS = [
    Document(
        content="Path parameters are declared in the path string.",
        meta={"source_path": "tutorial/path-params.md", "chunk_index": 0, "bucket": "tutorial"},
    ),
    Document(
        content="Their type is declared with a standard type annotation.",
        meta={"source_path": "tutorial/path-params.md", "chunk_index": 1, "bucket": "tutorial"},
    ),
    Document(
        content="Query parameters are the other function parameters.",
        meta={"source_path": "tutorial/query-params.md", "chunk_index": 0, "bucket": "tutorial"},
    ),
    Document(
        content="A request body is data sent by the client to your API.",
        meta={"source_path": "tutorial/body.md", "chunk_index": 0, "bucket": "tutorial"},
    ),
]
EMBEDDINGS = [[1.0, 0.0, 0.0], [0.8, 0.6, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test on any outgoing connection (C006), and send no Haystack telemetry.

    Every ``Pipeline.run`` queues a telemetry event that a background thread sends,
    where this guard's error is swallowed and retried after the test.
    """
    monkeypatch.setattr(haystack_telemetry, "telemetry", None)

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the real .env, the shell's variables and the root logger out of every test.

    ``main`` configures the root logger with ``force=True``, which would remove
    pytest's log capture, and quiets ``httpx`` for the rest of the session.
    """
    monkeypatch.setattr(qdrant_store, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(hybrid, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(logging, "basicConfig", lambda *args, **kwargs: None)
    monkeypatch.setattr(logging.getLogger("httpx"), "setLevel", lambda level: None)
    for name in ("QDRANT_COLLECTION", "EMBEDDING_DIMENSION", "QDRANT_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.invalid:6333")
    monkeypatch.setenv("QDRANT_API_KEY", "test-key")
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def chunk_runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Run folders live under a temporary folder; ``CHUNKS_RUN`` holds ``CHUNKS``.

    Returns the folder that stands in for ``data_ingestion/outputs``.
    """
    outputs = tmp_path / "outputs"
    write_chunks(outputs / CHUNKS_RUN, VARIANT.strategy, CHUNKS)
    real_resolve = hybrid.resolve_run_dir

    def resolve(run_id: str) -> Path:
        real_resolve(run_id)  # keeps its check of the name
        return outputs / run_id

    monkeypatch.setattr(hybrid, "resolve_run_dir", resolve)
    return outputs


@pytest.fixture(autouse=True)
def existence_checks(monkeypatch: pytest.MonkeyPatch) -> list:
    """Every collection exists; returns the settings each ``collection_exists`` call got."""
    checked: list = []

    def exists(settings):
        checked.append(settings)
        return True

    monkeypatch.setattr(hybrid, "collection_exists", exists)
    return checked


@component
class FakeTextEmbedder:
    """Returns one fixed vector and records the text it was given."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    @component.output_types(embedding=list[float])
    def run(self, text: str) -> dict[str, list[float]]:
        self.texts.append(text)
        return {"embedding": [1.0, 0.0, 0.0]}


@component
class FakeRanker:
    """Copies the ``VoyageRanker`` behaviour ``retrieve`` relies on, so the tests can fail:
    scored copies best first, ``top_k`` from the run over the constructor's, ``top_k`` < 1 refused.
    """

    built: list["FakeRanker"] = []

    def __init__(self, *, model: str, top_k: int | None = None) -> None:
        self.model = model
        self.top_k = top_k
        self.received: list[Document] = []
        FakeRanker.built.append(self)

    @component.output_types(documents=list[Document])
    def run(
        self, query: str, documents: list[Document], top_k: int | None = None
    ) -> dict[str, list[Document]]:
        top_k = top_k if top_k is not None else self.top_k
        if top_k is not None and top_k <= 0:
            raise ValueError(f"top_k must be > 0, but got {top_k}")
        self.received = documents
        words = set(query.lower().rstrip("?").split())
        scored = [
            dataclasses.replace(doc, score=float(len(words & set(doc.content.lower().split()))))
            for doc in documents
        ]
        scored.sort(key=lambda doc: doc.score, reverse=True)
        return {"documents": scored[:top_k]}


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch):
    """Fake query embedder and ranker, and an in-memory Qdrant store holding the chunks
    as stage 3 writes them (its point keys, its extra payload).

    Returns the fake embedder, which records the texts it was asked to embed.
    """
    store = QdrantDocumentStore(location=":memory:", index=COLLECTION, embedding_dim=3)
    store.write_documents(
        [
            Document(
                id=point_key(chunk, VARIANT),
                content=chunk.content,
                embedding=embedding,
                meta={**chunk.meta, "embedder": "voyage", "chunks_run": CHUNKS_RUN},
            )
            for chunk, embedding in zip(CHUNKS, EMBEDDINGS, strict=True)
        ]
    )
    embedder = FakeTextEmbedder()
    FakeRanker.built = []
    monkeypatch.setattr(hybrid, "build_document_store", lambda settings: store)
    monkeypatch.setattr(hybrid, "build_query_embedder", lambda name: embedder)
    monkeypatch.setattr(hybrid, "VoyageRanker", FakeRanker)
    return embedder


def connections(pipeline) -> list[tuple[str, str]]:
    """The pipeline's connections as ``("sender.socket", "receiver.socket")`` pairs."""
    return [
        (f"{sender}.{data['from_socket'].name}", f"{receiver}.{data['to_socket'].name}")
        for sender, receiver, data in pipeline.graph.edges(data=True)
    ]


# --- build_hybrid_pipeline ----------------------------------------------------


def test_pipeline_connects_five_components(existence_checks: list):
    pipeline = hybrid.build_hybrid_pipeline(VARIANT, chunks_run=CHUNKS_RUN)

    assert list(pipeline.graph.nodes) == ["bm25", "query_embedder", "dense", "joiner", "ranker"]
    assert sorted(connections(pipeline)) == [
        ("bm25.documents", "joiner.documents"),
        ("dense.documents", "joiner.documents"),
        ("joiner.documents", "ranker.documents"),
        ("query_embedder.embedding", "dense.query_embedding"),
    ]
    assert isinstance(pipeline.get_component("bm25"), InMemoryBM25Retriever)
    assert type(pipeline.get_component("query_embedder")) is type(build_query_embedder("voyage"))
    assert isinstance(pipeline.get_component("dense"), QdrantEmbeddingRetriever)
    joiner = pipeline.get_component("joiner")
    assert isinstance(joiner, DocumentJoiner)
    assert joiner.to_dict()["init_parameters"]["join_mode"] == "reciprocal_rank_fusion"
    ranker = pipeline.get_component("ranker")
    assert isinstance(ranker, VoyageRanker)
    assert ranker.to_dict()["init_parameters"]["model"] == "rerank-2.5"
    assert [settings.collection for settings in existence_checks] == [COLLECTION]


def test_defaults_take_50_candidates_from_each_retriever_and_rerank_to_10():
    pipeline = hybrid.build_hybrid_pipeline(VARIANT, chunks_run=CHUNKS_RUN)

    dense = pipeline.get_component("dense").to_dict()["init_parameters"]
    store = dense["document_store"]["init_parameters"]
    assert pipeline.get_component("bm25").to_dict()["init_parameters"]["top_k"] == 50
    assert dense["top_k"] == 50
    assert pipeline.get_component("ranker").to_dict()["init_parameters"]["top_k"] == 10
    assert pipeline.metadata == {"collection": COLLECTION, "candidates": 50, "top_k": 10}
    assert store["index"] == COLLECTION
    assert store["embedding_dim"] == VARIANT.embedder.dimension == 2048
    assert store["recreate_index"] is False
    assert store["return_embedding"] is False


def test_candidates_and_top_k_follow_the_arguments():
    pipeline = hybrid.build_hybrid_pipeline(
        VARIANT, chunks_run=CHUNKS_RUN, prefix="ix_verify", candidates=7, top_k=3
    )

    assert pipeline.get_component("bm25").to_dict()["init_parameters"]["top_k"] == 7
    assert pipeline.get_component("dense").to_dict()["init_parameters"]["top_k"] == 7
    assert pipeline.get_component("ranker").to_dict()["init_parameters"]["top_k"] == 3
    assert pipeline.metadata == {
        "collection": "ix_verify__markdown_ast__voyage",
        "candidates": 7,
        "top_k": 3,
    }


def test_each_bm25_chunk_has_stage_3s_point_key():
    pipeline = hybrid.build_hybrid_pipeline(VARIANT, chunks_run=CHUNKS_RUN)

    held = pipeline.get_component("bm25").document_store.filter_documents()
    assert sorted(doc.id for doc in held) == sorted(point_key(chunk, VARIANT) for chunk in CHUNKS)
    assert "markdown_ast:voyage:tutorial/path-params.md:0" in {doc.id for doc in held}


def test_a_missing_collection_is_refused_before_any_store_is_built(
    monkeypatch: pytest.MonkeyPatch,
):
    built = []
    monkeypatch.setattr(hybrid, "collection_exists", lambda settings: False)
    monkeypatch.setattr(hybrid, "build_document_store", built.append)
    monkeypatch.setattr(hybrid, "InMemoryDocumentStore", lambda: built.append("memory"))

    with pytest.raises(
        ValueError, match="Collection ix_verify__markdown_ast__voyage does not exist"
    ):
        hybrid.build_hybrid_pipeline(VARIANT, chunks_run=CHUNKS_RUN, prefix="ix_verify")

    assert built == []


def test_a_missing_chunk_run_is_named_by_its_path(chunk_runs: Path, existence_checks: list):
    expected = chunk_runs / "no_such_run" / "chunks" / "markdown_ast.jsonl"

    with pytest.raises(ValueError, match="no markdown_ast chunks") as error:
        hybrid.build_hybrid_pipeline(VARIANT, chunks_run="no_such_run")

    assert str(expected) in str(error.value)
    assert existence_checks == []  # refused before any Qdrant call


# --- retrieve -----------------------------------------------------------------


def test_retrieve_returns_top_k_reranked_chunks_with_sources_and_scores(
    fakes: FakeTextEmbedder,
):
    pipeline = hybrid.build_hybrid_pipeline(VARIANT, chunks_run=CHUNKS_RUN, top_k=2)

    documents = hybrid.retrieve(pipeline, QUESTION)

    assert [doc.id for doc in documents] == [
        "markdown_ast:voyage:tutorial/path-params.md:0",
        "markdown_ast:voyage:tutorial/query-params.md:0",
    ]
    assert [doc.meta["source_path"] for doc in documents] == [
        "tutorial/path-params.md",
        "tutorial/query-params.md",
    ]
    scores = [doc.score for doc in documents]
    assert all(score is not None for score in scores)
    assert scores == sorted(scores, reverse=True)
    assert fakes.texts == [QUESTION]
    [ranker] = FakeRanker.built
    assert ranker.model == "rerank-2.5"


def test_a_chunk_both_retrievers_find_reaches_the_ranker_once(fakes: FakeTextEmbedder):
    pipeline = hybrid.build_hybrid_pipeline(VARIANT, chunks_run=CHUNKS_RUN)

    outputs = pipeline.run(
        {
            "bm25": {"query": QUESTION},
            "query_embedder": {"text": QUESTION},
            "ranker": {"query": QUESTION},
        },
        include_outputs_from={"bm25", "dense"},
    )
    hybrid.retrieve(pipeline, QUESTION)

    first = "markdown_ast:voyage:tutorial/path-params.md:0"
    assert first in {doc.id for doc in outputs["bm25"]["documents"]}
    assert first in {doc.id for doc in outputs["dense"]["documents"]}
    [ranker] = FakeRanker.built
    fused_ids = [doc.id for doc in ranker.received]
    assert fused_ids.count(first) == 1
    assert len(fused_ids) == len(set(fused_ids))
    assert sorted(fused_ids) == sorted(point_key(chunk, VARIANT) for chunk in CHUNKS)


def test_a_top_k_passed_to_retrieve_wins(fakes: FakeTextEmbedder):
    pipeline = hybrid.build_hybrid_pipeline(VARIANT, chunks_run=CHUNKS_RUN, top_k=3)

    assert len(hybrid.retrieve(pipeline, QUESTION, top_k=1)) == 1
    assert len(hybrid.retrieve(pipeline, QUESTION)) == 3


# --- main ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "names",
    [
        ["--strategy", "naive_tiny"],
        ["--embedder", "openai"],
        ["--strategy", "all"],
        ["--embedder", "all"],
        ["--collection-prefix", "a__b"],
        ["--chunks-run", "../elsewhere"],
        ["--top-k", "0"],
        ["--question", "  "],
        ["--no-compare"],
    ],
    ids=[
        "unknown-strategy",
        "unknown-embedder",
        "all-strategies",
        "all-embedders",
        "bad-prefix",
        "bad-chunks-run",
        "top-k-0",
        "blank-question",
        "no-compare-flag",
    ],
)
def test_main_exits_2_for_a_bad_command_line(names: list[str], existence_checks: list):
    with pytest.raises(SystemExit) as exit_info:
        hybrid.main(["--question", QUESTION, *names])

    assert exit_info.value.code == 2
    assert existence_checks == []  # stopped before any Qdrant call


def test_main_exits_1_for_a_missing_collection(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(hybrid, "collection_exists", lambda settings: False)

    with pytest.raises(SystemExit) as exit_info:
        hybrid.main(["--question", QUESTION, "--chunks-run", CHUNKS_RUN])

    assert exit_info.value.code == 1


def test_main_exits_1_for_a_missing_chunk_run():
    with pytest.raises(SystemExit) as exit_info:
        hybrid.main(["--question", QUESTION, "--chunks-run", "no_such_run"])

    assert exit_info.value.code == 1


@pytest.mark.parametrize("name", ["QDRANT_URL", "VOYAGE_API_KEY"])
def test_main_exits_1_for_missing_config(
    name: str, monkeypatch: pytest.MonkeyPatch, existence_checks: list
):
    monkeypatch.delenv(name)

    with pytest.raises(SystemExit) as exit_info:
        hybrid.main(["--question", QUESTION, "--chunks-run", CHUNKS_RUN])

    assert exit_info.value.code == 1
    assert existence_checks == []


def test_main_exits_1_for_bge_without_openrouter_key(
    monkeypatch: pytest.MonkeyPatch, existence_checks: list, tmp_path: Path
):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(hybrid, "cached_tokenizer_path", lambda name: tmp_path)

    with pytest.raises(SystemExit) as exit_info:
        hybrid.main(["--question", QUESTION, "--embedder", "bge", "--chunks-run", CHUNKS_RUN])

    assert exit_info.value.code == 1
    assert existence_checks == []


def test_main_exits_1_without_bges_tokenizer(
    monkeypatch: pytest.MonkeyPatch, existence_checks: list
):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(hybrid, "cached_tokenizer_path", lambda name: None)

    with pytest.raises(SystemExit) as exit_info:
        hybrid.main(["--question", QUESTION, "--embedder", "bge", "--chunks-run", CHUNKS_RUN])

    assert exit_info.value.code == 1
    assert existence_checks == []


@pytest.mark.parametrize("compare", [[], ["--compare"]], ids=["plain", "compare"])
def test_main_logs_the_chunks_and_writes_no_files(
    compare: list[str],
    fakes: FakeTextEmbedder,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    before = sorted(tmp_path.rglob("*"))
    caplog.set_level(logging.INFO, logger=hybrid.__name__)

    hybrid.main(["--question", QUESTION, "--chunks-run", CHUNKS_RUN, "--top-k", "2", *compare])

    assert sorted(tmp_path.rglob("*")) == before
    assert fakes.texts == [QUESTION]  # one query embedding, compared or not
    messages = [record.getMessage() for record in caplog.records]
    assert "Reranked: 2 chunks" in messages
    assert any(
        message.startswith("#1  ")
        and "markdown_ast:voyage:tutorial/path-params.md:0  tutorial/path-params.md" in message
        for message in messages
    )
    if compare:
        assert "BM25: 4 chunks" in messages
        assert "Dense: 4 chunks" in messages
        in_both = next(m for m in messages if m.startswith("In both lists: "))
        assert "markdown_ast:voyage:tutorial/path-params.md:0" in in_both
    else:
        assert not any(m.startswith("In both lists") for m in messages)


def test_importing_the_module_parses_no_arguments_and_opens_no_connection(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(sys, "argv", ["hybrid.py", "--no-such-flag"])
    spec = importlib.util.spec_from_file_location(
        "hybrid_fresh", PROJECT_ROOT / "retrieval" / "hybrid.py"
    )
    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)  # parsing would exit 2; connecting would fail the test

    assert (module.CANDIDATES, module.TOP_K, module.RERANK_MODEL) == (50, 10, "rerank-2.5")
