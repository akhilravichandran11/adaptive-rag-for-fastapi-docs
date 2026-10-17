"""
The two embedders the chunking study compares, and how much text each one loses.

One registry, ``EMBEDDERS``, knows each embedder's model, vector dimension, and
input limit, and builds its Haystack components:

    bge      BAAI/bge-large-en-v1.5   1024 dims     512 tokens   OpenRouter API (tokenizer local)
    voyage   voyage-4-lite            2048 dims   32000 tokens   Voyage AI API

BGE ran locally through fastembed until IDX-12; a full semantic pass took 106
minutes on a laptop CPU, so it now runs on OpenRouter (``openrouter_embeddings.py``).
The local path was removed in IDX-14; only BGE's tokenizer stays on disk.

These values live here rather than in the environment on purpose. The study's
results are only comparable if every run indexes with the same models at the same
dimensions, so a stray ``.env`` edit must not change them silently; the indexing
stage (build step 3) will copy the model and dimension into ``run_meta.json`` for
each run.

BGE reads at most 512 tokens and drops the rest of a chunk without saying so, which
is a result of the study rather than a problem to design away: the larger chunking
strategies deliberately exceed it. ``truncation_report`` measures that loss before
anything is indexed — exactly, using BGE's own tokenizer loaded offline from the
project model cache, and for Voyage as a labelled estimate, since counting its
tokens would mean calling the API.

    from data_ingestion.embedders import EMBEDDERS, build_document_embedder, truncation_report

    embedder = build_document_embedder("bge")
    for stats in truncation_report(chunk_texts).values():
        logger.info("%s", stats.summary())

Nothing here calls an API or downloads anything: both embedders' components only
hold settings until they are run, and BGE's tokenizer is read from the cache filled
by ``scripts/download_models.py`` (see ``model_cache.py``).
"""

from __future__ import annotations

import copy
import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from data_ingestion.model_cache import fastembed_cache_dir

logger = logging.getLogger(__name__)

# Tokens per word used for Voyage, whose tokenizer is not available offline. The
# same ratio produced the chunk-size estimates in the build plan, so the numbers
# reported here stay comparable with those.
VOYAGE_TOKENS_PER_WORD = 1.3


@dataclass(frozen=True)
class TokenizerSource:
    """Where an API-run model's tokenizer is downloaded from, pinned to one snapshot."""

    repo: str
    filename: str
    revision: str
    sha256: str


@dataclass(frozen=True)
class EmbedderSpec:
    """One embedder's identity, as the study fixes it.

    ``backend`` chooses which components are built and how tokens are counted, so a
    new entry has to say which library runs it rather than falling into a default.
    ``exact_tokens`` says whether that backend's counts come from the model's real
    tokenizer; it is carried into every ``TruncationStats`` so a report can never be
    read as exact when it is not.
    """

    name: str
    model: str
    dimension: int
    max_tokens: int
    backend: str  # "openrouter" or "voyage" (both APIs)
    exact_tokens: bool
    api_model: str | None = None  # the provider's name for ``model``, where it differs
    tokenizer: TokenizerSource | None = None  # local tokenizer for an API-run model
    query_prefix: str = ""  # sent before every query, never before a document


EMBEDDERS: dict[str, EmbedderSpec] = {
    "bge": EmbedderSpec(
        name="bge",
        model="BAAI/bge-large-en-v1.5",
        dimension=1024,
        max_tokens=512,
        backend="openrouter",
        exact_tokens=True,
        # Ran locally through fastembed until IDX-12; a full semantic pass took 106
        # minutes on a laptop CPU (F016), so it now runs on OpenRouter.
        api_model="baai/bge-large-en-v1.5",
        # The tokenizer fastembed used, from the snapshot already cached, so token
        # counts do not change with the switch.
        tokenizer=TokenizerSource(
            repo="qdrant/bge-large-en-v1.5-onnx",
            filename="tokenizer.json",
            revision="dc76b2c078fc38f0d243233d0ab0b51de925557e",
            sha256="d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
        ),
        # BGE's model card asks for this before queries (huggingface.co/BAAI/bge-large-en-v1.5).
        query_prefix="Represent this sentence for searching relevant passages: ",
    ),
    "voyage": EmbedderSpec(
        name="voyage",
        model="voyage-4-lite",
        dimension=2048,
        # voyage-4-lite takes 32k tokens and supports 256/512/1024/2048 output
        # dimensions (default 1024): https://docs.voyageai.com/docs/embeddings
        max_tokens=32_000,
        backend="voyage",
        exact_tokens=False,
    ),
}


def _unsupported_backend(spec: EmbedderSpec) -> ValueError:
    """The error raised when a registry entry names a backend nothing here handles.

    Better a loud failure than a silent fallback: guessing would mean building a
    Voyage component for a local model, and a paid call with a model Voyage has
    never heard of.
    """
    return ValueError(
        f"Embedder {spec.name!r} names backend {spec.backend!r}, which this module "
        "does not build; supported backends: openrouter, voyage"
    )


def get_spec(name: str) -> EmbedderSpec:
    """Look up an embedder by registry name.

    Raises ``ValueError`` naming the known embedders when ``name`` is not one of them.
    """
    try:
        return EMBEDDERS[name]
    except KeyError:
        known = ", ".join(sorted(EMBEDDERS))
        raise ValueError(f"Unknown embedder {name!r}; known embedders: {known}") from None


def build_document_embedder(name: str):
    """Build the component that embeds documents during indexing.

    Both the OpenRouter (BGE) and Voyage components hold their settings until they
    are run, so building one makes no API call and needs no key.
    """
    spec = get_spec(name)
    if spec.backend == "openrouter":
        from data_ingestion.openrouter_embeddings import OpenRouterDocumentEmbedder

        return OpenRouterDocumentEmbedder(
            model=spec.api_model or spec.model, dimension=spec.dimension
        )

    if spec.backend == "voyage":
        from haystack_integrations.components.embedders.voyage_embedders import (
            VoyageDocumentEmbedder,
        )

        # truncate defaults to True: Voyage silently cuts anything past its 32k limit
        # rather than failing. truncation_report says how often that would happen.
        # "document" here pairs with "query" in build_text_embedder (D39).
        return VoyageDocumentEmbedder(
            model=spec.model, output_dimension=spec.dimension, input_type="document"
        )

    raise _unsupported_backend(spec)


def build_text_embedder(name: str):
    """Build the component that embeds a single string (a query at search time).

    Must be the same model as ``build_document_embedder(name)``, or queries land in
    a different space from the documents they search.
    """
    spec = get_spec(name)
    if spec.backend == "openrouter":
        from data_ingestion.openrouter_embeddings import OpenRouterTextEmbedder

        return OpenRouterTextEmbedder(
            model=spec.api_model or spec.model,
            dimension=spec.dimension,
            prefix=spec.query_prefix,
        )

    if spec.backend == "voyage":
        from haystack_integrations.components.embedders.voyage_embedders import VoyageTextEmbedder

        return VoyageTextEmbedder(
            model=spec.model, output_dimension=spec.dimension, input_type="query"
        )

    raise _unsupported_backend(spec)


def cached_tokenizer_path(name: str) -> Path | None:
    """Where the named embedder's tokenizer sits in the project cache, or ``None``.

    A filesystem lookup only — it never downloads and never loads the model, so it
    is also the cheap way to ask whether the offline tokenizer is available (for a
    diagnostic, or to skip a test). ``None`` means "not cached", and for Voyage,
    which has no local tokenizer, "there is none to have".

    BGE's tokenizer is the file pinned in its registry entry (repo, filename,
    revision). Raises ``ValueError`` for a backend this module does not handle.
    """
    spec = get_spec(name)
    if spec.backend == "voyage":
        return None
    if spec.backend != "openrouter":
        raise _unsupported_backend(spec)
    if spec.tokenizer is None:
        raise ValueError(
            f"Embedder {spec.name!r} runs on {spec.backend} but names no tokenizer source"
        )

    from huggingface_hub import try_to_load_from_cache

    path = try_to_load_from_cache(
        repo_id=spec.tokenizer.repo,
        filename=spec.tokenizer.filename,
        revision=spec.tokenizer.revision,
        cache_dir=fastembed_cache_dir(),
    )
    return Path(path) if isinstance(path, str) else None


@lru_cache(maxsize=4)
def _tokenizer_from_file(path: str):
    """Read one tokenizer from disk, keyed by path so a changed cache is reloaded."""
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(path)
    # The text's true length is what the report needs, not the window the model
    # would see, so neither truncation nor padding is enabled here.
    tokenizer.no_truncation()
    tokenizer.no_padding()
    logger.debug("Loaded a tokenizer from %s", path)
    return tokenizer


def _local_tokenizer(spec: EmbedderSpec):
    """The cached tokenizer for an embedder with a local one, or a pointer to the download script."""
    path = cached_tokenizer_path(spec.name)
    if path is None:
        raise FileNotFoundError(
            f"{spec.model}'s tokenizer is not in the model cache ({fastembed_cache_dir()}). "
            "Run 'uv run python scripts/download_models.py' from the project root first."
        )
    return _tokenizer_from_file(str(path))


def bge_tokenizer():
    """BGE's own tokenizer, from the project model cache, without touching the network.

    This is the tokenizer fastembed used when BGE ran locally, and the one
    ``openrouter_embeddings.truncate_to_bge_limit`` cuts with before a text is sent.
    Its counts include the ``[CLS]`` and ``[SEP]`` tokens that count against the
    512-token budget.

    Raises ``FileNotFoundError`` pointing at the download script when the tokenizer
    has not been cached yet. The load is memoized per cache folder, so moving
    ``FASTEMBED_CACHE_PATH`` mid-process reloads rather than returning the tokenizer
    from the folder that is no longer in use.

    Each call returns a private copy. A caller may turn on truncation or padding for
    its own use without changing the counts ``count_tokens`` reports, which read the
    shared, unmodified tokenizer.
    """
    return copy.deepcopy(_local_tokenizer(EMBEDDERS["bge"]))


def count_tokens(name: str, texts: Iterable[str]) -> list[int]:
    """Count the tokens each text costs the named embedder.

    Exact for BGE (its real tokenizer, special tokens included); for
    Voyage an estimate of ``VOYAGE_TOKENS_PER_WORD`` per whitespace-separated word,
    rounded up. ``get_spec(name).exact_tokens`` tells the two apart.
    """
    spec = get_spec(name)
    texts = list(texts)
    if spec.backend == "openrouter":
        if not texts:
            return []
        encodings = _local_tokenizer(spec).encode_batch(texts)
        return [len(encoding.ids) for encoding in encodings]
    if spec.backend == "voyage":
        return [math.ceil(len(text.split()) * VOYAGE_TOKENS_PER_WORD) for text in texts]
    raise _unsupported_backend(spec)


@dataclass(frozen=True)
class TruncationStats:
    """How much of a set of chunks one embedder would read, and how much it would drop."""

    embedder: str
    model: str
    max_tokens: int
    exact: bool
    chunk_count: int
    chunks_over_limit: int
    tokens_lost: int
    largest_chunk_tokens: int

    @property
    def percent_over_limit(self) -> float:
        """Share of chunks whose tail the embedder would drop, as a percentage."""
        if not self.chunk_count:
            return 0.0
        return 100.0 * self.chunks_over_limit / self.chunk_count

    def summary(self) -> str:
        """One line for a log or an indexing report, always saying how tokens were counted."""
        basis = "exact token counts" if self.exact else "estimated token counts"
        return (
            f"{self.embedder} ({self.model}, limit {self.max_tokens} tokens): "
            f"{self.chunks_over_limit}/{self.chunk_count} chunks over the limit "
            f"({self.percent_over_limit:.1f}%), {self.tokens_lost} tokens dropped, "
            f"largest chunk {self.largest_chunk_tokens} tokens — {basis}"
        )

    def as_dict(self) -> dict[str, object]:
        """Plain values for ``run_meta.json`` and the indexing report."""
        return {
            "embedder": self.embedder,
            "model": self.model,
            "max_tokens": self.max_tokens,
            "exact": self.exact,
            "chunk_count": self.chunk_count,
            "chunks_over_limit": self.chunks_over_limit,
            "percent_over_limit": round(self.percent_over_limit, 2),
            "tokens_lost": self.tokens_lost,
            "largest_chunk_tokens": self.largest_chunk_tokens,
        }


def truncation_stats(name: str, texts: Iterable[str]) -> TruncationStats:
    """Measure what the named embedder would drop from ``texts``.

    ``tokens_lost`` counts only the overflow: for every chunk longer than the limit,
    how many of its tokens the embedder would never read.
    """
    spec = get_spec(name)
    counts = count_tokens(spec.name, texts)
    over = [count for count in counts if count > spec.max_tokens]
    return TruncationStats(
        embedder=spec.name,
        model=spec.model,
        max_tokens=spec.max_tokens,
        exact=spec.exact_tokens,
        chunk_count=len(counts),
        chunks_over_limit=len(over),
        tokens_lost=sum(count - spec.max_tokens for count in over),
        largest_chunk_tokens=max(counts, default=0),
    )


def truncation_report(
    texts: Iterable[str], names: Iterable[str] | None = None
) -> dict[str, TruncationStats]:
    """Measure the same chunks against every embedder (or just ``names``).

    The texts are read once so a generator of chunks can be passed in. An empty
    ``names`` means an empty report, not every embedder: a caller that filtered its
    list down to nothing asked for nothing.
    """
    texts = list(texts)
    selected = EMBEDDERS if names is None else list(names)
    return {name: truncation_stats(name, texts) for name in selected}
