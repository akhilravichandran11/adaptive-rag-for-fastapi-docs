"""Shared pipeline conventions: run ids, per-run output directories, artifact names.

One run == one directory under OUTPUTS_DIR named by its run id. Every stage reads the
previous stage's artifact and writes its own into that same directory, using the fixed
names below (the run id lives in the directory, never in the filenames).

The orchestrator mints the run id once and passes the run dir to each stage; a stage run
standalone mints its own. Resolution order for a run id is always:
    explicit argument  >  RUN_ID environment variable  >  a fresh UTC timestamp.
"""

import os
from datetime import UTC, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUTS_DIR = SCRIPT_DIR / "outputs"

# Fixed artifact names within a run directory.
CONTENT_ANALYSIS_FILE = "content_analysis.json"
ANALYSIS_REPORT_FILE = "analysis_report.json"
KNOWLEDGE_DOMAINS_FILE = "knowledge_domains.json"
LEARNING_TAXONOMY_FILE = "learning_taxonomy.json"
SYNTHETIC_DATASET_FILE = "synthetic_dataset.json"
RUN_META_FILE = "run_meta.json"


def generate_run_id(label: str | None = None) -> str:
    """Mint a fresh, sortable run id: ``<UTC timestamp>[-<label>]``."""
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"{ts}-{label}" if label else ts


def resolve_run_id(run_id: str | None = None, label: str | None = None) -> str:
    """Resolve a run id: explicit arg > ``RUN_ID`` env var > freshly generated."""
    return run_id or os.getenv("RUN_ID") or generate_run_id(label)


def resolve_run_dir(run_id: str) -> Path:
    """Return the output directory for a run (caller creates it on demand)."""
    return OUTPUTS_DIR / run_id
