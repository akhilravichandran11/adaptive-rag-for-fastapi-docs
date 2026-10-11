"""
Write a chunk-size report: every strategy's chunks of a corpus, checked against the shape rules.

Loads a folder of markdown docs (the normalized corpus by default), chunks it with
each strategy, and logs a table of the counts — chunks over the declared maximum,
under the declared minimum, empty, documents with lost text, order errors, the
informational low-prose and unbroken-run counts, and how much BGE and Voyage would
truncate. The same report, with per-chunk detail and the sha256 of the corpus
``manifest.json``, is written as JSON to ``outputs/chunk-size-reports/<UTC time>.json``.
Nothing is changed: the report describes chunks, it never fixes them. See
``data_ingestion/chunk_shape.py`` for the rules.

The five size-based strategies run offline and cost nothing. ``semantic`` finds its
boundaries with BGE on OpenRouter — a paid call (cents per corpus) that needs
``OPENROUTER_API_KEY`` — so it runs only with ``--include-semantic``.

    uv run python scripts/chunk_size_report.py                      # size-based strategies, current corpus
    uv run python scripts/chunk_size_report.py --messy              # messy copies of the corpus instead
    uv run python scripts/chunk_size_report.py --include-semantic   # adds semantic (paid)

Needs BGE's tokenizer and NLTK's ``punkt_tab`` in the project model cache
(``scripts/download_models.py``).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))  # lets the script run by path from any directory

from data_ingestion.chunk_shape import (  # noqa: E402  (needs the path set above)
    DEFAULT_INJECTED_RUN_LENGTH,
    DEFAULT_LOW_PROSE_SHARE,
    DEFAULT_MESSY_COUNT,
    DEFAULT_MESSY_SEED,
    DEFAULT_UNBROKEN_RUN,
    MESSY_CHANGES,
    PAID_STRATEGIES,
    REPORTS_DIR,
    SIZE_BASED_STRATEGIES,
    build_report,
    log_report,
    make_messy_copies,
    write_report,
)
from data_ingestion.chunkers import get_chunker  # noqa: E402
from data_ingestion.loading import load_documents  # noqa: E402
from data_ingestion.metadata import CORPUS_ROOT  # noqa: E402

logger = logging.getLogger(__name__)

DOCS_DIR = CORPUS_ROOT / "docs"
MANIFEST_NAME = "manifest.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check every chunking strategy's chunks against the shape rules and "
        "write a chunk-size report. Changes no chunk.",
    )
    parser.add_argument(
        "--docs-dir",
        type=Path,
        default=DOCS_DIR,
        help="Folder of markdown docs to chunk (default: the normalized corpus)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help=f"Corpus manifest whose sha256 the report records "
        f"(default: {MANIFEST_NAME} beside the docs folder; skipped if absent)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPORTS_DIR,
        help="Where the JSON report is written (default: outputs/chunk-size-reports)",
    )
    parser.add_argument(
        "--strategy",
        action="append",
        choices=SIZE_BASED_STRATEGIES,
        help="A size-based strategy to report on; repeat for several (default: all five)",
    )
    parser.add_argument(
        "--include-semantic",
        action="store_true",
        help="Also run semantic. PAID: it calls OpenRouter (BGE) and needs OPENROUTER_API_KEY",
    )
    parser.add_argument(
        "--low-prose-share",
        type=float,
        default=DEFAULT_LOW_PROSE_SHARE,
        help="A chunk is low-prose when under this share of its characters are letters "
        "or whitespace (default: %(default)s)",
    )
    parser.add_argument(
        "--unbroken-run",
        type=int,
        default=DEFAULT_UNBROKEN_RUN,
        help="A chunk is flagged when it holds more than this many characters without "
        "whitespace (default: %(default)s)",
    )
    messy = parser.add_argument_group("messy copies (report on degraded copies instead)")
    messy.add_argument(
        "--messy",
        action="store_true",
        help="Report on messy copies of a seeded handful of the docs instead of the docs",
    )
    messy.add_argument(
        "--messy-change",
        action="append",
        choices=MESSY_CHANGES,
        help="A change to apply; repeat for several (default: all four)",
    )
    messy.add_argument(
        "--messy-count",
        type=int,
        default=DEFAULT_MESSY_COUNT,
        help="How many docs to copy (default: %(default)s)",
    )
    messy.add_argument(
        "--messy-seed",
        type=int,
        default=DEFAULT_MESSY_SEED,
        help="Seed choosing the docs and placing injected runs (default: %(default)s)",
    )
    messy.add_argument(
        "--messy-run-length",
        type=int,
        default=DEFAULT_INJECTED_RUN_LENGTH,
        help="Length of an injected unbroken run (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    # Checked here, before anything is chunked: a bad value found after a paid
    # semantic run would waste the run.
    if not 0 < args.low_prose_share <= 1:
        parser.error(f"--low-prose-share must be in (0, 1], got {args.low_prose_share}")
    for flag, value in (
        ("--unbroken-run", args.unbroken_run),
        ("--messy-count", args.messy_count),
        ("--messy-run-length", args.messy_run_length),
    ):
        if value < 1:
            parser.error(f"{flag} must be at least 1, got {value}")
    args.messy_change = list(dict.fromkeys(args.messy_change or MESSY_CHANGES))
    return args


def main(argv: list[str] | None = None) -> Path:
    """Run the report and return the path of the JSON it wrote."""
    args = parse_args(argv)

    # Same format as the curation and Qdrant stages.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    # httpx (under the OpenAI client) logs one INFO line per HTTP request.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv(PROJECT_ROOT / ".env")

    strategies = list(dict.fromkeys(args.strategy or SIZE_BASED_STRATEGIES))
    if args.include_semantic:
        logger.warning(
            "Including %s: a PAID call to OpenRouter (BGE boundaries, cents per corpus)",
            ", ".join(PAID_STRATEGIES),
        )
        strategies.extend(PAID_STRATEGIES)
    else:
        logger.info(
            "Leaving out %s (paid, OpenRouter); pass --include-semantic to run it",
            ", ".join(PAID_STRATEGIES),
        )

    try:
        documents = load_documents(args.docs_dir)
    except (FileNotFoundError, RuntimeError) as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from None

    manifest = args.manifest or args.docs_dir.parent / MANIFEST_NAME
    if not manifest.is_file():
        if args.manifest is not None:
            logger.error("Manifest not found: %s", manifest)
            raise SystemExit(1)
        logger.warning(
            "No %s beside %s; the report records no corpus hash", MANIFEST_NAME, args.docs_dir
        )
        manifest = None

    if args.messy:
        changes = args.messy_change
        try:
            documents = make_messy_copies(
                documents,
                changes=changes,
                count=args.messy_count,
                seed=args.messy_seed,
                run_length=args.messy_run_length,
            )
        except ValueError as exc:
            logger.error("%s", exc)
            raise SystemExit(2) from None
        logger.info(
            "Reporting on %d messy copies (changes: %s; count %d, seed %d)",
            len(documents),
            ", ".join(changes),
            args.messy_count,
            args.messy_seed,
        )

    chunks_by_strategy = {}
    for name in strategies:
        chunker = get_chunker(name)
        chunker.warm_up()
        try:
            chunks_by_strategy[name] = chunker.run(documents)["documents"]
        except ValueError as exc:  # e.g. two documents sharing a source path
            logger.error("%s", exc)
            raise SystemExit(2) from None
        logger.info("%s: %d chunks", name, len(chunks_by_strategy[name]))

    try:
        report = build_report(
            documents,
            chunks_by_strategy,
            low_prose_share=args.low_prose_share,
            unbroken_run=args.unbroken_run,
            manifest_path=manifest,
        )
    except ValueError as exc:
        logger.error("%s", exc)
        raise SystemExit(2) from None
    report["input"] = {
        "docs_dir": str(args.docs_dir),
        "messy": (
            {
                "changes": args.messy_change,
                "count": args.messy_count,
                "seed": args.messy_seed,
                "run_length": args.messy_run_length,
            }
            if args.messy
            else None
        ),
    }

    log_report(report, log=logger)
    path = write_report(report, args.out_dir)
    logger.info("Chunk-size report written to %s", path)
    return path


if __name__ == "__main__":
    main()
