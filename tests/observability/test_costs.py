import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from observability import costs
from observability.costs import create_recorder


def test_completed_call_is_flushed_and_summarized_once(tmp_path):
    directory = tmp_path / "run"
    record, finish = create_recorder(directory)
    record(
        provider="voyage",
        operation="embedding",
        model="voyage-4-lite",
        outcome="response",
        usage={"total_tokens": 1_000_000},
    )
    rows = [json.loads(line) for line in (directory / "calls.jsonl").read_text().splitlines()]
    assert rows == [
        {
            "provider": "voyage",
            "operation": "embedding",
            "model": "voyage-4-lite",
            "outcome": "response",
            "usage": {"total_tokens": 1_000_000},
            "usd": 0.02,
            "reason": None,
        }
    ]
    finish()
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["known_estimated_usd"] == pytest.approx(0.02)
    assert summary["providers"]["voyage"]["known_estimated_usd"] == pytest.approx(0.02)
    assert summary["call_count"] == 1
    assert summary["unknown_cost_count"] == 0
    assert summary["run_failed"] is False


@pytest.mark.parametrize(
    ("provider", "operation", "model", "usage", "expected"),
    [
        ("voyage", "rerank", "rerank-2.5", {"total_tokens": 1_000_000}, 0.05),
        ("tavily", "search", None, {"credits": 2}, 0.016),
        (
            "gemini",
            "generation",
            "gemini-3.1-pro-preview",
            {
                "prompt_token_count": 100_000,
                "cached_content_token_count": 20_000,
                "candidates_token_count": 10_000,
                "thoughts_token_count": 5_000,
                "total_token_count": 115_000,
            },
            0.344,
        ),
        (
            "gemini",
            "generation",
            "gemini-3.8-flash",
            {
                "prompt_token_count": 100_000,
                "cached_content_token_count": 20_000,
                "candidates_token_count": 10_000,
                "thoughts_token_count": 5_000,
            },
            0.11775,
        ),
        (
            "gemini",
            "generation",
            "gemini-3.5-flash-lite",
            {
                "prompt_token_count": 100_000,
                "cached_content_token_count": 20_000,
                "candidates_token_count": 10_000,
                "thoughts_token_count": 5_000,
            },
            0.0621,
        ),
    ],
)
def test_provider_prices_use_reported_disjoint_usage(
    tmp_path, monkeypatch, provider, operation, model, usage, expected
):
    monkeypatch.setattr(costs, "date", Mock(wraps=date, today=lambda: date(2026, 11, 21)))
    directory = tmp_path / "run"
    record, finish = create_recorder(directory)
    record(provider=provider, operation=operation, model=model, outcome="response", usage=usage)
    finish()
    row = json.loads((directory / "calls.jsonl").read_text())
    assert row["usd"] == pytest.approx(expected)
    assert row["reason"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"usage": None},
        {"usage": {}},
        {"usage": {"total_tokens": -1}},
        {"usage": {"total_tokens": True}},
        {"usage": {"total_tokens": float("nan")}},
        {"usage": {"total_tokens": float("inf")}},
        {"usage": {"total_tokens": "123"}},
        {"usage": {"total_tokens": 5, "image_tokens": 2}},
        {"model": "voyage-alias"},
        {"model": "rerank-2.5"},
        {"service_tier": "batch"},
        {"modality": "audio"},
    ],
)
def test_unpriceable_usage_stays_unknown(tmp_path, changes):
    directory = tmp_path / "run"
    record, finish = create_recorder(directory)
    arguments = dict(
        provider="voyage",
        operation="embedding",
        model="voyage-4-lite",
        outcome="response",
        usage={"total_tokens": 5},
    )
    record(**(arguments | changes))
    finish()
    row = json.loads((directory / "calls.jsonl").read_text())
    assert row["usd"] is None
    assert row["reason"]
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["unknown_cost_count"] == 1
    assert summary["all_observed_costs_known"] is False


def test_zero_error_and_sensitive_usage_are_distinct(tmp_path):
    directory = tmp_path / "run"
    record, finish = create_recorder(directory)
    record(
        provider="voyage",
        operation="embedding",
        model="voyage-4-lite",
        outcome="response",
        usage={"total_tokens": 0},
    )
    record(provider="tavily", operation="search", model=None, outcome="error", usage=None)
    record(
        provider="voyage",
        operation="rerank",
        model="rerank-2.5",
        outcome="response",
        usage={"total_tokens": 10, "prompt": "secret-content", "api_key": "secret-key"},
    )
    finish()
    text = (directory / "calls.jsonl").read_text()
    assert "secret" not in text
    rows = [json.loads(line) for line in text.splitlines()]
    assert rows[0]["usd"] == 0
    assert rows[1]["usd"] is None
    assert rows[2]["usage"] == {"total_tokens": 10}
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["run_failed"] is True
    assert summary["unknown_cost_count"] == 2
    assert summary["providers"]["tavily"]["unknown_cost_count"] == 1
    assert summary["pricing_date"]
    assert "PAYG" in summary["pricing_basis"]
    assert "retry" in summary["limitations"].lower()


@pytest.mark.parametrize(
    "changes",
    [
        {"prompt_token_count": 200_001},
        {"cached_content_token_count": 101},
        {"total_token_count": 200},
        {"tool_use_prompt_token_count": 1},
        {"thoughts_token_count": 1.5},
    ],
)
def test_unsupported_gemini_usage_is_not_priced(tmp_path, changes):
    directory = tmp_path / "run"
    record, finish = create_recorder(directory)
    usage = {"prompt_token_count": 100, "candidates_token_count": 10}
    record(
        provider="gemini",
        operation="generation",
        model="gemini-3.1-pro-preview",
        outcome="response",
        usage=usage | changes,
    )
    finish()
    row = json.loads((directory / "calls.jsonl").read_text())
    assert row["usd"] is None
    assert row["reason"]


def test_expired_promotion_is_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(costs, "date", Mock(wraps=date, today=lambda: date(2027, 1, 1)))
    directory = tmp_path / "run"
    record, finish = create_recorder(directory)
    record(
        provider="gemini",
        operation="generation",
        model="gemini-3.8-flash",
        outcome="response",
        usage={"prompt_token_count": 100, "candidates_token_count": 10},
    )
    finish()
    row = json.loads((directory / "calls.jsonl").read_text())
    assert row["usd"] is None
    assert row["reason"] == "expired_price"


def test_concurrent_callbacks_preserve_rows_and_totals(tmp_path):
    directory = tmp_path / "run"
    record, finish = create_recorder(directory)

    def complete_call(_):
        record(
            provider="tavily",
            operation="search",
            model=None,
            outcome="response",
            usage={"credits": 2},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(complete_call, range(100)))
    finish(run_failed=True)
    rows = [json.loads(line) for line in (directory / "calls.jsonl").read_text().splitlines()]
    assert len(rows) == 100
    assert all(row["usd"] == 0.016 for row in rows)
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["call_count"] == 100
    assert summary["known_estimated_usd"] == pytest.approx(1.6)
    assert summary["providers"]["tavily"]["call_count"] == 100
    assert summary["run_failed"] is True


def test_existing_directory_is_refused_without_changing_it(tmp_path):
    existing = tmp_path / "calls.jsonl"
    existing.write_text("previous run")
    with pytest.raises(FileExistsError):
        create_recorder(tmp_path)
    assert existing.read_text() == "previous run"


def test_calls_file_open_failure_surfaces_at_setup(tmp_path, monkeypatch):
    def fail_open(*args, **kwargs):
        raise PermissionError("fixture open failure")

    monkeypatch.setattr(Path, "open", fail_open)
    with pytest.raises(PermissionError, match="fixture open failure"):
        create_recorder(tmp_path / "run")


@pytest.mark.parametrize("failure", ["write", "flush"])
def test_accounting_failure_closes_file_and_cannot_report_success(tmp_path, monkeypatch, failure):
    actual_open = Path.open
    handles = []

    def fail(*args):
        raise OSError("fixture accounting failure")

    def open_with_failure(path, *args, **kwargs):
        handle = actual_open(path, *args, **kwargs)
        handles.append(handle)
        methods = dict(write=handle.write, flush=handle.flush, close=handle.close)
        methods[failure] = fail
        return SimpleNamespace(**methods)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", open_with_failure)
        record, finish = create_recorder(tmp_path / "run")
    with pytest.raises(OSError, match="fixture accounting failure"):
        record(
            provider="tavily",
            operation="search",
            model=None,
            outcome="response",
            usage={"credits": 2},
        )
    with pytest.raises(OSError, match="accounting"):
        finish()
    assert handles[0].closed
    assert not (tmp_path / "run" / "summary.json").exists()


def test_summary_failure_closes_calls_and_refuses_later_callbacks(tmp_path):
    directory = tmp_path / "run"
    record, finish = create_recorder(directory)
    (directory / "summary.json").mkdir()
    with pytest.raises(OSError):
        finish()
    with pytest.raises(OSError, match="closed"):
        record(
            provider="tavily",
            operation="search",
            model=None,
            outcome="response",
            usage={"credits": 2},
        )


def test_second_finish_does_not_overwrite_failed_run(tmp_path):
    directory = tmp_path / "run"
    _, finish = create_recorder(directory)
    finish(run_failed=True)
    with pytest.raises(OSError, match="closed"):
        finish()
    assert json.loads((directory / "summary.json").read_text())["run_failed"] is True
