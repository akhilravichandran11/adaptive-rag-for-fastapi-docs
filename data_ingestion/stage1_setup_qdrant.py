"""
Qdrant document-store bootstrap for the FastAPI Q&A RAG project.

Stands up (and optionally smoke-tests) a Haystack ``QdrantDocumentStore``. The
collection name comes from the environment or a per-run ``--collection``
override. A run attaches to the collection and keeps its data; only an explicit
``--recreate`` drops and rebuilds it.

    python stage1_setup_qdrant.py                                    # attach, collection from .env
    python stage1_setup_qdrant.py --collection tmp_test              # attach to tmp_test
    python stage1_setup_qdrant.py --collection tmp_test --recreate   # wipe and rebuild tmp_test
    python stage1_setup_qdrant.py --collection tmp_test 2>&1 | tee setup.log   # logs go to stderr
"""

from __future__ import annotations

import argparse
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from haystack.utils import Secret
from haystack_integrations.document_stores.qdrant import QdrantDocumentStore
from qdrant_client import QdrantClient

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_LABEL = "FastAPI Q&A RAG system"

# HNSW graph settings, applied only when a collection is created (a new name or
# --recreate); attaching to an existing collection ignores them. m=16 is Qdrant's
# default (links per vector: more means better recall and more memory).
# ef_construct=200 doubles the default of 100 (candidates weighed per insert:
# more means a better graph and slower indexing).
HNSW_TUNING = {"m": 16, "ef_construct": 200}


@dataclass
class QdrantSettings:
    """Connection settings for the document store, built and validated by ``load_settings``.

    The API key is not stored here; ``build_document_store`` reads it from the
    environment so it never sits in a plain attribute.
    """

    url: str
    collection: str
    embedding_dim: int


def load_settings(collection_override: str | None = None) -> QdrantSettings:
    """Read config from ``PROJECT_ROOT/.env`` and fail loudly if anything is wrong.

    Raises ``ValueError`` naming every missing variable, or naming
    ``EMBEDDING_DIMENSION`` and its value when it is not a positive integer.
    """
    load_dotenv(PROJECT_ROOT / ".env")

    url = os.getenv("QDRANT_URL")
    collection = collection_override or os.getenv("QDRANT_COLLECTION")
    raw_dim = os.getenv("EMBEDDING_DIMENSION")

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

    return QdrantSettings(url=url, collection=collection, embedding_dim=embedding_dim)


def collection_exists(settings: QdrantSettings) -> bool:
    """Ask Qdrant whether the collection already exists.

    Must run before ``build_document_store`` is used: the Haystack store creates a
    missing collection as soon as it first connects, so this check uses its own client.
    """
    client = QdrantClient(
        url=settings.url, api_key=Secret.from_env_var("QDRANT_API_KEY").resolve_value()
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
) -> QdrantDocumentStore:
    """Open a QdrantDocumentStore for the given settings.

    By default this attaches to the collection and keeps its data; pass
    ``recreate=True`` to wipe and rebuild it. ``return_embedding`` only controls
    whether returned documents carry their stored vectors. Similarity search runs
    inside Qdrant either way, so leave it off unless the caller reads the vectors.
    """
    return QdrantDocumentStore(
        url=settings.url,
        index=settings.collection,
        api_key=Secret.from_env_var("QDRANT_API_KEY"),
        embedding_dim=settings.embedding_dim,
        recreate_index=recreate,
        return_embedding=return_embedding,
        wait_result_from_api=True,  # confirm writes before returning
        hnsw_config=HNSW_TUNING,
    )


def smoke_test(store: QdrantDocumentStore) -> int:
    """Prove the store accepts a write and a delete, leaving nothing behind.

    Writes one probe document, checks the count rose by one, deletes the probe by
    id, and checks the count is back where it started. Returns that count.
    """
    from haystack.dataclasses import Document

    collection = store.index
    start = store.count_documents()
    logger.info("Documents currently in collection: %d", start)

    # A random id means the cleanup below can only ever delete the document
    # written here, never an existing document with the same content.
    probe = Document(
        id=uuid.uuid4().hex,
        content=f"Connectivity probe for the {APP_LABEL}.",
        meta={"source": "connection_test", "test": True},
    )
    try:
        store.write_documents([probe])
        after_write = store.count_documents()
        if after_write != start + 1:
            raise RuntimeError(
                f"Probe write to '{collection}' did not register: count went from {start} to {after_write}"
            )
        logger.info("Wrote one probe document; count is now %d", after_write)
    finally:
        store.delete_documents([probe.id])

    end = store.count_documents()
    if end != start:
        raise RuntimeError(
            f"Collection '{collection}' did not return to its starting count after deleting the probe: "
            f"started at {start}, ended at {end}"
        )
    logger.info("Deleted the probe; count is back to %d", end)
    return end


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Bootstrap Qdrant for the {APP_LABEL}")
    parser.add_argument(
        "--collection", help="Collection name; overrides QDRANT_COLLECTION from .env"
    )
    parser.add_argument(
        "--recreate",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Drop and rebuild the collection; by default the run attaches and keeps existing data",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    # Configure once from the entry point, in the format the curation stages use.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    # httpx (under qdrant-client) logs one INFO line per HTTP request.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        settings = load_settings(collection_override=args.collection)
    except ValueError as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from None

    logger.info("Endpoint:   %s", settings.url)
    logger.info("Collection: %s (recreate=%s)", settings.collection, args.recreate)
    logger.info("Dimensions: %d", settings.embedding_dim)

    try:
        if not collection_exists(settings):
            logger.info("Collection '%s' does not exist; creating it", settings.collection)
        elif args.recreate:
            logger.warning(
                "Collection '%s' already exists; --recreate will wipe and rebuild it",
                settings.collection,
            )
        else:
            logger.info(
                "Collection '%s' already exists; attaching and keeping its data",
                settings.collection,
            )
        store = build_document_store(settings, recreate=args.recreate)
        smoke_test(store)
    except Exception as exc:  # noqa: BLE001  (CLI converts setup failures to exit status)
        logger.error("Setup failed: %s", exc)
        logger.error(
            "Check the Qdrant URL and API key, network access, and the embedding dimension."
        )
        raise SystemExit(1) from None

    logger.info("Qdrant document store '%s' is ready.", settings.collection)


if __name__ == "__main__":
    main()
