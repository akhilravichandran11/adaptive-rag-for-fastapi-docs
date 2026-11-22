"""
Stage 3: index one saved chunk run into one Qdrant collection per strategy × embedder.

One job (D42): read a chunk run that stage 2 saved (D38) and, for each selected
strategy × embedder, rebuild its collection with four payload indexes (D33, D35),
embed the chunks 64 at a time and write them with fixed point ids (D34). Nothing is
chunked here, so both embedders index the same chunks, in any order and across runs.

    python stage3_index_chunks.py --chunks-run 20261008_101500 --strategy naive_small \\
        --embedder bge --collection-prefix ix_verify_20261008
    python stage3_index_chunks.py --chunks-run 20261008_100210 --strategy all --embedder bge
    python stage3_index_chunks.py --chunks-run 20261008_100210 --strategy all --embedder voyage

Each collection is named ``<prefix>__<strategy>__<embedder>`` (``variants.py``). One
that already exists is rebuilt only with ``--recreate``; without it the run stops
before anything is written. A run writes into a new folder,
``data_ingestion/outputs/<run_id>/`` (see ``pipeline.py``); a run id whose folder
exists, from either stage, is refused:

    index_report.json   one entry per collection: its embedder, the chunk run and its
                        limit, the points written, the truncation, the tokens sent and
                        billed, and its status: complete, or partial if it failed or
                        holds a different number of points than chunks. Written before
                        each collection's first write and after its outcome, so a run
                        killed part way leaves that collection recorded as partial.
    run_meta.json       (D40) the chunk run, the prefix and flags, and each embedder's
                        model, backend, api_model, dimension and input_type. Written
                        last (C009), also when a collection fails or Ctrl-C stops the
                        run, so a run directory without it is a run that was killed.

Both embedders are PAID APIs: ``bge`` calls OpenRouter (``OPENROUTER_API_KEY``) and
``voyage`` calls Voyage AI (``VOYAGE_API_KEY``). Qdrant needs ``QDRANT_URL`` and
``QDRANT_API_KEY``. All are read from ``.env``.

Needs BGE's tokenizer in the project model cache (``scripts/download_models.py``): the
BGE embedder cuts every text to 512 tokens with it, and the report counts tokens with
it. Exits 2 for a bad command line (an unknown strategy or embedder name, or a bad
collection prefix, included), and 1 when the run cannot start (its run folder exists),
an input is missing or malformed, a collection exists without ``--recreate``, or a
collection ends partial.
"""

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from haystack import Document
from haystack.document_stores.types import DuplicatePolicy
from haystack_integrations.document_stores.qdrant import QdrantDocumentStore

from data_ingestion.chunk_snapshot import check_same_corpus, read_chunks
from data_ingestion.chunkers import STRATEGIES
from data_ingestion.embedders import (
    build_document_embedder,
    cached_tokenizer_path,
    count_tokens,
    truncation_stats,
)
from data_ingestion.metadata import MANIFEST_PATH
from data_ingestion.pipeline import (
    INDEX_REPORT_FILE,
    RUN_META_FILE,
    resolve_run_dir,
    resolve_run_id,
)
from data_ingestion.qdrant_store import (
    build_document_store,
    collection_exists,
    load_settings,
)
from data_ingestion.variants import (
    DEFAULT_PREFIX,
    Variant,
    check_prefix,
    select_variants,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

logger = logging.getLogger(__name__)

STAGE = "index"

# Created with every collection (D33). Haystack keeps a document's metadata under
# "meta", so the Qdrant field paths are meta.<field>. The cluster's strict mode refuses
# a filter on a field with no index, and Haystack's filtered count turns that into 0.
PAYLOAD_INDEXES = [
    {"field_name": "meta.bucket", "field_schema": "keyword"},
    {"field_name": "meta.source_path", "field_schema": "keyword"},
    {"field_name": "meta.knowledge_domain", "field_schema": "keyword"},
    {"field_name": "meta.quality_score", "field_schema": "float"},
]

# Chunks embedded and written at a time. OpenRouter's component sends 64 per request.
WRITE_BATCH = 64


def point_key(chunk: Document, variant: Variant) -> str:
    """The fixed id of ``chunk``'s point in ``variant``'s collection (D34).

    ``strategy:embedder:source_path:chunk_index``, so the same chunk gets the same key
    in every run. Haystack's Qdrant store turns the key into a UUID5.
    """
    return (
        f"{variant.strategy}:{variant.embedder.name}:"
        f"{chunk.meta['source_path']}:{chunk.meta['chunk_index']}"
    )


def open_collection(
    variant: Variant, *, prefix: str, recreate: bool
) -> tuple[QdrantDocumentStore, bool]:
    """Open ``variant``'s collection to be rebuilt, and say whether it already existed (D35).

    Stops the run with an error naming the collection when it exists and ``recreate``
    is false. Otherwise the store is built with ``recreate=True`` and
    ``PAYLOAD_INDEXES``. Building it connects to nothing: on the first call of any of
    its methods it creates a missing collection, or wipes and rebuilds an existing one,
    with the indexes. Raises ``ValueError`` (from ``load_settings``) when the Qdrant
    settings are incomplete.
    """
    settings = load_settings(
        collection_override=variant.collection(prefix),
        dimension_override=variant.embedder.dimension,
    )
    existed = collection_exists(settings)
    if existed and not recreate:
        raise SystemExit(
            f"Collection {settings.collection} already exists; pass --recreate to rebuild it"
        )
    store = build_document_store(settings, recreate=True, payload_fields_to_index=PAYLOAD_INDEXES)
    return store, existed


def write_variant(
    store: QdrantDocumentStore,
    embedder,
    chunks: list[Document],
    variant: Variant,
    run: dict[str, Any],
) -> dict[str, int | None]:
    """Embed ``chunks`` ``WRITE_BATCH`` at a time with ``embedder`` and write them to ``store``.

    ``embedder`` is the variant's document embedder component, and ``run`` is this
    run's ``run_meta``. Each point gets its ``point_key`` and keeps the chunk's metadata,
    plus ``embedder``, ``index_run`` (``run["run_id"]``) and ``chunks_run``. A batch is
    written only once every vector in it has the registry's dimension: Voyage's
    component does not check, so a wrong size raises ``ValueError`` here.

    Returns ``points_written``, the store's ``final_count``, and ``tokens_billed``: the
    ``total_tokens`` the embedder reported, summed over batches (Voyage), or ``None``
    when it reports none (BGE: ``embed_texts`` drops OpenRouter's usage).
    """
    dimension = variant.embedder.dimension
    written = 0
    billed = None
    # Slices, not itertools.batched: that needs Python 3.12, and the floor is 3.11.
    for start in range(0, len(chunks), WRITE_BATCH):
        batch = [
            Document(
                id=point_key(chunk, variant),
                content=chunk.content,
                meta={
                    **chunk.meta,
                    "embedder": variant.embedder.name,
                    "index_run": run["run_id"],
                    "chunks_run": run["chunks_run"],
                },
            )
            for chunk in chunks[start : start + WRITE_BATCH]
        ]
        result = embedder.run(documents=batch)
        for document in result["documents"]:
            if len(document.embedding) != dimension:
                raise ValueError(
                    f"{variant.embedder.model} returned a {len(document.embedding)}-dimension "
                    f"vector for {document.id}; {variant.embedder.name} collections hold "
                    f"{dimension}-dimension vectors"
                )
        written += store.write_documents(result["documents"], policy=DuplicatePolicy.OVERWRITE)
        if "meta" in result:  # Voyage's component reports its tokens; OpenRouter's does not
            billed = (billed or 0) + result["meta"]["total_tokens"]
    return {
        "points_written": written,
        "final_count": store.count_documents(),
        "tokens_billed": billed,
    }


def write_json(path: Path, record: Any) -> None:
    """Write ``record`` to ``path`` as indented JSON, replacing an earlier copy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Embed one saved chunk run (stage 2) into one Qdrant collection per "
        "strategy × embedder. Both embedders are PAID API calls.",
    )
    parser.add_argument(
        "--chunks-run",
        required=True,
        help="The stage 2 run whose saved chunks are indexed, e.g. 20261008_100210",
    )
    parser.add_argument(
        "--strategy",
        required=True,
        help=f"A strategy the chunk run holds ({', '.join(STRATEGIES)}), or 'all' for every "
        "strategy its run_meta.json lists",
    )
    parser.add_argument(
        "--embedder",
        required=True,
        help="bge (OpenRouter, needs OPENROUTER_API_KEY), voyage (Voyage AI, needs "
        "VOYAGE_API_KEY), or 'all' for both; every one is PAID",
    )
    parser.add_argument(
        "--collection-prefix",
        default=DEFAULT_PREFIX,
        help="Collections are named <prefix>__<strategy>__<embedder> (default: %(default)s); "
        "another prefix gives throwaway collections, e.g. ix_verify_20261008. Letters, "
        "digits, _ and -, with no __ and no _ at the end",
    )
    parser.add_argument(
        "--recreate",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Rebuild collections that already exist; without it the run stops at the first one",
    )
    parser.add_argument(
        "--run-id",
        help="Run folder under data_ingestion/outputs (default: RUN_ID, else the UTC time)",
    )
    args = parser.parse_args(argv)
    try:
        select_variants(args.strategy, args.embedder)  # names every unknown name at once (C008)
        check_prefix(args.collection_prefix)
    except ValueError as exc:
        parser.error(str(exc))
    for flag, run_id in (("--chunks-run", args.chunks_run), ("--run-id", args.run_id)):
        if run_id is not None:
            try:
                resolve_run_dir(run_id)
            except ValueError as exc:
                parser.error(f"{flag}: {exc}")
    return args


def main(argv: list[str] | None = None) -> Path:
    """Run stage 3 and return the run directory it wrote."""
    args = parse_args(argv)

    # Configure once from the entry point, in the format the other stages use.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    # httpx (under the Qdrant, OpenAI and Voyage clients) logs one INFO line per request.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv(PROJECT_ROOT / ".env")

    if cached_tokenizer_path("bge") is None:
        logger.error(
            "BGE's tokenizer is not in the project model cache. "
            "Run 'uv run python scripts/download_models.py' from the project root first."
        )
        raise SystemExit(1)

    run_id = resolve_run_id(args.run_id)
    # In the chunk run's own folder, this run's run_meta.json would replace stage 2's
    # (F001). The folder check below refuses it too; this one comes first for its clearer
    # message, and here rather than in parse_args so that RUN_ID is caught too.
    if run_id == args.chunks_run:
        logger.error(
            "Run id %s is the chunk run's own id; pick another --run-id (or RUN_ID) so "
            "this run does not write into the chunk run's folder",
            run_id,
        )
        raise SystemExit(1)
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

    chunks_run_dir = resolve_run_dir(args.chunks_run)  # parse_args checked it
    try:
        chunk_meta = json.loads((chunks_run_dir / RUN_META_FILE).read_text(encoding="utf-8"))
        check_same_corpus(chunk_meta, MANIFEST_PATH)
    except (OSError, ValueError) as exc:  # a malformed JSON file is a ValueError too
        logger.error("Chunk run %s: %s", args.chunks_run, exc)
        raise SystemExit(1) from None

    # The strategies run_meta.json lists, not the files in chunks/: a chunk run made
    # before stage 2 refused a reused run id can hold a chunk file it did not make (F001).
    chunk_run_strategies = chunk_meta["strategies"]
    # A chunk run that lists a strategy this code does not know (one made before a
    # rename) was made by another code version, so it is refused whatever --strategy
    # selects; under --strategy all that strategy would otherwise drop out without a word.
    unknown = [name for name in chunk_run_strategies if name not in STRATEGIES]
    if unknown:
        logger.error(
            "Chunk run %s lists strategies this code does not know: %s; known: %s. "
            "Run stage 2 again",
            args.chunks_run,
            ", ".join(unknown),
            ", ".join(STRATEGIES),
        )
        raise SystemExit(1)
    if args.strategy != "all" and args.strategy not in chunk_run_strategies:
        logger.error(
            "Chunk run %s holds no %s chunks; it holds: %s",
            args.chunks_run,
            args.strategy,
            ", ".join(chunk_run_strategies),
        )
        raise SystemExit(1)
    variants = [
        variant
        for variant in select_variants(args.strategy, args.embedder)
        if variant.strategy in chunk_run_strategies
    ]
    if not variants:
        logger.error("Chunk run %s lists no strategies to index", args.chunks_run)
        raise SystemExit(1)
    strategies = list(dict.fromkeys(variant.strategy for variant in variants))  # each once

    try:
        chunks_by_strategy = {name: read_chunks(chunks_run_dir, name) for name in strategies}
    except (OSError, ValueError) as exc:
        logger.error("Chunk run %s: %s", args.chunks_run, exc)
        raise SystemExit(1) from None
    # A chunk file whose length run_meta.json does not record was not finished by the
    # run that wrote run_meta.json: a failed re-run into the same run id, which stage 2
    # allowed before it refused a reused run id (F001).
    for name, chunks in chunks_by_strategy.items():
        if len(chunks) != chunk_run_strategies[name]["chunk_count"]:
            logger.error(
                "Chunk run %s: chunks/%s.jsonl holds %d chunks, but its run_meta.json "
                "records %d; run stage 2 again into a new run id",
                args.chunks_run,
                name,
                len(chunks),
                chunk_run_strategies[name]["chunk_count"],
            )
            raise SystemExit(1)

    logger.info(
        "Run %s | chunk run %s | curation run %s | corpus manifest %s",
        run_id,
        args.chunks_run,
        chunk_meta["curation_run"],
        chunk_meta["manifest_sha256"][:8],
    )
    logger.info(
        "Read %d chunk file(s); corpus manifest unchanged since the chunk run",
        len(chunks_by_strategy),
    )
    limit = chunk_meta["limit"]
    if limit is not None and args.collection_prefix == DEFAULT_PREFIX:
        logger.warning(
            "Chunk run %s was made with --limit %d, and it is going into the default "
            "collections: %s",
            args.chunks_run,
            limit,
            ", ".join(variant.collection(args.collection_prefix) for variant in variants),
        )

    specs = {variant.embedder.name: variant.embedder for variant in variants}  # each once
    components = {name: build_document_embedder(name) for name in specs}
    run_meta = {
        "stage": STAGE,
        "run_id": run_id,
        "chunks_run": args.chunks_run,
        "collection_prefix": args.collection_prefix,
        "flags": {"strategy": args.strategy, "embedder": args.embedder, "recreate": args.recreate},
        "embedders": {
            name: {
                "model": spec.model,
                "backend": spec.backend,
                "api_model": spec.api_model,
                "dimension": spec.dimension,
                # The BGE (OpenRouter) components have no input_type, so BGE records null.
                "input_type": getattr(components[name], "input_type", None),
            }
            for name, spec in specs.items()
        },
    }

    # Every collection is opened before any is written. A store connects, and so
    # rebuilds, only on the first call of any of its methods (count_documents included),
    # so one that exists without --recreate stops the run with nothing changed.
    try:
        opened = [
            open_collection(variant, prefix=args.collection_prefix, recreate=args.recreate)
            for variant in variants
        ]
    except ValueError as exc:  # load_settings: the Qdrant settings are incomplete
        logger.error("%s", exc)
        raise SystemExit(1) from None
    # Created only now, before the first write, so that of two runs that both passed
    # the folder check above, one writes and the other stops (F001).
    try:
        run_dir.mkdir(parents=True)
    except FileExistsError:
        logger.error("Run folder %s was created by another run since this one started", run_dir)
        raise SystemExit(1) from None

    report: list[dict[str, Any]] = []
    try:
        for variant, (store, existed) in zip(variants, opened, strict=True):
            spec = variant.embedder
            collection = variant.collection(args.collection_prefix)
            chunks = chunks_by_strategy[variant.strategy]
            texts = [chunk.content for chunk in chunks]
            entry: dict[str, Any] = {
                "collection": collection,
                "strategy": variant.strategy,
                "embedder": spec.name,
                **run_meta["embedders"][spec.name],
                "chunks_run": args.chunks_run,
                "chunks_run_limit": limit,
                "collection_existed": existed,
                "payload_indexes": [index["field_name"] for index in PAYLOAD_INDEXES],
                "chunks": len(chunks),
                "points_written": None,
                "final_count": None,
                "truncation": truncation_stats(spec.name, texts).as_dict(),
                "tokens_sent": None,
                "tokens_billed": None,
                "status": "partial",  # until every batch is written and counted
                "seconds": None,
            }
            report.append(entry)
            # On disk before the collection's first write: a run killed from here on
            # (SIGTERM, SIGHUP and SIGKILL skip `finally`) leaves it recorded as partial.
            write_json(run_dir / INDEX_REPORT_FILE, report)
            logger.info(
                "%s: %s with %d payload indexes; embedding %d chunks",
                collection,
                "rebuilding" if existed else "creating",
                len(PAYLOAD_INDEXES),
                len(chunks),
            )
            started = time.monotonic()
            try:
                entry.update(write_variant(store, components[spec.name], chunks, variant, run_meta))
            except Exception:  # one failed collection is recorded and the others still run
                logger.exception(
                    "%s: indexing failed, so it is partial; run again with --recreate to "
                    "rebuild it",
                    collection,
                )
            else:
                if entry["tokens_billed"] is None:
                    # BGE: count what was sent offline, each chunk cut to 512 tokens as
                    # openrouter_embeddings cuts it before sending.
                    counts = count_tokens(spec.name, texts)
                    entry["tokens_sent"] = sum(min(count, spec.max_tokens) for count in counts)
                else:
                    entry["tokens_sent"] = entry["tokens_billed"]
                # Step 6 trusts the status, so a count that differs stays partial: two
                # chunks sharing a point key merge, and a failed count reads as 0.
                if entry["final_count"] == len(chunks):
                    entry["status"] = "complete"
                    logger.info(
                        "%s: wrote %d points, count %d",
                        collection,
                        entry["points_written"],
                        entry["final_count"],
                    )
                else:
                    logger.error(
                        "%s: holds %d points for %d chunks, so it is partial; check the "
                        "chunk run for repeated point keys, then run again with --recreate",
                        collection,
                        entry["final_count"],
                        len(chunks),
                    )
            entry["seconds"] = round(time.monotonic() - started, 1)
            write_json(run_dir / INDEX_REPORT_FILE, report)
    finally:
        # run_meta.json is written last (C009), and here so that a run stopped by an
        # exception or Ctrl-C the loop does not catch still records its flags; the
        # report goes once more first so it is whole. A kill skips this block.
        write_json(run_dir / INDEX_REPORT_FILE, report)
        write_json(run_dir / RUN_META_FILE, run_meta)

    partial = [entry["collection"] for entry in report if entry["status"] != "complete"]
    if partial:
        logger.error(
            "%d of %d collection(s) partial: %s. %s in %s",
            len(partial),
            len(report),
            ", ".join(partial),
            INDEX_REPORT_FILE,
            run_dir,
        )
        raise SystemExit(1)
    logger.info(
        "Run %s: %d collection(s) complete; %s and %s in %s",
        run_id,
        len(report),
        INDEX_REPORT_FILE,
        RUN_META_FILE,
        run_dir,
    )
    return run_dir


if __name__ == "__main__":
    main()
