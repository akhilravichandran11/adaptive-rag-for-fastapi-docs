"""
Prove that stage 3 indexes a saved chunk run with fixed point ids, rebuilds a
collection only with --recreate, and records what it wrote.

``data_ingestion/stage3_index_chunks.py`` reads one chunk run saved by stage 2 and, for
each strategy × embedder, rebuilds its collection, embeds the chunks and writes them
(D33-D35), then writes ``index_report.json`` and ``run_meta.json`` (D40). Only its
agreed seams are tested: ``point_key``, ``write_variant``, ``open_collection`` and
``main(argv)``.

No test reaches Qdrant or an embedder API. ``collection_exists`` and
``build_document_store`` are replaced, where the stage looks them up, by a fake
cluster: one persistent ``QdrantDocumentStore(location=":memory:")`` per collection,
behind a store that, like the real one, changes nothing until its first method call
and wipes the collection then when built with ``recreate=True``. Two tests prove that
on the real library: a store built for the cluster opens no connection or client until
a method is called (its clients replaced, so no background thread starts), and an
on-disk store under ``tmp_path`` built with ``recreate=True`` wipes its collection only
when first used. The embedders are fakes that return fixed-length vectors.
In-memory Qdrant has no strict mode and ignores payload indexes, so these tests prove
ids, counts and the rebuild rule; the live run (PRT-06) proves the indexes. Chunk runs
are saved under ``tmp_path`` with ``chunk_snapshot.write_chunks``, and the stage's
manifest and ``outputs/`` are pointed there. An autouse fixture refuses every outgoing
connection (C006). The real ``.env`` is not loaded.

The BGE figures in the report (exact truncation, tokens sent) are counted with BGE's
own tokenizer from the project model cache; that one test skips when it is absent::

    uv run python scripts/download_models.py     # from the project root
    uv run pytest tests/data_ingestion/test_stage3_index_chunks.py
"""

import json
import logging
import socket
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import qdrant_client
from haystack import Document
from haystack_integrations.document_stores.qdrant import QdrantDocumentStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))  # lets pytest import the project from any directory

from data_ingestion import (  # noqa: E402  (needs the path set above)
    embedders,
    pipeline,
    qdrant_store,
)
from data_ingestion import stage3_index_chunks as stage3  # noqa: E402
from data_ingestion.chunk_snapshot import manifest_sha256, write_chunks  # noqa: E402
from data_ingestion.embedders import (  # noqa: E402
    EMBEDDERS,
    bge_tokenizer,
    build_document_embedder,
    cached_tokenizer_path,
    count_tokens,
)
from data_ingestion.openrouter_embeddings import truncate_to_bge_limit  # noqa: E402
from data_ingestion.variants import Variant  # noqa: E402

# A filesystem probe, as in test_embedders.py: it runs at collection time, before the
# network guard exists, so it must not load the tokenizer.
needs_bge = pytest.mark.skipif(
    cached_tokenizer_path("bge") is None,
    reason="BGE's tokenizer is not in the project model cache; run scripts/download_models.py",
)

CHUNKS_RUN = "chunks_test"
INDEX_RUN = "index_test"
CURATION_RUN = "20260101_000000"
# write_variant reads only these two fields of a run's run_meta.
RUN = {"run_id": INDEX_RUN, "chunks_run": CHUNKS_RUN}

BGE = Variant("naive_small", EMBEDDERS["bge"])
VOYAGE = Variant("naive_small", EMBEDDERS["voyage"])

# D33, written out here so the test does not just compare the stage's constant with itself.
FOUR_INDEXES = [
    {"field_name": "meta.bucket", "field_schema": "keyword"},
    {"field_name": "meta.source_path", "field_schema": "keyword"},
    {"field_name": "meta.knowledge_domain", "field_schema": "keyword"},
    {"field_name": "meta.quality_score", "field_schema": "float"},
]

# index_report.json's fields, in the plan's order.
REPORT_FIELDS = [
    "collection",
    "strategy",
    "embedder",
    "model",
    "backend",
    "api_model",
    "dimension",
    "input_type",
    "chunks_run",
    "chunks_run_limit",
    "collection_existed",
    "payload_indexes",
    "chunks",
    "points_written",
    "final_count",
    "truncation",
    "tokens_sent",
    "tokens_billed",
    "status",
    "seconds",
]


class FakeEmbedder:
    """Stands in for an embedder component; as it is, for BGE's (no ``input_type``, no usage).

    Returns copies of the documents, each with a vector of ``dimension`` equal floats,
    and records each batch's size.
    """

    def __init__(self, dimension: int) -> None:
        self.dimension = dimension
        self.batches: list[int] = []

    def run(self, documents: list[Document]) -> dict:
        self.batches.append(len(documents))
        return {"documents": [replace(doc, embedding=[0.1] * self.dimension) for doc in documents]}


class FakeVoyageEmbedder(FakeEmbedder):
    """Stands in for ``VoyageDocumentEmbedder``: ``input_type`` and ``total_tokens`` in its meta."""

    input_type = "document"
    TOKENS_PER_CHUNK = 7

    def __init__(self) -> None:
        super().__init__(EMBEDDERS["voyage"].dimension)

    def run(self, documents: list[Document]) -> dict:
        result = super().run(documents)
        result["meta"] = {"total_tokens": self.TOKENS_PER_CHUNK * len(documents)}
        return result


class FakeStore:
    """What the fake ``build_document_store`` returns: a store on one collection of ``cluster``.

    ``cluster`` maps a collection name to one persistent in-memory store, as the Qdrant
    cluster keeps a collection between runs. Like the real store, building this one
    changes nothing: on the first call of any of its methods it creates its collection
    if missing, or, when built with ``recreate=True``, replaces it with an empty one.
    """

    def __init__(self, cluster: dict, settings: qdrant_store.QdrantSettings, recreate: bool):
        self.cluster = cluster
        self.settings = settings
        self.recreate = recreate
        self.used = False

    def backing_store(self) -> QdrantDocumentStore:
        name = self.settings.collection
        if not self.used:
            self.used = True
            if self.recreate or name not in self.cluster:
                self.cluster[name] = QdrantDocumentStore(
                    location=":memory:", embedding_dim=self.settings.embedding_dim
                )
        return self.cluster[name]

    def write_documents(self, documents: list[Document], policy) -> int:
        return self.backing_store().write_documents(documents, policy=policy)

    def count_documents(self) -> int:
        return self.backing_store().count_documents()


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test on any outgoing connection (C006)."""

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the real .env, model cache check, RUN_ID and root logger out of every test.

    ``load_settings`` still runs for real, on stand-in connection variables. ``main``
    configures the root logger with ``force=True``, which would remove pytest's log
    capture; skipping that keeps ``caplog`` working.
    """
    monkeypatch.setattr(stage3, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(qdrant_store, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(stage3, "cached_tokenizer_path", lambda name: tmp_path / "tokenizer.json")
    monkeypatch.setattr(logging, "basicConfig", lambda *args, **kwargs: None)
    monkeypatch.delenv("RUN_ID", raising=False)
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.invalid:6333")
    monkeypatch.setenv("QDRANT_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def qdrant(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A fake Qdrant cluster in place of the real one.

    ``cluster`` holds each collection's persistent in-memory store, and the fake
    ``collection_exists`` reports a collection once it is there. ``built`` records each
    ``build_document_store`` call.
    """
    fake = SimpleNamespace(cluster={}, built=[])

    def exists(settings: qdrant_store.QdrantSettings) -> bool:
        return settings.collection in fake.cluster

    def build(settings: qdrant_store.QdrantSettings, **kwargs) -> FakeStore:
        fake.built.append((settings, kwargs))
        return FakeStore(fake.cluster, settings, kwargs.get("recreate", False))

    monkeypatch.setattr(stage3, "collection_exists", exists)
    monkeypatch.setattr(stage3, "build_document_store", build)
    return fake


@pytest.fixture(autouse=True)
def components(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Fakes in place of the real components, so ``main`` never builds one that could call an API."""
    built = {}

    def build(name: str):
        built[name] = FakeVoyageEmbedder() if name == "voyage" else FakeEmbedder(1024)
        return built[name]

    monkeypatch.setattr(stage3, "build_document_embedder", build)
    return built


@pytest.fixture(autouse=True)
def paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    """A corpus manifest and an ``outputs/`` folder under ``tmp_path``, with the stage pointed at them."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"source": {"ref": "abc1234"}, "files": []}), encoding="utf-8")
    outputs = tmp_path / "outputs"
    monkeypatch.setattr(stage3, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(pipeline, "OUTPUTS_DIR", outputs)
    return SimpleNamespace(manifest=manifest, outputs=outputs)


def make_chunks(strategy: str, documents: int = 2, per_document: int = 3) -> list[Document]:
    """Chunks shaped like a strategy's output: the shared chunk fields and the indexed metadata."""
    return [
        Document(
            content=f"Part {index} of document {number}, chunked by {strategy}.",
            meta={
                "source_path": f"docs/en/docs/tutorial/doc-{number}.md",
                "chunk_index": index,
                "chunk_count": per_document,
                "strategy": strategy,
                "bucket": "tutorial",
                "knowledge_domain": "implementation_patterns",
                "quality_score": 8.0,
            },
        )
        for number in range(documents)
        for index in range(per_document)
    ]


def make_chunk_run(
    paths: SimpleNamespace,
    chunks_by_strategy: dict[str, list[Document]],
    limit: int | None = None,
    run_id: str = CHUNKS_RUN,
) -> Path:
    """Save a chunk run as stage 2 does: the chunk files, then a run_meta.json listing them."""
    run_dir = paths.outputs / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    for name, chunks in chunks_by_strategy.items():
        write_chunks(run_dir, name, chunks)
    run_meta = {
        "stage": "chunk",
        "run_id": run_id,
        "manifest_sha256": manifest_sha256(paths.manifest),
        "curation_run": CURATION_RUN,
        "limit": limit,
        "strategies": {
            name: {"chunk_count": len(chunks)} for name, chunks in chunks_by_strategy.items()
        },
    }
    (run_dir / "run_meta.json").write_text(json.dumps(run_meta), encoding="utf-8")
    return run_dir


def memory_store(variant: Variant) -> QdrantDocumentStore:
    return QdrantDocumentStore(location=":memory:", embedding_dim=variant.embedder.dimension)


def seed(qdrant: SimpleNamespace, collection: str, variant: Variant, ids: list[str]) -> None:
    """Put points with ``ids`` into ``collection`` on the fake cluster, as an earlier run left them."""
    store = memory_store(variant)
    vector = [0.1] * variant.embedder.dimension
    store.write_documents([Document(id=i, content="an earlier run", embedding=vector) for i in ids])
    qdrant.cluster[collection] = store


def point_ids(store: QdrantDocumentStore) -> set[str]:
    return {doc.id for doc in store.filter_documents()}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def folder_bytes(folder: Path) -> dict:
    """Every path under ``folder`` with its bytes (None for a folder), to compare before and after."""
    return {
        path.relative_to(folder): path.read_bytes() if path.is_file() else None
        for path in folder.rglob("*")
    }


def stage3_argv(strategy: str = "all", embedder: str = "voyage", run_id: str = INDEX_RUN) -> list:
    return ["--chunks-run", CHUNKS_RUN, "--strategy", strategy, "--embedder", embedder,
            "--run-id", run_id]  # fmt: skip


def two_strategy_chunk_run(paths: SimpleNamespace) -> None:
    make_chunk_run(
        paths,
        {"naive_small": make_chunks("naive_small"), "naive_large": make_chunks("naive_large")},
    )


# --- the fakes and the real store they stand in for ------------------------------------


def test_the_fake_embedders_have_the_real_components_input_type() -> None:
    # Building a real component makes no call; only its attributes are read.
    assert not hasattr(build_document_embedder("bge"), "input_type")
    assert not hasattr(FakeEmbedder(1024), "input_type")
    assert build_document_embedder("voyage").input_type == FakeVoyageEmbedder.input_type


def test_a_real_store_connects_to_nothing_until_one_of_its_methods_is_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Opening every collection before writing any relies on this: building a store
    # changes nothing on the cluster; its first method call creates the client, which
    # connects and rebuilds the collection.
    attempts = []

    def refuse(*args, **kwargs):
        attempts.append(args)
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    clients = []

    def no_client(**params):
        # Stands in for the Qdrant clients. A sync method such as count_documents
        # creates only QdrantClient, on first use; the AsyncQdrantClient stand-in catches
        # a future library that builds that one early. A real client would start a
        # version check on a background thread that could connect after this test's
        # guard is gone.
        clients.append(params["url"])
        raise ConnectionError("no Qdrant client in tests")

    monkeypatch.setattr(qdrant_client, "QdrantClient", no_client)
    monkeypatch.setattr(qdrant_client, "AsyncQdrantClient", no_client)
    settings = qdrant_store.QdrantSettings(
        url="https://127.0.0.1:6333", collection="ix_test__naive_small__bge", embedding_dim=1024
    )

    store = qdrant_store.build_document_store(
        settings, recreate=True, payload_fields_to_index=stage3.PAYLOAD_INDEXES
    )

    assert (attempts, clients) == ([], [])  # building opened no connection and no client
    with pytest.raises(ConnectionError, match="no Qdrant client"):
        store.count_documents()
    assert clients == ["https://127.0.0.1:6333"]


def test_a_real_store_built_with_recreate_wipes_its_collection_only_when_first_used(
    tmp_path: Path,
) -> None:
    # The same lazy rebuild on the real library, through an on-disk store (no server).
    # Each store is closed before the next one opens: the folder takes one client at a time.
    path = str(tmp_path / "qdrant")
    earlier = QdrantDocumentStore(path=path, index="ix_test", embedding_dim=4)
    earlier.write_documents([Document(id="earlier", content="an earlier run", embedding=[0.1] * 4)])
    earlier.close()

    rebuilt = QdrantDocumentStore(path=path, index="ix_test", embedding_dim=4, recreate_index=True)
    looking = QdrantDocumentStore(path=path, index="ix_test", embedding_dim=4)
    assert looking.count_documents() == 1  # building the recreate store changed nothing
    looking.close()

    assert rebuilt.count_documents() == 0  # its first method call wiped the collection
    rebuilt.close()


# --- point_key -------------------------------------------------------------------------


def test_point_key_is_strategy_embedder_source_path_and_chunk_index() -> None:
    chunk = make_chunks("naive_small")[4]  # document 1, chunk 1

    assert stage3.point_key(chunk, BGE) == "naive_small:bge:docs/en/docs/tutorial/doc-1.md:1"
    assert stage3.point_key(chunk, VOYAGE) == "naive_small:voyage:docs/en/docs/tutorial/doc-1.md:1"
    # The same chunk read back as a new document, as stage 3 reads it, gets the same key.
    again = Document(content=chunk.content, meta=dict(chunk.meta))
    assert stage3.point_key(again, BGE) == stage3.point_key(chunk, BGE)


# --- write_variant ---------------------------------------------------------------------


def test_write_variant_writes_every_chunk_in_batches_of_64_under_its_point_key() -> None:
    chunks = make_chunks("naive_small", documents=10, per_document=13)  # 130: 64 + 64 + 2
    store = memory_store(BGE)
    embedder = FakeEmbedder(1024)

    result = stage3.write_variant(store, embedder, chunks, BGE, RUN)

    assert embedder.batches == [64, 64, 2]
    assert result == {"points_written": 130, "final_count": 130, "tokens_billed": None}
    stored = {doc.id: doc for doc in store.filter_documents()}
    assert set(stored) == {stage3.point_key(chunk, BGE) for chunk in chunks}
    point = stored[stage3.point_key(chunks[0], BGE)]
    assert point.content == chunks[0].content
    assert point.meta == {
        **chunks[0].meta,
        "embedder": "bge",
        "index_run": INDEX_RUN,
        "chunks_run": CHUNKS_RUN,
    }
    assert "embedder" not in chunks[0].meta  # the chunks passed in are left as they were


def test_the_same_chunks_give_the_same_points_in_a_fresh_store_and_when_written_again() -> None:
    chunks = make_chunks("naive_small")
    first, fresh = memory_store(BGE), memory_store(BGE)

    stage3.write_variant(first, FakeEmbedder(1024), chunks, BGE, RUN)
    result = stage3.write_variant(fresh, FakeEmbedder(1024), chunks, BGE, RUN)

    assert result["final_count"] == first.count_documents() == len(chunks)
    assert point_ids(fresh) == point_ids(first)
    # Written over the same points by id (DuplicatePolicy.OVERWRITE), the count stays.
    assert stage3.write_variant(first, FakeEmbedder(1024), chunks, BGE, RUN)["final_count"] == 6


def test_write_variant_sums_the_tokens_voyage_reports_for_each_batch() -> None:
    chunks = make_chunks("naive_small", documents=10, per_document=13)

    result = stage3.write_variant(memory_store(VOYAGE), FakeVoyageEmbedder(), chunks, VOYAGE, RUN)

    assert result["tokens_billed"] == FakeVoyageEmbedder.TOKENS_PER_CHUNK * 130


@pytest.mark.parametrize("variant", [BGE, VOYAGE], ids=["bge", "voyage"])
def test_a_wrong_length_vector_raises_before_its_batch_is_written(variant: Variant) -> None:
    store = memory_store(variant)
    wrong_size = FakeEmbedder(dimension=variant.embedder.dimension - 1)

    with pytest.raises(ValueError, match=f"hold {variant.embedder.dimension}-dimension"):
        stage3.write_variant(store, wrong_size, make_chunks("naive_small"), variant, RUN)

    assert store.count_documents() == 0


# --- open_collection -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exists", "recreate"),
    [(False, False), (False, True), (True, True)],
    ids=["new", "new-with-recreate", "existing-with-recreate"],
)
def test_open_collection_builds_a_rebuilt_store_with_the_four_payload_indexes(
    qdrant: SimpleNamespace, exists: bool, recreate: bool
) -> None:
    collection = "ix_test__naive_small__voyage"
    if exists:
        seed(qdrant, collection, VOYAGE, ["earlier"])

    _, existed = stage3.open_collection(VOYAGE, prefix="ix_test", recreate=recreate)

    assert existed is exists
    [(settings, kwargs)] = qdrant.built
    assert (settings.collection, settings.embedding_dim) == (collection, 2048)
    assert kwargs == {"recreate": True, "payload_fields_to_index": FOUR_INDEXES}
    # Opening changed nothing: a new collection is not created, an existing one keeps its points.
    assert {name: point_ids(points) for name, points in qdrant.cluster.items()} == (
        {collection: {"earlier"}} if exists else {}
    )


def test_open_collection_stops_on_an_existing_collection_without_recreate(
    qdrant: SimpleNamespace,
) -> None:
    seed(qdrant, "fastapi_docs__naive_small__bge", BGE, ["earlier"])

    with pytest.raises(SystemExit) as exit_info:
        stage3.open_collection(BGE, prefix="fastapi_docs", recreate=False)

    message = str(exit_info.value)
    assert "fastapi_docs__naive_small__bge" in message
    assert "--recreate" in message
    assert qdrant.built == []
    assert point_ids(qdrant.cluster["fastapi_docs__naive_small__bge"]) == {"earlier"}


# --- main ------------------------------------------------------------------------------


def test_main_indexes_each_listed_strategy_and_writes_the_report_and_run_meta(
    paths: SimpleNamespace, qdrant: SimpleNamespace, components: dict
) -> None:
    make_chunk_run(
        paths,
        {
            "naive_small": make_chunks("naive_small"),
            "markdown_ast": make_chunks("markdown_ast", 2, 2),
        },
    )

    run_dir = stage3.main(stage3_argv())

    assert run_dir == paths.outputs / INDEX_RUN
    assert sorted(path.name for path in run_dir.iterdir()) == ["index_report.json", "run_meta.json"]
    assert list(components) == ["voyage"]  # only the selected embedder is built
    report = read_json(run_dir / "index_report.json")
    assert [entry["collection"] for entry in report] == [
        "fastapi_docs__naive_small__voyage",
        "fastapi_docs__markdown_ast__voyage",
    ]
    assert [list(entry) for entry in report] == [REPORT_FIELDS, REPORT_FIELDS]

    entry = report[0]
    assert entry.pop("seconds") >= 0
    truncation = entry.pop("truncation")
    assert (truncation["embedder"], truncation["exact"], truncation["chunk_count"]) == (
        "voyage",
        False,
        6,
    )
    assert entry == {
        "collection": "fastapi_docs__naive_small__voyage",
        "strategy": "naive_small",
        "embedder": "voyage",
        "model": "voyage-4-lite",
        "backend": "voyage",
        "api_model": None,
        "dimension": 2048,
        "input_type": "document",
        "chunks_run": CHUNKS_RUN,
        "chunks_run_limit": None,
        "collection_existed": False,
        "payload_indexes": [index["field_name"] for index in FOUR_INDEXES],
        "chunks": 6,
        "points_written": 6,
        "final_count": 6,
        "tokens_sent": FakeVoyageEmbedder.TOKENS_PER_CHUNK * 6,
        "tokens_billed": FakeVoyageEmbedder.TOKENS_PER_CHUNK * 6,
        "status": "complete",
    }
    assert report[1]["final_count"] == 4

    store = qdrant.cluster["fastapi_docs__naive_small__voyage"]
    assert {
        (doc.meta["index_run"], doc.meta["chunks_run"]) for doc in store.filter_documents()
    } == {(INDEX_RUN, CHUNKS_RUN)}
    assert read_json(run_dir / "run_meta.json") == {
        "stage": "index",
        "run_id": INDEX_RUN,
        "chunks_run": CHUNKS_RUN,
        "collection_prefix": "fastapi_docs",
        "flags": {"strategy": "all", "embedder": "voyage", "recreate": False},
        "embedders": {
            "voyage": {
                "model": "voyage-4-lite",
                "backend": "voyage",
                "api_model": None,
                "dimension": 2048,
                "input_type": "document",
            }
        },
    }


def test_bge_records_a_null_input_type_without_the_model_cache(
    paths: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # As in a checkout without models/: the real token counts would now raise, so stage 3
    # counts words instead, where it looks the counts up.
    monkeypatch.setattr(embedders, "cached_tokenizer_path", lambda name: None)
    monkeypatch.setattr(stage3, "count_tokens", lambda name, texts: [len(t.split()) for t in texts])
    monkeypatch.setattr(
        stage3, "truncation_stats", lambda name, texts: SimpleNamespace(as_dict=lambda: {})
    )
    make_chunk_run(paths, {"naive_small": make_chunks("naive_small")})

    run_dir = stage3.main(stage3_argv(embedder="all"))

    run_meta = read_json(run_dir / "run_meta.json")
    assert {name: record["input_type"] for name, record in run_meta["embedders"].items()} == {
        "bge": None,
        "voyage": "document",
    }
    report = read_json(run_dir / "index_report.json")
    assert [(entry["embedder"], entry["input_type"]) for entry in report] == [
        ("bge", None),
        ("voyage", "document"),
    ]
    assert [entry["status"] for entry in report] == ["complete", "complete"]


@needs_bge
def test_bge_counts_the_tokens_it_was_sent_with_its_own_tokenizer(paths: SimpleNamespace) -> None:
    chunks = make_chunks("naive_small")
    chunks[0] = Document(content="token " * 600, meta=chunks[0].meta)  # over BGE's 512
    make_chunk_run(paths, {"naive_small": chunks})
    texts = [chunk.content for chunk in chunks]
    assert count_tokens("bge", texts)[0] > 512
    # What openrouter_embeddings actually sends: each text cut to BGE's 512 tokens.
    tokenizer = bge_tokenizer()
    sent = count_tokens("bge", [truncate_to_bge_limit(text, tokenizer) for text in texts])

    run_dir = stage3.main(stage3_argv(embedder="bge"))

    [bge] = read_json(run_dir / "index_report.json")
    assert (bge["input_type"], bge["tokens_billed"]) == (None, None)
    assert bge["tokens_sent"] == sum(sent)
    assert (bge["truncation"]["exact"], bge["truncation"]["chunks_over_limit"]) == (True, 1)
    assert (bge["status"], bge["final_count"]) == ("complete", 6)


@pytest.mark.parametrize(
    ("prefix_args", "warned"),
    [([], True), (["--collection-prefix", "ix_verify"], False)],
    ids=["default-prefix", "throwaway-prefix"],
)
def test_a_limited_chunk_run_into_the_default_prefix_warns_and_still_writes(
    paths: SimpleNamespace, caplog: pytest.LogCaptureFixture, prefix_args: list, warned: bool
) -> None:
    make_chunk_run(
        paths,
        {"naive_small": make_chunks("naive_small"), "naive_large": make_chunks("naive_large")},
        limit=5,
    )

    run_dir = stage3.main(stage3_argv() + prefix_args)

    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.name == stage3.logger.name and record.levelno == logging.WARNING
    ]
    if warned:
        [message] = warnings
        assert "--limit 5" in message
        assert "fastapi_docs__naive_small__voyage" in message
        assert "fastapi_docs__naive_large__voyage" in message
    else:
        assert warnings == []
    report = read_json(run_dir / "index_report.json")
    assert [(entry["status"], entry["final_count"]) for entry in report] == [("complete", 6)] * 2
    assert [entry["chunks_run_limit"] for entry in report] == [5, 5]


def test_a_chunk_run_from_another_corpus_build_is_refused_before_anything_is_written(
    paths: SimpleNamespace, qdrant: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    make_chunk_run(paths, {"naive_small": make_chunks("naive_small")})
    paths.manifest.write_text(json.dumps({"files": ["rebuilt"]}), encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        stage3.main(stage3_argv())

    assert exit_info.value.code == 1
    assert "corpus changed" in caplog.text
    assert qdrant.built == []
    assert not (paths.outputs / INDEX_RUN).exists()


def test_a_chunk_file_run_meta_does_not_list_is_neither_indexed_nor_accepted(
    paths: SimpleNamespace, qdrant: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    run_dir = make_chunk_run(paths, {"naive_large": make_chunks("naive_large")})
    # Left by an earlier run under the same run id (F001): on disk, not in run_meta.json.
    write_chunks(run_dir, "naive_small", make_chunks("naive_small"))

    indexed = stage3.main(stage3_argv(strategy="all", run_id="all_listed"))

    assert [entry["strategy"] for entry in read_json(indexed / "index_report.json")] == [
        "naive_large"
    ]
    assert [settings.collection for settings, _ in qdrant.built] == [
        "fastapi_docs__naive_large__voyage"
    ]

    with pytest.raises(SystemExit) as exit_info:
        stage3.main(stage3_argv(strategy="naive_small", run_id="stray"))

    assert exit_info.value.code == 1
    assert "holds no naive_small chunks; it holds: naive_large" in caplog.text
    assert len(qdrant.built) == 1
    assert not (paths.outputs / "stray").exists()


@pytest.mark.parametrize(
    "from_env",
    [False, True],
    ids=["run-id-flag", "RUN_ID-from-environment"],
)
def test_a_run_id_equal_to_the_chunk_run_is_refused_and_the_chunk_run_is_untouched(
    paths: SimpleNamespace,
    qdrant: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    from_env: bool,
) -> None:
    run_dir = make_chunk_run(paths, {"naive_small": make_chunks("naive_small")})
    chunk_run_meta = (run_dir / "run_meta.json").read_bytes()
    argv = ["--chunks-run", CHUNKS_RUN, "--strategy", "all", "--embedder", "voyage"]
    if from_env:
        monkeypatch.setenv("RUN_ID", CHUNKS_RUN)
    else:
        argv += ["--run-id", CHUNKS_RUN]

    with pytest.raises(SystemExit) as exit_info:
        stage3.main(argv)

    assert exit_info.value.code == 1
    assert f"Run id {CHUNKS_RUN} is the chunk run's own id" in caplog.text
    assert (run_dir / "run_meta.json").read_bytes() == chunk_run_meta
    assert not (run_dir / "index_report.json").exists()
    assert qdrant.built == []


@pytest.mark.parametrize("earlier", ["stage 2", "stage 3"], ids=["stage-2-run", "stage-3-run"])
def test_a_run_id_an_earlier_run_used_is_refused_before_anything_is_read_or_written(
    paths: SimpleNamespace,
    qdrant: SimpleNamespace,
    components: dict,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    earlier: str,
) -> None:
    make_chunk_run(paths, {"naive_small": make_chunks("naive_small")})
    if earlier == "stage 2":
        # Another chunk run, not the one being indexed (F001: RUN_ID shared by both stages).
        taken = make_chunk_run(
            paths, {"naive_large": make_chunks("naive_large")}, run_id="other_chunks"
        )
    else:
        # A second stage-3 pass under the same run id, e.g. BGE after Voyage (F001).
        taken = stage3.main(stage3_argv(run_id="shared"))
    before = folder_bytes(taken)
    collections_built = len(qdrant.built)

    def refuse(*args, **kwargs):
        raise AssertionError("the chunk run was read before the run folder was checked")

    monkeypatch.setattr(stage3, "check_same_corpus", refuse)

    with pytest.raises(SystemExit) as exit_info:
        stage3.main(stage3_argv(embedder="bge", run_id=taken.name))

    assert exit_info.value.code == 1
    assert f"Run folder {taken} already exists" in caplog.text
    assert folder_bytes(taken) == before
    assert len(qdrant.built) == collections_built
    assert "bge" not in components


def test_a_run_id_differing_from_the_chunk_runs_only_in_case_is_refused_on_macos(
    paths: SimpleNamespace, qdrant: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    run_dir = make_chunk_run(paths, {"naive_small": make_chunks("naive_small")})
    alias = CHUNKS_RUN.upper()  # passes the same-id check, which compares exactly
    if not (paths.outputs / alias).exists():
        pytest.skip("the disk under tmp_path is case-sensitive, so the two ids are two folders")
    before = folder_bytes(run_dir)

    with pytest.raises(SystemExit) as exit_info:
        stage3.main(stage3_argv(run_id=alias))

    assert exit_info.value.code == 1
    assert f"Run folder {paths.outputs / alias} already exists" in caplog.text
    assert folder_bytes(run_dir) == before
    assert qdrant.built == []


def test_a_run_that_takes_the_run_folder_after_the_check_stops_this_one_before_it_writes(
    paths: SimpleNamespace,
    qdrant: SimpleNamespace,
    components: dict,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    make_chunk_run(paths, {"naive_small": make_chunks("naive_small")})
    raced = paths.outputs / INDEX_RUN
    real_open_collection = stage3.open_collection

    def open_collection_while_another_run_starts(*args, **kwargs):
        # Another run with the same id passed its check too, and writes first.
        raced.mkdir(parents=True, exist_ok=True)
        (raced / "index_report.json").write_text("[]\n", encoding="utf-8")
        return real_open_collection(*args, **kwargs)

    monkeypatch.setattr(stage3, "open_collection", open_collection_while_another_run_starts)

    with pytest.raises(SystemExit) as exit_info:
        stage3.main(stage3_argv())

    assert exit_info.value.code == 1
    assert f"Run folder {raced} was created by another run" in caplog.text
    assert folder_bytes(raced) == {Path("index_report.json"): b"[]\n"}
    assert qdrant.cluster == {}  # no collection was created or rebuilt
    assert components["voyage"].batches == []


@pytest.mark.parametrize(
    ("listed", "strategy"),
    [(["old_name"], "all"), (["old_name", "naive_small"], "all"),
     (["old_name", "naive_small"], "naive_small")],
    ids=["only-unknown", "unknown-beside-known", "named-known-strategy"],
)  # fmt: skip
def test_a_chunk_run_listing_a_strategy_the_registry_does_not_know_is_refused(
    paths: SimpleNamespace,
    qdrant: SimpleNamespace,
    caplog: pytest.LogCaptureFixture,
    listed: list[str],
    strategy: str,
) -> None:
    # As a chunk run made before a strategy was renamed would be. It is refused even
    # when --strategy names a strategy this code knows.
    make_chunk_run(paths, {name: make_chunks(name) for name in listed})

    with pytest.raises(SystemExit) as exit_info:
        stage3.main(stage3_argv(strategy=strategy))

    assert exit_info.value.code == 1
    assert "lists strategies this code does not know: old_name; known: naive_small," in caplog.text
    assert qdrant.built == []
    assert not (paths.outputs / INDEX_RUN).exists()


def test_a_chunk_run_with_nothing_to_index_is_refused(
    paths: SimpleNamespace, qdrant: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    make_chunk_run(paths, {})

    with pytest.raises(SystemExit) as exit_info:
        stage3.main(stage3_argv())

    assert exit_info.value.code == 1
    assert f"Chunk run {CHUNKS_RUN} lists no strategies to index" in caplog.text
    assert qdrant.built == []
    assert not (paths.outputs / INDEX_RUN).exists()


def test_a_chunk_file_whose_count_differs_from_run_meta_is_refused(
    paths: SimpleNamespace, qdrant: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    run_dir = make_chunk_run(paths, {"naive_large": make_chunks("naive_large")})
    # A re-run into the same run id that failed after write_chunks (F001): the file
    # shrank, and run_meta.json still records the full run.
    write_chunks(run_dir, "naive_large", make_chunks("naive_large")[:2])

    with pytest.raises(SystemExit) as exit_info:
        stage3.main(stage3_argv())

    assert exit_info.value.code == 1
    assert "naive_large.jsonl holds 2 chunks, but its run_meta.json records 6" in caplog.text
    assert qdrant.built == []
    assert not (paths.outputs / INDEX_RUN).exists()


def test_an_existing_later_collection_stops_the_run_before_anything_is_embedded(
    paths: SimpleNamespace, qdrant: SimpleNamespace, components: dict
) -> None:
    two_strategy_chunk_run(paths)
    # The second of the two collections, holding an earlier run's points.
    seed(qdrant, "fastapi_docs__naive_large__voyage", VOYAGE, ["earlier-1", "earlier-2"])

    with pytest.raises(SystemExit) as exit_info:
        stage3.main(stage3_argv())

    assert "fastapi_docs__naive_large__voyage" in str(exit_info.value)
    assert "--recreate" in str(exit_info.value)
    assert components["voyage"].batches == []
    # The first collection was opened but never created; the existing one is untouched.
    assert set(qdrant.cluster) == {"fastapi_docs__naive_large__voyage"}
    assert point_ids(qdrant.cluster["fastapi_docs__naive_large__voyage"]) == {
        "earlier-1",
        "earlier-2",
    }
    assert not (paths.outputs / INDEX_RUN).exists()


def test_a_rebuild_needs_recreate_and_replaces_the_points_rather_than_adding_to_them(
    paths: SimpleNamespace, qdrant: SimpleNamespace
) -> None:
    make_chunk_run(paths, {"naive_small": make_chunks("naive_small")})
    collection = "fastapi_docs__naive_small__voyage"
    first = read_json(stage3.main(stage3_argv(run_id="first")) / "index_report.json")
    run_ids = point_ids(qdrant.cluster[collection])
    # A point this chunk run does not make, as a chunker change would leave behind.
    stale = "naive_small:voyage:docs/en/docs/tutorial/removed.md:0"
    qdrant.cluster[collection].write_documents(
        [Document(id=stale, content="an earlier chunk", embedding=[0.1] * 2048)]
    )

    with pytest.raises(SystemExit, match="pass --recreate"):
        stage3.main(stage3_argv(run_id="second"))
    assert point_ids(qdrant.cluster[collection]) == run_ids | {stale}  # refused: unchanged
    rebuilt = read_json(
        stage3.main([*stage3_argv(run_id="third"), "--recreate"]) / "index_report.json"
    )

    assert len(run_ids) == 6
    assert point_ids(qdrant.cluster[collection]) == run_ids  # the stale point is gone
    assert first[0]["collection_existed"] is False
    assert rebuilt[0]["collection_existed"] is True
    assert (rebuilt[0]["status"], rebuilt[0]["final_count"]) == ("complete", 6)
    assert not (paths.outputs / "second").exists()


def test_a_count_that_differs_from_the_chunks_leaves_the_collection_partial(
    paths: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    chunks = make_chunks("naive_small")
    # Two chunks with one point key: written with OVERWRITE, they become one point.
    chunks[1] = Document(content=chunks[1].content, meta={**chunks[1].meta, "chunk_index": 0})
    make_chunk_run(paths, {"naive_small": chunks})

    with pytest.raises(SystemExit) as exit_info:
        stage3.main(stage3_argv())

    assert exit_info.value.code == 1
    [entry] = read_json(paths.outputs / INDEX_RUN / "index_report.json")
    assert (entry["status"], entry["chunks"], entry["final_count"]) == ("partial", 6, 5)
    assert "fastapi_docs__naive_small__voyage: holds 5 points for 6 chunks" in caplog.text


def test_a_failed_collection_is_partial_the_others_complete_and_the_run_exits_1(
    paths: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    two_strategy_chunk_run(paths)
    real_write_variant = stage3.write_variant

    def write_variant(store, embedder, chunks, variant, run):
        if variant.strategy == "naive_small":
            raise RuntimeError("the embedding API failed")
        return real_write_variant(store, embedder, chunks, variant, run)

    monkeypatch.setattr(stage3, "write_variant", write_variant)

    with pytest.raises(SystemExit) as exit_info:
        stage3.main(stage3_argv())

    assert exit_info.value.code == 1
    run_dir = paths.outputs / INDEX_RUN
    report = read_json(run_dir / "index_report.json")
    assert [(entry["strategy"], entry["status"]) for entry in report] == [
        ("naive_small", "partial"),
        ("naive_large", "complete"),
    ]
    assert (report[0]["points_written"], report[0]["tokens_sent"]) == (None, None)
    assert read_json(run_dir / "run_meta.json")["stage"] == "index"
    assert "fastapi_docs__naive_small__voyage: indexing failed" in caplog.text
    assert "the embedding API failed" in caplog.text


def test_before_each_collection_is_written_the_report_marks_it_partial_and_run_meta_waits(
    paths: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    two_strategy_chunk_run(paths)
    run_dir = paths.outputs / INDEX_RUN
    real_write_variant = stage3.write_variant
    seen = []

    def write_variant(store, embedder, chunks, variant, run):
        # What a run killed now, before this collection's first write, leaves on disk.
        report = read_json(run_dir / "index_report.json")
        statuses = [(entry["strategy"], entry["status"]) for entry in report]
        seen.append((statuses, (run_dir / "run_meta.json").exists()))
        return real_write_variant(store, embedder, chunks, variant, run)

    monkeypatch.setattr(stage3, "write_variant", write_variant)

    stage3.main(stage3_argv())

    assert seen == [
        ([("naive_small", "partial")], False),
        ([("naive_small", "complete"), ("naive_large", "partial")], False),
    ]
    assert read_json(run_dir / "run_meta.json")["stage"] == "index"  # written last (C009)


def test_a_ctrl_c_leaves_the_collection_it_stopped_in_partial_and_still_writes_run_meta(
    paths: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    two_strategy_chunk_run(paths)
    real_write_variant = stage3.write_variant

    def write_variant(store, embedder, chunks, variant, run):
        if variant.strategy == "naive_large":
            raise KeyboardInterrupt  # as Ctrl-C does
        return real_write_variant(store, embedder, chunks, variant, run)

    monkeypatch.setattr(stage3, "write_variant", write_variant)

    with pytest.raises(KeyboardInterrupt):
        stage3.main(stage3_argv())

    run_dir = paths.outputs / INDEX_RUN
    report = read_json(run_dir / "index_report.json")
    assert [(entry["strategy"], entry["status"]) for entry in report] == [
        ("naive_small", "complete"),
        ("naive_large", "partial"),
    ]
    assert read_json(run_dir / "run_meta.json")["stage"] == "index"  # from the `finally`


@pytest.mark.parametrize(
    "argv",
    [
        ["--strategy", "all", "--embedder", "voyage"],
        ["--chunks-run", CHUNKS_RUN, "--strategy", "all"],
        ["--chunks-run", CHUNKS_RUN, "--strategy", "all", "--embedder", "openai"],
        ["--chunks-run", CHUNKS_RUN, "--strategy", "naive_tiny", "--embedder", "voyage"],
        ["--chunks-run", "../x", "--strategy", "all", "--embedder", "voyage"],
        [*stage3_argv(run_id="../x")],
        [*stage3_argv(), "--collection-prefix", "a__b"],
        [*stage3_argv(), "--collection-prefix", "fastapi_"],
    ],
    ids=["no-chunks-run", "no-embedder", "unknown-embedder", "unknown-strategy",
         "chunks-run-outside-outputs", "run-id-outside-outputs",
         "collection-prefix-with-double-underscore", "collection-prefix-ending-in-underscore"],
)  # fmt: skip
def test_a_bad_command_line_is_a_usage_error(
    paths: SimpleNamespace, qdrant: SimpleNamespace, argv: list[str]
) -> None:
    make_chunk_run(paths, {"naive_small": make_chunks("naive_small")})

    with pytest.raises(SystemExit) as exit_info:
        stage3.main(argv)

    assert exit_info.value.code == 2
    assert qdrant.built == []


def test_two_unknown_names_fail_once_naming_both(
    paths: SimpleNamespace, qdrant: SimpleNamespace, capsys: pytest.CaptureFixture
) -> None:
    make_chunk_run(paths, {"naive_small": make_chunks("naive_small")})

    with pytest.raises(SystemExit) as exit_info:
        stage3.main(stage3_argv(strategy="naive_tiny", embedder="openai"))

    assert exit_info.value.code == 2
    error = capsys.readouterr().err
    assert "strategy 'naive_tiny'" in error
    assert "embedder 'openai'" in error
    assert qdrant.built == []


def test_a_missing_tokenizer_exits_1_pointing_at_the_download_script(
    paths: SimpleNamespace,
    qdrant: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    make_chunk_run(paths, {"naive_small": make_chunks("naive_small")})
    monkeypatch.setattr(stage3, "cached_tokenizer_path", lambda name: None)

    with pytest.raises(SystemExit) as exit_info:
        stage3.main(stage3_argv())

    assert exit_info.value.code == 1
    assert "scripts/download_models.py" in caplog.text
    assert qdrant.built == []


def test_a_missing_chunk_run_exits_1_naming_it(
    qdrant: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        stage3.main(stage3_argv())

    assert exit_info.value.code == 1
    assert f"Chunk run {CHUNKS_RUN}" in caplog.text
    assert qdrant.built == []
