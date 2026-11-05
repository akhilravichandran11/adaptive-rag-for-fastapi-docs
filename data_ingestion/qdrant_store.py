"""
Qdrant settings and document stores, shared by the ingestion stages.

Moved out of ``stage1_setup_qdrant.py`` so that a stage imports a module, never
another stage. Stage 1 keeps its command line and its probe smoke test. Stage 3
builds one collection per embedder, so ``load_settings`` takes a vector size per
collection and ``build_document_store`` takes the payload indexes to create.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from haystack.utils import Secret
from haystack_integrations.document_stores.qdrant import QdrantDocumentStore
from qdrant_client import QdrantClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# HNSW graph settings, applied only when a collection is created (a new name or
# --recreate); attaching to an existing collection ignores them. m=16 is Qdrant's
# default (links per vector: more means better recall and more memory).
# ef_construct=200 doubles the default of 100 (candidates weighed per insert:
# more means a better graph and slower indexing).
HNSW_TUNING = {"m": 16, "ef_construct": 200}

# Seconds a Qdrant request may take when QDRANT_TIMEOUT is unset. Without one the
# client falls back to httpx's 5 s, which timed out 2048-dim upserts (~2.6 MB each).
DEFAULT_QDRANT_TIMEOUT = 60


@dataclass
class QdrantSettings:
    """Connection settings for the document store, built and validated by ``load_settings``.

    The API key is not stored here; ``build_document_store`` reads it from the
    environment so it never sits in a plain attribute.
    """

    url: str
    collection: str
    embedding_dim: int
    timeout: int = DEFAULT_QDRANT_TIMEOUT  # seconds, for every Qdrant request


def load_settings(
    collection_override: str | None = None,
    dimension_override: int | None = None,
) -> QdrantSettings:
    """Read config from ``PROJECT_ROOT/.env`` and fail loudly if anything is wrong.

    The overrides replace ``QDRANT_COLLECTION`` and ``EMBEDDING_DIMENSION``; with
    both given, neither variable is required. ``QDRANT_TIMEOUT`` is optional: unset
    or empty means ``DEFAULT_QDRANT_TIMEOUT`` seconds. Raises ``ValueError`` naming
    every missing variable, or naming ``EMBEDDING_DIMENSION`` or ``QDRANT_TIMEOUT``
    and its value when it is not a positive integer.
    """
    load_dotenv(PROJECT_ROOT / ".env")

    url = os.getenv("QDRANT_URL")
    collection = collection_override or os.getenv("QDRANT_COLLECTION")
    # `is not None`, not truthiness: an override of 0 must fail the check below
    # instead of silently falling back to .env.
    raw_dim = (
        str(dimension_override)
        if dimension_override is not None
        else os.getenv("EMBEDDING_DIMENSION")
    )

    required = {
        "QDRANT_URL": url,
        "QDRANT_API_KEY": os.getenv("QDRANT_API_KEY"),
        "QDRANT_COLLECTION": collection,
        "EMBEDDING_DIMENSION": raw_dim,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(f"Qdrant config incomplete; set: {', '.join(missing)}")

    try:
        embedding_dim = int(raw_dim)
    except ValueError:
        raise ValueError(
            f"EMBEDDING_DIMENSION must be a positive integer; got {raw_dim!r}"
        ) from None
    if embedding_dim <= 0:
        raise ValueError(f"EMBEDDING_DIMENSION must be a positive integer; got {raw_dim!r}")

    raw_timeout = os.getenv("QDRANT_TIMEOUT") or str(DEFAULT_QDRANT_TIMEOUT)
    try:
        timeout = int(raw_timeout)
    except ValueError:
        raise ValueError(
            f"QDRANT_TIMEOUT must be a positive integer; got {raw_timeout!r}"
        ) from None
    if timeout <= 0:
        raise ValueError(f"QDRANT_TIMEOUT must be a positive integer; got {raw_timeout!r}")

    return QdrantSettings(
        url=url, collection=collection, embedding_dim=embedding_dim, timeout=timeout
    )


def collection_exists(settings: QdrantSettings) -> bool:
    """Ask Qdrant whether the collection already exists.

    Must run before ``build_document_store`` is used: the Haystack store creates a
    missing collection as soon as it first connects, so this check uses its own client.
    """
    client = QdrantClient(
        url=settings.url,
        api_key=Secret.from_env_var("QDRANT_API_KEY").resolve_value(),
        timeout=settings.timeout,
    )
    try:
        return client.collection_exists(settings.collection)
    finally:
        client.close()


def build_document_store(
    settings: QdrantSettings,
    *,
    recreate: bool = False,
    return_embedding: bool = False,
    payload_fields_to_index: list[dict] | None = None,
) -> QdrantDocumentStore:
    """Open a QdrantDocumentStore for the given settings.

    By default this attaches to the collection and keeps its data; pass
    ``recreate=True`` to wipe and rebuild it. ``return_embedding`` only controls
    whether returned documents carry their stored vectors. Similarity search runs
    inside Qdrant either way, so leave it off unless the caller reads the vectors.

    ``payload_fields_to_index`` (e.g. ``[{"field_name": "meta.bucket",
    "field_schema": "keyword"}]``) is created when the collection is created or
    recreated, like ``HNSW_TUNING``; attaching to an existing collection ignores it.
    """
    return QdrantDocumentStore(
        url=settings.url,
        index=settings.collection,
        api_key=Secret.from_env_var("QDRANT_API_KEY"),
        embedding_dim=settings.embedding_dim,
        recreate_index=recreate,
        return_embedding=return_embedding,
        wait_result_from_api=True,  # confirm writes before returning
        timeout=settings.timeout,
        hnsw_config=HNSW_TUNING,
        payload_fields_to_index=payload_fields_to_index,
    )
