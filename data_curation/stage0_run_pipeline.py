"""Orchestrator: run the data-curation stages in order against one run directory.

Mints a run id once, creates ``outputs/<run_id>/``, then runs each stage in sequence.
Each stage reads the previous stage's fixed-name artifact from the run dir and writes
its own into it. Fails fast: the first stage error aborts with a nonzero exit.

NOTE: Stages 1, 2, and 3 conform to the run-directory contract. Stage 4 remains
unavailable to the full chain.
"""

import argparse
import asyncio
import importlib
import logging
from pathlib import Path

from dotenv import load_dotenv
from openai_llm_utils import configure_logging
from pipeline import (
    CONTENT_ANALYSIS_FILE,
    IMPLEMENTATION_ROADMAP_FILE,
    LEARNING_TAXONOMY_FILE,
    SYNTHETIC_DATASET_FILE,
    resolve_run_dir,
    resolve_run_id,
)

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
load_dotenv(PROJECT_ROOT / ".env")

logger = logging.getLogger(__name__)


# The pipeline chain, in order. Each module is imported lazily when its stage runs,
# so stages not yet refactored only matter if actually reached (use --only otherwise).
STAGES = [
    {
        "n": 1,
        "module": "stage1_analyze_content_quality",
        "output": CONTENT_ANALYSIS_FILE,
        "kwargs": lambda a: {"limit": a.limit},
    },
    {
        "n": 2,
        "module": "stage2_knowledge_domains",
        "output": LEARNING_TAXONOMY_FILE,
        "kwargs": lambda a: {},
    },
    {
        "n": 3,
        "module": "stage3_learning_taxonomy_final",
        "output": IMPLEMENTATION_ROADMAP_FILE,
        "kwargs": lambda a: {},
    },
    {
        "n": 4,
        "module": "stage4_synthetic_dataset",
        "output": SYNTHETIC_DATASET_FILE,
        "kwargs": lambda a: {},
    },
]


async def run_pipeline(args) -> None:
    """Resolve one run directory and run the selected stages in order, fail-fast."""
    configure_logging()

    run_id = resolve_run_id(args.run_id, args.label)
    run_dir = resolve_run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Pipeline run %s -> %s", run_id, run_dir)

    selected = [s for s in STAGES if args.only is None or s["n"] in args.only]
    if not selected:
        logger.warning("No stages selected (check --only); nothing to do.")
        return

    for stage in selected:
        output_path = run_dir / stage["output"]
        if args.resume and output_path.exists():
            logger.info("Stage %d: output exists, skipping (--resume)", stage["n"])
            continue

        logger.info("=== Stage %d: %s ===", stage["n"], stage["module"])
        try:
            module = importlib.import_module(stage["module"])
            await module.main(run_dir=run_dir, **stage["kwargs"](args))
        except SystemExit as e:
            # A stage signals failure via SystemExit(nonzero); let the orchestrator own the exit.
            if e.code not in (0, None):
                logger.error(
                    "Stage %d exited with code %s; aborting run %s", stage["n"], e.code, run_id
                )
                raise SystemExit(1) from None
        except Exception:
            logger.exception("Stage %d failed; aborting run %s", stage["n"], run_id)
            raise SystemExit(1) from None

    logger.info("Pipeline complete: %s", run_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the data-curation pipeline.")
    parser.add_argument(
        "--run-id",
        default=None,
        metavar="ID",
        help="Reuse/target a run id. Default: RUN_ID env var, else a fresh timestamp.",
    )
    parser.add_argument(
        "--label",
        default=None,
        metavar="LABEL",
        help="Label appended to a generated run id (ignored if --run-id is given).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Pass a file limit to stage 1 (smoke test before a full run).",
    )
    parser.add_argument(
        "--only",
        type=lambda s: {int(x) for x in s.split(",")},
        default=None,
        metavar="NUMS",
        help="Run only these stage numbers, comma-separated (e.g. 1 or 2,3).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip stages whose output already exists in the run dir.",
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be >= 1")

    asyncio.run(run_pipeline(args))
