"""
Stage 2: chunk the normalized FastAPI docs with each selected strategy and save the chunks.

One job (D42): load the docs, attach the named curation run's metadata (dropping any
document it quarantined), chunk them with each strategy, and save each strategy's
chunks through ``chunk_snapshot`` as the handoff stage 3 indexes from (D38). Nothing
here touches Qdrant, embeds for indexing, or checks chunk shape; the shape checks stay
in ``scripts/chunk_size_report.py`` (D41 withdrawn).

    python stage2_chunk_corpus.py --strategy naive_small --curation-run 20260925_032632
    python stage2_chunk_corpus.py --strategy naive_small --curation-run 20260925_032632 --limit 5
    python stage2_chunk_corpus.py --strategy all --curation-run 20260925_032632   # includes semantic (paid)

A run writes into a new folder, ``data_ingestion/outputs/<run_id>/`` (see
``pipeline.py``); a run id whose folder exists, from either stage, is refused:

    chunks/<strategy>.jsonl   one per selected strategy, saved as each strategy finishes
    run_meta.json             written last (D40): the corpus commit and manifest sha256,
                              the curation run, the limit (null unless it cut documents),
                              the documents loaded, dropped and chunked, and each
                              strategy's chunk count and settings

Every strategy but ``semantic`` runs offline and costs nothing. ``semantic`` finds its
cut points with BGE on OpenRouter: a paid call (about $0.008 for the corpus) that needs
``OPENROUTER_API_KEY`` in ``.env``; without it, a run that selects ``semantic`` stops
before chunking anything. ``--strategy all`` includes it.

Needs BGE's tokenizer and NLTK's ``punkt_tab`` in the project model cache
(``scripts/download_models.py``). Exits 2 for a bad command line, and 1, without
writing ``run_meta.json``, when the run cannot start (its run folder exists, or
``semantic`` has no key), an input is missing or malformed, no document is left to
chunk, or a strategy makes no chunks. A run that fails once it has started chunking
keeps its folder, even an empty one, so a retry needs a new run id.
"""

import argparse
import dataclasses
import json
import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from haystack import Document

from data_ingestion.chunk_snapshot import manifest_sha256, write_chunks
from data_ingestion.chunkers import STRATEGIES, get_chunker
from data_ingestion.embedders import EMBEDDERS, cached_tokenizer_path
from data_ingestion.loading import load_documents
from data_ingestion.metadata import (
    CORPUS_ROOT,
    CURATION_OUTPUTS_ROOT,
    MANIFEST_PATH,
    MetadataError,
    attach_metadata,
    load_curation_run,
    load_manifest,
)
from data_ingestion.pipeline import RUN_META_FILE, resolve_run_dir, resolve_run_id

PROJECT_ROOT = Path(__file__).resolve().parents[1]

logger = logging.getLogger(__name__)

DOCS_DIR = CORPUS_ROOT / "docs"
STAGE = "chunk"

# The one strategy that makes a paid call: BGE on OpenRouter finds its cut points.
PAID_STRATEGY = "semantic"


def semantic_boundary_model() -> dict[str, str | None]:
    """The embedder ``semantic`` found its cut points with, as ``run_meta.json`` records it."""
    from data_ingestion.chunkers.semantic import BOUNDARY_EMBEDDER

    spec = EMBEDDERS[BOUNDARY_EMBEDDER]
    return {
        "embedder": spec.name,
        "model": spec.model,
        "api_model": spec.api_model,
        "backend": spec.backend,
    }


def chunk_corpus(
    documents: list[Document], strategies: list[str], run_dir: Path
) -> dict[str, dict[str, Any]]:
    """Chunk ``documents`` with each strategy and save each strategy's chunks in ``run_dir``.

    Returns, per strategy in the order given, what ``run_meta.json`` records about it:
    ``chunk_count``, the declared ``settings``, and for ``semantic`` the
    ``boundary_model`` that found its cut points.
    """
    records: dict[str, dict[str, Any]] = {}
    for name in strategies:
        chunker = get_chunker(name)
        chunker.warm_up()
        chunks = chunker.run(documents)["documents"]
        path = write_chunks(run_dir, name, chunks)
        record: dict[str, Any] = {
            "chunk_count": len(chunks),
            "settings": dataclasses.asdict(chunker.settings),
        }
        if name == PAID_STRATEGY:
            record["boundary_model"] = semantic_boundary_model()
        records[name] = record
        logger.info("%s: %d chunks saved to %s", name, len(chunks), path)
    return records


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Chunk the normalized FastAPI docs with each selected strategy and save "
        "the chunks for stage 3. No Qdrant, no indexing embeddings.",
    )
    parser.add_argument(
        "--strategy",
        required=True,
        choices=["all", *STRATEGIES],
        help="A chunking strategy, or 'all' for every one. semantic (and so all) is PAID: "
        "it calls OpenRouter for its cut points and needs OPENROUTER_API_KEY",
    )
    parser.add_argument(
        "--curation-run",
        required=True,
        help="The data_curation run whose metadata every chunk carries, e.g. 20260925_032632",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Chunk only the first N documents left after quarantined ones are dropped",
    )
    parser.add_argument(
        "--run-id",
        help="Run folder under data_ingestion/outputs (default: RUN_ID, else the UTC time)",
    )
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error(f"--limit must be at least 1, got {args.limit}")
    if args.run_id is not None:
        try:
            resolve_run_dir(args.run_id)
        except ValueError as exc:
            parser.error(f"--run-id: {exc}")
    return args


def main(argv: list[str] | None = None) -> Path:
    """Run stage 2 and return the run directory it wrote."""
    args = parse_args(argv)

    # Configure once from the entry point, in the format the other stages use.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    # httpx (under the OpenAI client semantic uses) logs one INFO line per request.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv(PROJECT_ROOT / ".env")

    if cached_tokenizer_path("bge") is None:
        logger.error(
            "BGE's tokenizer is not in the project model cache. "
            "Run 'uv run python scripts/download_models.py' from the project root first."
        )
        raise SystemExit(1)

    run_id = resolve_run_id(args.run_id)
    try:
        run_dir = resolve_run_dir(run_id)
    except ValueError as exc:  # only RUN_ID can fail here; parse_args checked --run-id
        logger.error("RUN_ID: %s", exc)
        raise SystemExit(1) from None
    # An earlier run's files, from either stage, would be mixed with or replaced by this
    # run's (F001). On a case-insensitive disk (macOS) this also finds a folder whose
    # name differs only in case.
    if run_dir.exists():
        logger.error("Run folder %s already exists; pick a new --run-id (or RUN_ID)", run_dir)
        raise SystemExit(1)

    strategies = list(STRATEGIES) if args.strategy == "all" else [args.strategy]
    if PAID_STRATEGY in strategies:
        # Checked before anything is chunked: under --strategy all, semantic runs sixth
        # of seven, after five free strategies that would be chunked for nothing (F002).
        if not os.getenv("OPENROUTER_API_KEY", "").strip():
            logger.error(
                "%s needs OPENROUTER_API_KEY for its paid OpenRouter calls, and it is not "
                "set in .env or the environment",
                PAID_STRATEGY,
            )
            raise SystemExit(1)
        logger.warning(
            "%s calls OpenRouter (BGE) for its cut points: a PAID call, about $0.008 for "
            "the corpus, that needs OPENROUTER_API_KEY",
            PAID_STRATEGY,
        )

    # The metadata sources first: a mistyped --curation-run fails before 124 files are read.
    try:
        manifest = load_manifest(MANIFEST_PATH)
        manifest_hash = manifest_sha256(MANIFEST_PATH)
        curation = load_curation_run(args.curation_run, CURATION_OUTPUTS_ROOT)
        documents = load_documents(DOCS_DIR)
        enriched = attach_metadata(documents, manifest=manifest, curation=curation)
    except (OSError, RuntimeError, MetadataError) as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from None

    kept = enriched.documents
    if not kept:
        logger.error(
            "Curation run %s drops all %d loaded documents; nothing is left to chunk",
            args.curation_run,
            len(documents),
        )
        raise SystemExit(1)
    # Recorded only when it cuts documents, so stage 3 warns only about a run that is
    # really limited (F002).
    limit = args.limit if args.limit is not None and args.limit < len(kept) else None
    if limit is not None:
        kept = kept[:limit]
        logger.info(
            "--limit %d: chunking the first %d of %d kept documents",
            limit,
            len(kept),
            enriched.kept_count,
        )

    logger.info(
        "Run %s | curation run %s | corpus %s, manifest %s",
        run_id,
        args.curation_run,
        manifest.corpus_commit,
        manifest_hash[:8],
    )
    # Created only now, before the first write, so that of two runs that both passed
    # the folder check above, one writes and the other stops (F001).
    try:
        run_dir.mkdir(parents=True)
    except FileExistsError:
        logger.error("Run folder %s was created by another run since this one started", run_dir)
        raise SystemExit(1) from None
    records = chunk_corpus(kept, strategies, run_dir)
    # Stage 3 would rebuild an empty collection from a strategy with no chunks (F002).
    empty_strategies = [name for name, record in records.items() if not record["chunk_count"]]
    if empty_strategies:
        logger.error(
            "%s made no chunks from %d documents; %s is not written",
            ", ".join(empty_strategies),
            len(kept),
            RUN_META_FILE,
        )
        raise SystemExit(1)

    run_meta = {
        "stage": STAGE,
        "run_id": run_id,
        "corpus_commit": manifest.corpus_commit,
        "manifest_sha256": manifest_hash,
        "curation_run": args.curation_run,
        "limit": limit,
        "documents": {
            "loaded": len(documents),
            "dropped": enriched.dropped_filenames,
            "chunked": len(kept),
        },
        "strategies": records,
    }
    # Written last, once every strategy's chunks are saved.
    payload = json.dumps(run_meta, indent=2, ensure_ascii=False) + "\n"
    (run_dir / RUN_META_FILE).write_text(payload, encoding="utf-8")
    logger.info(
        "Run %s: %d chunk file(s) and %s in %s", run_id, len(records), RUN_META_FILE, run_dir
    )
    return run_dir


if __name__ == "__main__":
    main()
