"""
Offline tests for the ``semantic`` chunking strategy.

They run on small fixtures built here and never touch the corpus, the network, or
an API key. An autouse fixture refuses any outgoing socket connection (C006), so a
call to OpenRouter, a tokenizer load that fell back to downloading from Hugging
Face, or Haystack fetching its own ``punkt_tab`` (F002), fails the test instead of
passing quietly.

BGE runs on OpenRouter, so a second autouse fixture replaces the client with a
stand-in that turns each sentence group into a "keyword vector" (one axis per test
topic). The tests therefore check the splitter's wiring, settings and length rules
with real sentence splitting and real truncation, not BGE's own sense of a topic;
that is checked by a real corpus run. The tests that split text need BGE's
tokenizer and ``punkt_tab`` in the project model cache and skip when either is
absent:

    uv run python scripts/download_models.py     # from the project root
    uv run pytest tests/data_ingestion/test_chunkers_semantic.py

The sentence splitter drops runs of blank lines (F004), so nothing here asserts a
chunk is an exact copy of its source; text survival is checked with whitespace
ignored.
"""

import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest
from haystack import Document

from data_ingestion import openrouter_embeddings
from data_ingestion.chunkers import (
    get_chunker,
    strategy_names,
)
from data_ingestion.chunkers.semantic import SPLITTER_META
from data_ingestion.embedders import cached_tokenizer_path
from data_ingestion.model_cache import register_nltk_data_dir

# Metadata a Haystack splitter writes for its own bookkeeping; chunks must not carry it.
SPLITTER_META_KEYS = ("source_id", "parent_id", "split_id", "split_idx_start", "_split_overlap")

PARENT_META = {
    "source_path": "docs/mixed__topics.md",
    "bucket": "tutorial",
    "url": "https://example.invalid/mixed/topics/",
    "overall_quality": 4.5,
}

# Two topics with nothing in common, six sentences each: with three sentences per
# group that is two groups a topic, so the change of topic falls on a group boundary.
FASTAPI_TOPIC = (
    "FastAPI reads path parameters from the URL template of each route. "
    "Declaring a path parameter as an int makes FastAPI convert and validate it. "
    "A request with a path parameter that is not a number gets a 422 response. "
    "The route decorator lists the path template with the parameter in braces. "
    "Path parameters are always required because they are part of the URL. "
    "The order of route declarations matters when two path templates overlap. "
)
BAKING_TOPIC = (
    "Sourdough bread rises with a starter of wild yeast and flour. "
    "The baker feeds the starter with fresh flour and water every day. "
    "A long cold proof in the fridge gives the loaf a sour, complex flavour. "
    "Scoring the dough with a razor lets the crust open in the hot oven. "
    "Steam during the first minutes of baking keeps the crust thin and crisp. "
    "The loaf should cool on a rack for an hour before it is sliced. "
)
FASTAPI_WORDS = ("FastAPI", "route", "path parameter")
BAKING_WORDS = ("Sourdough", "loaf", "starter", "crust")


def punkt_is_cached() -> bool:
    """Whether NLTK's sentence tokenizer is in the project model cache."""
    import nltk

    register_nltk_data_dir()
    try:
        nltk.data.find("tokenizers/punkt_tab")
    except LookupError:
        return False
    return True


# Filesystem probes, not model loads: this runs during collection, before the
# no_network fixture exists (see C006's note on collection time).
needs_models = pytest.mark.skipif(
    cached_tokenizer_path("bge") is None or not punkt_is_cached(),
    reason="semantic needs BGE's tokenizer and NLTK punkt_tab in the model cache; "
    "run scripts/download_models.py",
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every outgoing connection raise, so no test can reach an API or a CDN (C006)."""

    def refuse(*args: object, **kwargs: object):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


# Marker words per test topic. Each is one axis of the stand-in vectors below.
TOPIC_MARKERS = (
    ("fastapi", "path", "route", "parameter", "query", "url"),
    ("sourdough", "loaf", "starter", "crust", "dough", "baker", "flour"),
)


def keyword_vector(text: str) -> list[float]:
    """A stand-in for BGE: marker-word counts per topic, a constant axis, zero padding.

    Groups on the same topic point the same way and groups on different topics do
    not, which is the property the splitter's cut rule relies on. The constant axis
    keeps a group with no marker words (``"Happy coding!"``) from being a zero vector.
    """
    lowered = text.lower()
    vector = [float(sum(lowered.count(word) for word in words)) for words in TOPIC_MARKERS]
    vector.append(1.0)
    return vector + [0.0] * (1024 - len(vector))


class KeywordEmbeddings:
    """Stands in for ``client.embeddings`` and answers with keyword vectors."""

    def create(self, *, model: str, input: list[str], **kwargs: object):
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=index, embedding=keyword_vector(text))
                for index, text in enumerate(input)
            ]
        )


@pytest.fixture(autouse=True)
def stub_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the OpenRouter client, so splitting makes no paid call (C006)."""
    client = SimpleNamespace(embeddings=KeywordEmbeddings())
    monkeypatch.setattr(openrouter_embeddings, "get_client", lambda: client)


@pytest.fixture(scope="module")
def chunker():
    """One semantic chunker for the module. Building it loads nothing; the tokenizer
    loads on the first ``run``, which happens inside a test and so under ``no_network``."""
    return get_chunker("semantic")


def two_topics() -> Document:
    return Document(content=FASTAPI_TOPIC + BAKING_TOPIC, meta=dict(PARENT_META))


def without_whitespace(text: str) -> str:
    return "".join(text.split())


# --- registration (no model needed) ----------------------------------------


def test_semantic_is_registered_after_the_size_based_strategies() -> None:
    assert "semantic" in strategy_names()
    assert strategy_names().index("semantic") > strategy_names().index("recursive")


def test_importing_the_package_does_not_load_the_semantic_module() -> None:
    # A fresh interpreter, so modules this test session already imported do not count.
    code = (
        "import sys; import data_ingestion.chunkers; "
        "print('data_ingestion.chunkers.semantic' in sys.modules, 'fastembed' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.split() == ["False", "False"]


def test_the_settings_are_the_recorded_decision() -> None:
    settings = get_chunker("semantic").settings
    assert (settings.strategy, settings.size, settings.overlap, settings.unit) == (
        "semantic",
        1_000,
        0,
        "char",
    )
    assert SPLITTER_META == {
        "boundary_embedder": "bge",
        "percentile": 0.95,
        "sentences_per_group": 3,
        "min_length": 50,
    }


# --- edge cases that never reach the splitter (no model needed) ------------


@pytest.mark.parametrize("content", ["", None, "   \n\n  "], ids=["empty", "none", "whitespace"])
def test_a_document_without_content_produces_no_chunks(chunker, content: str | None) -> None:
    document = Document(content=content, meta={"source_path": "docs/empty.md"})
    assert chunker.run([document])["documents"] == []


def test_two_documents_sharing_a_source_path_are_refused(chunker) -> None:
    documents = [
        Document(content="First.", meta={"source_path": "docs/same.md"}),
        Document(content="Second.", meta={"source_path": "docs/same.md"}),
    ]
    with pytest.raises(ValueError, match="docs/same.md"):
        chunker.run(documents)


# --- splitting (needs BGE and punkt_tab) -----------------------------------


@needs_models
def test_chunks_carry_the_shared_fields_and_the_splitter_settings(chunker) -> None:
    chunks = chunker.run([two_topics()])["documents"]

    assert chunks
    for index, chunk in enumerate(chunks):
        meta = chunk.meta
        assert meta["strategy"] == "semantic"
        assert meta["chunk_index"] == index
        assert meta["chunk_count"] == len(chunks)
        assert meta["source_path"] == PARENT_META["source_path"]
        assert (meta["chunk_size"], meta["chunk_overlap"], meta["chunk_unit"]) == (1_000, 0, "char")
        for key, value in SPLITTER_META.items():
            assert meta[key] == value
        for key, value in PARENT_META.items():
            assert meta[key] == value
        for key in SPLITTER_META_KEYS:
            assert key not in meta
        assert chunk.embedding is None


@needs_models
def test_a_cut_falls_between_two_different_topics(chunker) -> None:
    chunks = chunker.run([two_topics()])["documents"]

    assert len(chunks) >= 2
    for chunk in chunks:
        content = chunk.content or ""
        about_fastapi = any(word in content for word in FASTAPI_WORDS)
        about_baking = any(word in content for word in BAKING_WORDS)
        assert not (about_fastapi and about_baking), f"one chunk mixes both topics: {content!r}"


@needs_models
def test_the_same_input_gives_the_same_chunks(chunker) -> None:
    first = chunker.run([two_topics()])["documents"]
    second = get_chunker("semantic").run([two_topics()])["documents"]

    assert [c.content for c in first] == [c.content for c in second]
    assert [c.meta for c in first] == [c.meta for c in second]
    assert [c.id for c in first] == [c.id for c in second]


@needs_models
def test_no_source_text_is_lost_or_reordered(chunker) -> None:
    parent = two_topics()
    chunks = chunker.run([parent])["documents"]

    joined = "".join(without_whitespace(c.content or "") for c in chunks)
    assert joined == without_whitespace(parent.content or "")


@needs_models
def test_each_parent_is_chunked_on_its_own(chunker) -> None:
    other = Document(content=BAKING_TOPIC, meta={"source_path": "docs/baking.md"})
    chunks = chunker.run([two_topics(), other])["documents"]

    for path in (PARENT_META["source_path"], "docs/baking.md"):
        own = [c for c in chunks if c.meta["source_path"] == path]
        assert [c.meta["chunk_index"] for c in own] == list(range(len(own)))
        assert {c.meta["chunk_count"] for c in own} == {len(own)}


@needs_models
def test_a_short_last_piece_is_merged_into_the_one_before_it(chunker) -> None:
    # Pins the behaviour semantic.py's docstring describes (F016): since Haystack
    # 3.2.0 a short closing line after one paragraph is merged into that paragraph
    # instead of standing alone below min_length (3.0.0 left it on its own). If
    # Haystack changes this again, this fails and the docstring and F016 need updating.
    paragraph = FASTAPI_TOPIC + (
        "Query parameters are read from the query string instead of the path. "
        "A default value makes a query parameter optional in the request. "
        "Type hints tell FastAPI how to convert the query string values. "
    )
    document = Document(content=paragraph + "Happy coding!", meta={"source_path": "docs/tail.md"})

    chunks = chunker.run([document])["documents"]

    assert len(chunks) == 1
    assert chunks[0].content.rstrip().endswith("Happy coding!")
    assert len(chunks[0].content) >= SPLITTER_META["min_length"]
