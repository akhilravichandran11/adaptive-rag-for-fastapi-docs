"""
BGE-large through OpenRouter: the semantic chunker's boundary model and the
embedder behind the ``*__bge`` collections.

BGE-large used to run on the laptop CPU through fastembed, and one full semantic
pass took 106 minutes (F016). The same model, ``baai/bge-large-en-v1.5``, is now
called on OpenRouter's OpenAI-compatible embeddings endpoint. Only its tokenizer
stays local.

BGE reads at most 512 tokens. OpenRouter's documentation says longer input is
"truncated or rejected" without saying which, so every text is cut to 512 BGE
tokens here, with BGE's own tokenizer, before it is sent. The result is the same
whatever OpenRouter does, and a long code block cannot fail a run.

    from data_ingestion.embedders import build_document_embedder

    embedder = build_document_embedder("bge")       # no key needed, no call made
    embedded = embedder.run(documents)["documents"]  # calls OpenRouter

Environment (read when the first request is made; callers load ``.env`` first):
    OPENROUTER_API_KEY       required to embed, not to build a component
    OPENROUTER_MAX_RETRIES   SDK retries on 429, timeouts and 5xx (default 5)
    OPENROUTER_TIMEOUT       seconds per request (default 120)
"""

import logging
import os
from dataclasses import replace

from haystack import Document, component
from openai import OpenAI

from data_ingestion.embedders import bge_tokenizer

logger = logging.getLogger(__name__)

BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "baai/bge-large-en-v1.5"
DEFAULT_BATCH_SIZE = 64
# BGE's input limit, counting [CLS] and [SEP]: 510 tokens of the text itself.
MAX_TOKENS = 512

# One client for every call, created on first use.
_client: OpenAI | None = None


def get_client() -> OpenAI:
    """Return the lazily created, shared OpenRouter client.

    Created on the first request, not at import or build time, so building a
    component needs no key. The SDK's own retries cover rate limits, timeouts and
    server errors (OpenRouter's 529 included), so nothing here retries.
    """
    global _client
    if _client is None:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set; add it to the project .env")
        _client = OpenAI(
            base_url=BASE_URL,
            api_key=api_key,
            max_retries=int(os.getenv("OPENROUTER_MAX_RETRIES") or "5"),
            timeout=float(os.getenv("OPENROUTER_TIMEOUT") or "120"),
        )
    return _client


def truncate_to_bge_limit(text: str, tokenizer=None) -> str:
    """Cut ``text`` to the part BGE reads: 512 tokens, ``[CLS]`` and ``[SEP]`` included.

    The cut falls at the end of the last token that fits, never inside a token, and
    a text that already fits comes back unchanged. Pass ``tokenizer`` (from
    ``bge_tokenizer()``) to avoid loading a copy per call.
    """
    tokenizer = tokenizer or bge_tokenizer()
    encoding = tokenizer.encode(text)
    if len(encoding.ids) <= MAX_TOKENS:
        return text
    # Index 0 is [CLS], so index MAX_TOKENS - 2 is the last text token that fits.
    return text[: encoding.offsets[MAX_TOKENS - 2][1]]


def embed_texts(
    texts: list[str],
    *,
    model: str = DEFAULT_MODEL,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dimension: int | None = None,
) -> list[list[float]]:
    """Embed ``texts`` on OpenRouter in batches and return one vector per text, in order.

    Every text is truncated to BGE's limit first, and the number cut is logged.
    ``dimension``, when given, is checked on every vector, so a changed model cannot
    slip into an index unnoticed.
    """
    if not texts:
        return []
    tokenizer = bge_tokenizer()
    sent = [truncate_to_bge_limit(text, tokenizer) for text in texts]
    truncated = sum(1 for before, after in zip(texts, sent, strict=True) if after != before)
    if truncated:
        logger.info("Truncated %d of %d texts to %d BGE tokens", truncated, len(texts), MAX_TOKENS)

    client = get_client()
    vectors: list[list[float]] = []
    for start in range(0, len(sent), batch_size):
        batch = sent[start : start + batch_size]
        response = client.embeddings.create(model=model, input=batch, encoding_format="float")
        data = sorted(response.data or [], key=lambda item: item.index)
        # Exactly one vector per text: a missing or repeated index would pair a
        # vector with the wrong text.
        indices = [item.index for item in data]
        if indices != list(range(len(batch))):
            raise RuntimeError(
                f"OpenRouter returned embeddings for indices {indices} for {len(batch)} texts"
            )
        for item in data:
            if dimension is not None and len(item.embedding) != dimension:
                raise RuntimeError(
                    f"{model} returned a {len(item.embedding)}-dimension vector; expected {dimension}"
                )
            vectors.append(item.embedding)
    logger.debug("Embedded %d texts with %s", len(texts), model)
    return vectors


@component
class OpenRouterDocumentEmbedder:
    """Embeds Haystack Documents with BGE on OpenRouter, like any document embedder.

    Building it only stores settings: no client, no key, no call.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        batch_size: int = DEFAULT_BATCH_SIZE,
        dimension: int | None = None,
    ) -> None:
        self.model = model
        self.batch_size = batch_size
        self.dimension = dimension

    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document]) -> dict[str, list[Document]]:
        """Return copies of ``documents`` with ``embedding`` set; the inputs are left as they were."""
        vectors = embed_texts(
            [document.content or "" for document in documents],
            model=self.model,
            batch_size=self.batch_size,
            dimension=self.dimension,
        )
        return {
            "documents": [
                replace(document, embedding=vector)
                for document, vector in zip(documents, vectors, strict=True)
            ]
        }


@component
class OpenRouterTextEmbedder:
    """Embeds one string (a question at search time) with the same model as the documents."""

    def __init__(
        self, model: str = DEFAULT_MODEL, dimension: int | None = None, prefix: str = ""
    ) -> None:
        self.model = model
        self.dimension = dimension
        self.prefix = prefix

    @component.output_types(embedding=list[float])
    def run(self, text: str) -> dict[str, list[float]]:
        """Return the vector for ``prefix + text``, truncated to BGE's limit like every document."""
        vectors = embed_texts([self.prefix + text], model=self.model, dimension=self.dimension)
        return {"embedding": vectors[0]}
