"""
Prove the run-id rule the ingestion stages share: explicit argument, then the
``RUN_ID`` environment variable, then a fresh UTC timestamp; and that a run
directory always stays a plain directory under ``outputs/``.

``data_ingestion.pipeline`` mirrors ``data_curation.pipeline`` (D12). Only its
agreed seams, ``resolve_run_id`` and ``resolve_run_dir``, are tested. Nothing here
can reach the network, so the module installs no guard (C006).

Run offline::

    uv run pytest tests/data_ingestion/test_pipeline.py
"""

import re
from datetime import UTC, datetime, timedelta

import pytest

from data_ingestion.pipeline import (
    OUTPUTS_DIR,
    resolve_run_dir,
    resolve_run_id,
)

TIMESTAMP = re.compile(r"\d{8}_\d{6}")


def test_explicit_argument_wins_over_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUN_ID", "from_env")

    assert resolve_run_id("from_arg") == "from_arg"


def test_run_id_used_without_an_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUN_ID", "from_env")

    assert resolve_run_id(None) == "from_env"
    assert resolve_run_id() == "from_env"


def test_fresh_utc_timestamp_without_argument_or_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUN_ID", raising=False)

    before = datetime.now(UTC).replace(microsecond=0)
    run_id = resolve_run_id()
    after = datetime.now(UTC)

    assert TIMESTAMP.fullmatch(run_id), run_id
    minted = datetime.strptime(run_id, "%Y%m%d_%H%M%S").replace(tzinfo=UTC)
    assert before <= minted <= after + timedelta(seconds=1)


def test_whitespace_only_run_id_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUN_ID", "   ")

    assert TIMESTAMP.fullmatch(resolve_run_id())


def test_run_id_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUN_ID", "  from_env\n")

    assert resolve_run_id() == "from_env"


@pytest.mark.parametrize("run_id", ["20261012_101500", "20261012_101500-smoke", "ix.verify_1"])
def test_run_dir_is_a_directory_under_outputs(run_id: str) -> None:
    assert resolve_run_dir(run_id) == OUTPUTS_DIR / run_id


@pytest.mark.parametrize("run_id", ["", " ", ".", "..", "../x", "/tmp/x", "a/b", " x", "x "])
def test_run_dir_refuses_anything_but_a_plain_name(run_id: str) -> None:
    with pytest.raises(ValueError, match=re.escape(repr(run_id))):
        resolve_run_dir(run_id)
