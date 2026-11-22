"""
Tests for ``data_ingestion.openrouter_embeddings``: truncation to BGE's limit,
batching, the response checks, and the two Haystack components.

No test reaches OpenRouter. An autouse fixture refuses any outgoing socket
connection (C006, no-network form), and every test that embeds replaces
``get_client`` with a fake that records what it was sent. Tests that truncate need
BGE's tokenizer in the project cache and skip when it is absent:

    uv run python scripts/download_models.py     # from the project root
    uv run pytest tests/data_ingestion/test_openrouter_embeddings.py
"""

import logging
import socket
from types import SimpleNamespace

import pytest
from haystack import Document

from data_ingestion import openrouter_embeddings
from data_ingestion.embedders import (
    bge_tokenizer,
    build_document_embedder,
    build_query_embedder,
    cached_tokenizer_path,
    count_tokens,
)
from data_ingestion.openrouter_embeddings import (
    MAX_TOKENS,
    OpenRouterDocumentEmbedder,
    OpenRouterTextEmbedder,
    embed_texts,
    get_client,
    truncate_to_bge_limit,
)

# A filesystem probe, not a tokenizer load: this runs at collection time, before
# the no_network fixture exists (see C006).
needs_bge = pytest.mark.skipif(
    cached_tokenizer_path("bge") is None,
    reason="BGE's tokenizer is not in the project model cache; run scripts/download_models.py",
)

DIMENSION = 1024


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every outgoing connection raise, so no test can reach OpenRouter (C006)."""

    def refuse(*args: object, **kwargs: object):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


class FakeEmbeddings:
    """Stands in for ``client.embeddings``: records each request, answers with fixed vectors.

    Each text's vector starts with its position in the whole run, so a test can
    tell which vector belongs to which text. ``reverse`` returns a batch's items
    out of ``index`` order; ``drop`` and ``dimension`` break the response on purpose.
    """

    def __init__(
        self,
        *,
        reverse: bool = False,
        drop: int = 0,
        dimension: int = DIMENSION,
        same_index: bool = False,
    ):
        self.requests: list[dict[str, object]] = []
        self.reverse = reverse
        self.same_index = same_index
        self.drop = drop
        self.dimension = dimension
        self._seen = 0

    def create(self, *, model: str, input: list[str], **kwargs: object):
        assert kwargs == {"encoding_format": "float"}
        self.requests.append({"model": model, "input": list(input)})
        items = []
        for index, _ in enumerate(input):
            vector = [float(self._seen + index)] + [0.0] * (self.dimension - 1)
            items.append(SimpleNamespace(index=0 if self.same_index else index, embedding=vector))
        self._seen += len(input)
        if self.reverse:
            items.reverse()
        return SimpleNamespace(data=items[: len(items) - self.drop])


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeEmbeddings:
    embeddings = FakeEmbeddings()
    monkeypatch.setattr(
        openrouter_embeddings, "get_client", lambda: SimpleNamespace(embeddings=embeddings)
    )
    return embeddings


# --- truncation (real tokenizer) ---------------------------------------------


@needs_bge
def test_a_long_text_is_cut_to_exactly_bge_s_limit():
    long_text = "word " * 600  # 602 tokens with [CLS] and [SEP]
    assert count_tokens("bge", [long_text]) == [602]

    cut = truncate_to_bge_limit(long_text)

    assert count_tokens("bge", [cut]) == [MAX_TOKENS] == [512]
    assert long_text.startswith(cut)


@needs_bge
def test_mixed_prose_and_code_is_cut_to_the_limit_on_a_token_boundary():
    pieces = ["unbelievable", 'app.get("/items/{id}")', "response_model=Item", "über", "3.11"]
    text = " ".join(pieces[i % len(pieces)] for i in range(400))
    assert count_tokens("bge", [text])[0] > MAX_TOKENS

    cut = truncate_to_bge_limit(text)

    assert count_tokens("bge", [cut]) == [MAX_TOKENS]
    assert text.startswith(cut)


@needs_bge
def test_a_text_that_fits_is_returned_untouched():
    text = "FastAPI reads path parameters from the URL."
    assert truncate_to_bge_limit(text) is text
    exactly_full = "word " * 510  # 512 tokens: fits exactly
    assert truncate_to_bge_limit(exactly_full) is exactly_full


@needs_bge
def test_a_shared_tokenizer_gives_the_same_cut():
    tokenizer = bge_tokenizer()
    text = "word " * 600
    assert truncate_to_bge_limit(text, tokenizer) == truncate_to_bge_limit(text)


# --- embed_texts (client stubbed) ----------------------------------------------


@needs_bge
def test_texts_are_truncated_before_they_are_sent_and_the_count_is_logged(
    fake: FakeEmbeddings, caplog: pytest.LogCaptureFixture
):
    texts = ["word " * 600, "a short text", "word " * 700]
    with caplog.at_level(logging.INFO, logger="data_ingestion.openrouter_embeddings"):
        embed_texts(texts)

    sent = fake.requests[0]["input"]
    assert count_tokens("bge", sent) == [512, 5, 512]  # "a short text" is 3 tokens + 2
    assert sent[1] == "a short text"
    assert "Truncated 2 of 3 texts to 512 BGE tokens" in caplog.text


@needs_bge
def test_nothing_is_logged_as_truncated_when_every_text_fits(
    fake: FakeEmbeddings, caplog: pytest.LogCaptureFixture
):
    with caplog.at_level(logging.INFO, logger="data_ingestion.openrouter_embeddings"):
        embed_texts(["one", "two"])
    assert "Truncated" not in caplog.text


@needs_bge
def test_texts_are_sent_in_batches_with_the_model_named(fake: FakeEmbeddings):
    texts = [f"text {i}" for i in range(5)]

    vectors = embed_texts(texts, model="baai/bge-large-en-v1.5", batch_size=2)

    assert [request["input"] for request in fake.requests] == [
        ["text 0", "text 1"],
        ["text 2", "text 3"],
        ["text 4"],
    ]
    assert {request["model"] for request in fake.requests} == {"baai/bge-large-en-v1.5"}
    assert [vector[0] for vector in vectors] == [0.0, 1.0, 2.0, 3.0, 4.0]


@needs_bge
def test_vectors_follow_the_input_order_even_when_the_response_does_not(
    monkeypatch: pytest.MonkeyPatch,
):
    embeddings = FakeEmbeddings(reverse=True)
    monkeypatch.setattr(
        openrouter_embeddings, "get_client", lambda: SimpleNamespace(embeddings=embeddings)
    )

    vectors = embed_texts(["a", "b", "c"], batch_size=2)

    assert [vector[0] for vector in vectors] == [0.0, 1.0, 2.0]


@needs_bge
def test_a_response_missing_a_vector_is_an_error(monkeypatch: pytest.MonkeyPatch):
    embeddings = FakeEmbeddings(drop=1)
    monkeypatch.setattr(
        openrouter_embeddings, "get_client", lambda: SimpleNamespace(embeddings=embeddings)
    )
    with pytest.raises(RuntimeError, match=r"indices \[0\] for 2 texts"):
        embed_texts(["a", "b"])


@needs_bge
def test_a_response_repeating_an_index_is_an_error(monkeypatch: pytest.MonkeyPatch):
    """The right count is not enough: two vectors both labelled 0 would be paired wrongly."""
    embeddings = FakeEmbeddings(same_index=True)
    monkeypatch.setattr(
        openrouter_embeddings, "get_client", lambda: SimpleNamespace(embeddings=embeddings)
    )
    with pytest.raises(RuntimeError, match=r"indices \[0, 0\] for 2 texts"):
        embed_texts(["a", "b"])


@needs_bge
def test_a_vector_of_the_wrong_size_is_an_error(monkeypatch: pytest.MonkeyPatch):
    embeddings = FakeEmbeddings(dimension=768)
    monkeypatch.setattr(
        openrouter_embeddings, "get_client", lambda: SimpleNamespace(embeddings=embeddings)
    )
    with pytest.raises(RuntimeError, match="768-dimension vector; expected 1024"):
        embed_texts(["a"], dimension=DIMENSION)


def test_no_texts_means_no_request(monkeypatch: pytest.MonkeyPatch):
    def no_client():
        raise AssertionError("embed_texts([]) asked for a client")

    monkeypatch.setattr(openrouter_embeddings, "get_client", no_client)
    assert embed_texts([]) == []


# --- the client ------------------------------------------------------------------


def test_the_client_needs_the_key_and_says_so(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(openrouter_embeddings, "_client", None)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        get_client()


def test_the_client_points_at_openrouter_and_is_shared(monkeypatch: pytest.MonkeyPatch):
    """Creating the client makes no request; only an embeddings call would."""
    monkeypatch.setattr(openrouter_embeddings, "_client", None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    monkeypatch.setenv("OPENROUTER_MAX_RETRIES", "3")
    monkeypatch.setenv("OPENROUTER_TIMEOUT", "30")

    client = get_client()

    assert str(client.base_url).rstrip("/") == "https://openrouter.ai/api/v1"
    assert client.max_retries == 3
    assert client.timeout == 30.0
    assert get_client() is client


def test_blank_retry_and_timeout_settings_fall_back_to_the_defaults(
    monkeypatch: pytest.MonkeyPatch,
):
    """A blank line in .env (``OPENROUTER_TIMEOUT=``) must not crash the first call."""
    monkeypatch.setattr(openrouter_embeddings, "_client", None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    monkeypatch.setenv("OPENROUTER_MAX_RETRIES", "")
    monkeypatch.setenv("OPENROUTER_TIMEOUT", "")

    client = get_client()

    assert client.max_retries == 5
    assert client.timeout == 120.0


# --- the components ----------------------------------------------------------------


def test_building_the_bge_components_needs_no_key_and_creates_no_client(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(openrouter_embeddings, "_client", None)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    document_embedder = build_document_embedder("bge")
    text_embedder = build_query_embedder("bge")

    assert isinstance(document_embedder, OpenRouterDocumentEmbedder)
    assert isinstance(text_embedder, OpenRouterTextEmbedder)
    for embedder in (document_embedder, text_embedder):
        assert embedder.model == "baai/bge-large-en-v1.5"
        assert embedder.dimension == DIMENSION
    assert openrouter_embeddings._client is None


@needs_bge
def test_the_document_embedder_returns_embedded_copies(fake: FakeEmbeddings):
    documents = [
        Document(content="first", meta={"source_path": "docs/a.md"}),
        Document(content="second", meta={"source_path": "docs/b.md"}),
    ]

    out = build_document_embedder("bge").run(documents)["documents"]

    assert fake.requests[0]["input"] == ["first", "second"]  # no query instruction (D44)
    assert [d.content for d in out] == ["first", "second"]
    assert [d.meta for d in out] == [d.meta for d in documents]
    assert [d.embedding[0] for d in out] == [0.0, 1.0]
    assert all(len(d.embedding) == DIMENSION for d in out)
    assert all(d.embedding is None for d in documents)


@needs_bge
def test_the_bge_text_embedder_sends_one_question_after_its_instruction(fake: FakeEmbeddings):
    question = "How do I make a query parameter optional?"
    out = build_query_embedder("bge").run(question)

    assert len(out["embedding"]) == DIMENSION
    assert fake.requests == [
        {
            "model": "baai/bge-large-en-v1.5",
            "input": ["Represent this sentence for searching relevant passages: " + question],
        }
    ]


@needs_bge
def test_the_text_embedder_sends_its_prefix_before_the_text(fake: FakeEmbeddings):
    OpenRouterTextEmbedder(prefix="P: ").run("q")

    assert fake.requests[0]["input"] == ["P: q"]


@needs_bge
def test_a_text_embedder_without_a_prefix_sends_the_text_alone(fake: FakeEmbeddings):
    OpenRouterTextEmbedder().run("q")

    assert fake.requests[0]["input"] == ["q"]


@needs_bge
def test_a_long_prefixed_question_keeps_its_prefix_within_the_limit(fake: FakeEmbeddings):
    OpenRouterTextEmbedder(prefix="P: ").run("word " * 600)

    sent = fake.requests[0]["input"][0]
    assert sent.startswith("P: word")
    assert count_tokens("bge", [sent]) == [MAX_TOKENS]
