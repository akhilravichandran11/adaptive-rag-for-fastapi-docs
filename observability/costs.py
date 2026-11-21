import json
import math
from datetime import date
from pathlib import Path
from threading import Lock

PRICING_DATE = "2026-11-21"
PRICING_SOURCES = {
    "voyage": "https://docs.voyageai.com/docs/pricing",
    "gemini": "https://ai.google.dev/gemini-api/docs/pricing",
    "tavily": "https://docs.tavily.com/documentation/api-credits",
}
PRICING_BASIS = "Standard text request list prices; Tavily PAYG reference estimate. Excludes discounts, free allowances and cache storage."
LIMITATIONS = "Completed observable SDK invocations only; hidden SDK retry charges and interrupted/in-flight calls may be missing. Not an invoice."
PRICES = {
    "voyage-4-lite": {"total_tokens": 0.02},
    "rerank-2.5": {"total_tokens": 0.05},
    "gemini-3.1-pro-preview": {"input": 2.0, "cache": 0.20, "output": 12.0},
    "gemini-3.8-flash": {"input": 0.75, "cache": 0.075, "output": 3.75},
    "gemini-3.5-flash-lite": {"input": 0.30, "cache": 0.03, "output": 2.50},
    "tavily-payg": {"credits": 0.008},
}
USAGE_FIELDS = {
    "voyage": ("total_tokens",),
    "tavily": ("credits",),
    "gemini": (
        "prompt_token_count",
        "cached_content_token_count",
        "candidates_token_count",
        "thoughts_token_count",
        "total_token_count",
        "tool_use_prompt_token_count",
    ),
}


def price_usage(provider, operation, model, usage, service_tier, modality):
    fields = USAGE_FIELDS[provider]
    raw = usage or {}
    selected = {
        key: value
        for key, value in raw.items()
        if key in fields and type(value) in (int, float) and math.isfinite(value) and value >= 0
    }
    if not selected:
        return selected, None, "missing_or_invalid_usage"
    if any(key not in fields for key in raw):
        return selected, None, "unsupported_usage_category"
    if len(selected) != len(raw):
        return selected, None, "invalid_usage"
    if provider != "tavily" and any(int(value) != value for value in selected.values()):
        return selected, None, "invalid_token_count"
    if service_tier != "standard" or modality != "text":
        return selected, None, "unsupported_tier_or_modality"
    if provider == "tavily":
        if operation != "search" or model is not None:
            return selected, None, "unsupported_operation_or_model"
        return selected, selected["credits"] * PRICES["tavily-payg"]["credits"], None
    if provider == "voyage":
        if (operation, model) not in (("embedding", "voyage-4-lite"), ("rerank", "rerank-2.5")):
            return selected, None, "unsupported_operation_or_model"
        return selected, selected["total_tokens"] * PRICES[model]["total_tokens"] / 1_000_000, None
    return _price_gemini_usage(operation, model, selected)


def _price_gemini_usage(operation, model, selected):
    if operation != "generation" or model not in PRICES or not model.startswith("gemini-"):
        return selected, None, "unsupported_operation_or_model"
    if "prompt_token_count" not in selected or "candidates_token_count" not in selected:
        return selected, None, "missing_usage"
    if model == "gemini-3.8-flash" and date.today() > date(2026, 12, 31):
        return selected, None, "expired_price"
    if model == "gemini-3.1-pro-preview" and selected["prompt_token_count"] > 200_000:
        return selected, None, "unsupported_long_context_tier"
    if selected.get("tool_use_prompt_token_count", 0):
        return selected, None, "unsupported_tool_usage"
    rates = PRICES[model]
    cached = selected.get("cached_content_token_count", 0)
    prompt = selected["prompt_token_count"]
    output = selected["candidates_token_count"] + selected.get("thoughts_token_count", 0)
    if cached > prompt or selected.get("total_token_count", prompt + output) != prompt + output:
        return selected, None, "inconsistent_usage"
    usd = (
        (prompt - cached) * rates["input"] + cached * rates["cache"] + output * rates["output"]
    ) / 1_000_000
    return selected, usd, None


def create_recorder(directory: str | Path):
    directory = Path(directory)
    directory.mkdir()
    calls = (directory / "calls.jsonl").open("x", encoding="utf-8")
    lock = Lock()
    providers = {}
    call_count = 0
    saw_error = False
    accounting_failed = False
    closed = False

    def record(
        *, provider, operation, model, outcome, usage, service_tier="standard", modality="text"
    ):
        nonlocal call_count, saw_error, accounting_failed
        if provider not in USAGE_FIELDS or outcome not in ("response", "error"):
            raise ValueError(
                f"Cost record requires a supported provider and response/error outcome; "
                f"provider={provider!r}, outcome={outcome!r}"
            )
        usage, usd, reason = price_usage(provider, operation, model, usage, service_tier, modality)
        row = dict(
            provider=provider,
            operation=operation,
            model=model,
            outcome=outcome,
            usage=usage,
            usd=usd,
            reason=reason,
        )
        with lock:
            if closed:
                raise OSError(f"Cost recorder is closed: {directory}")
            if accounting_failed:
                raise OSError(f"Cost accounting previously failed: {directory}")
            try:
                calls.write(json.dumps(row, allow_nan=False) + "\n")
                calls.flush()
            except OSError:
                accounting_failed = True
                raise
            call_count += 1
            saw_error = saw_error or outcome == "error"
            totals = providers.setdefault(
                provider, {"known_estimated_usd": 0.0, "unknown_cost_count": 0, "call_count": 0}
            )
            totals["known_estimated_usd"] += usd or 0
            totals["unknown_cost_count"] += usd is None
            totals["call_count"] += 1

    def finish(*, run_failed=False):
        nonlocal closed
        with lock:
            if closed:
                raise OSError(f"Cost recorder is closed: {directory}")
            closed = True
            calls.close()
            if accounting_failed:
                raise OSError(f"Cost accounting failed; no trustworthy summary: {directory}")
            unknown = sum(p["unknown_cost_count"] for p in providers.values())
            summary = dict(
                providers=providers,
                call_count=call_count,
                known_estimated_usd=sum(p["known_estimated_usd"] for p in providers.values()),
                unknown_cost_count=unknown,
                run_failed=run_failed or saw_error,
                all_observed_costs_known=not unknown,
                pricing_date=PRICING_DATE,
                pricing_basis=PRICING_BASIS,
                pricing_sources=PRICING_SOURCES,
                limitations=LIMITATIONS,
            )
            (directory / "summary.json").write_text(
                json.dumps(summary, allow_nan=False) + "\n", encoding="utf-8"
            )

    return record, finish
