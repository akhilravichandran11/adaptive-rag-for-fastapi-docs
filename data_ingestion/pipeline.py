"""
Run ids, per-run output directories and artifact names for the ingestion stages.

Mirrors ``data_curation/pipeline.py`` (D12). Each run writes into its own directory
under ``OUTPUTS_DIR``, named by its run id; the files inside keep the fixed names
below, so a run id never appears in a filename.

A run id resolves in this order: an explicit argument, then the ``RUN_ID``
environment variable, then a fresh UTC timestamp. It must be a plain directory
name, so a run can never write outside ``OUTPUTS_DIR``.
"""

import os
import re
from datetime import UTC, datetime
from pathlib import Path

OUTPUTS_DIR = Path(__file__).resolve().parent / "outputs"

# Fixed names inside a run directory.
CHUNKS_DIR = "chunks"
RUN_META_FILE = "run_meta.json"
INDEX_REPORT_FILE = "index_report.json"

# Letters, digits, "_", "-" and ".", starting with a letter or digit: never empty,
# absolute, nested, or "." / "..".
_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def generate_run_id() -> str:
    """A fresh run id: the current UTC time as ``%Y%m%d_%H%M%S``, which sorts by time."""
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def resolve_run_id(run_id: str | None = None) -> str:
    """The run id to use: ``run_id`` if given, else ``RUN_ID``, else a fresh one.

    ``RUN_ID`` is stripped, so a stray space from the shell is not part of the id
    and a blank one counts as unset.
    """
    return run_id or os.getenv("RUN_ID", "").strip() or generate_run_id()


def resolve_run_dir(run_id: str) -> Path:
    """The output directory for ``run_id``; the caller creates it when it writes.

    Raises ``ValueError`` unless ``run_id`` is a plain directory name, so a slip
    such as ``--run-id ../x`` or a pasted absolute path cannot write outside
    ``OUTPUTS_DIR``, where the files would no longer be git-ignored.
    """
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError(
            f"Run id {run_id!r} is not a plain directory name: use letters, digits, "
            "'_', '-' and '.', starting with a letter or digit"
        )
    return OUTPUTS_DIR / run_id
