"""
Qdrant document-store bootstrap for the FastAPI Q&A RAG project.

Stands up (and optionally smoke-tests) a Haystack ``QdrantDocumentStore``. The
collection name comes from the environment or a per-run ``--collection``
override. A run attaches to the collection and keeps its data; only an explicit
``--recreate`` drops and rebuilds it. The settings and store code live in
``qdrant_store.py``; this script keeps the command line and the probe smoke test.

    python stage1_setup_qdrant.py                                    # attach, collection from .env
    python stage1_setup_qdrant.py --collection tmp_test              # attach to tmp_test
    python stage1_setup_qdrant.py --collection tmp_test --recreate   # wipe and rebuild tmp_test
    python stage1_setup_qdrant.py --collection tmp_test 2>&1 | tee setup.log   # logs go to stderr
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from pathlib import Path

from haystack_integrations.document_stores.qdrant import QdrantDocumentStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))  # lets the script run by path from any directory

from data_ingestion.qdrant_store import (  # noqa: E402  (needs the path set above)
    build_document_store,
    collection_exists,
    load_settings,
)

logger = logging.getLogger(__name__)

APP_LABEL = "FastAPI Q&A RAG system"


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
