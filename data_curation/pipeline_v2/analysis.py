import hashlib
import json
import math
import os
import tempfile
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import openai
import tiktoken
from openai.types.responses import Response
from pydantic import ValidationError

from data_curation.pipeline_v2.artifacts import artifact_bytes, snapshot_hashes
from data_curation.pipeline_v2.recipe import build_analysis_recipe
from data_curation.pipeline_v2.schemas import (
    ERROR_MESSAGES,
    RETRY_CODES,
    STOP_CODES,
    TRANSIENT_CODES,
    AnalysisCheckpoint,
    DocumentAnalysis,
    RunMetadata,
    SourceInventory,
)
from data_curation.pipeline_v2.sources import prepare_source_inventory


def _read_json(path):
    content = path.read_bytes()
    try:
        return content, json.loads(content)
    except (ValueError, UnicodeError):
        raise ValueError(f"analysis preflight {path}: invalid JSON") from None


def _read_run(run_dir):
    metadata_bytes, saved = _read_json(run_dir / "run_metadata.json")
    run = RunMetadata.model_validate(saved)
    if run.run_id != run_dir.name:
        raise ValueError(f"analysis preflight {run_dir}: run_id mismatch")
    recipe = build_analysis_recipe()
    expected = recipe.model_dump()
    expected["analysis_spec"].update(snapshot_hashes(recipe))
    if any(saved[key] != value for key, value in expected.items()):
        raise ValueError(f"analysis preflight {run_dir}: unsupported saved recipe")
    inventory_bytes, saved_inventory = _read_json(run_dir / "source_inventory.json")
    inventory = SourceInventory.model_validate(saved_inventory)
    if hashlib.sha256(inventory_bytes).hexdigest() != run.source_inventory_sha256:
        raise ValueError(f"analysis preflight {run_dir}: inventory hash mismatch")
    selection = None
    if inventory.corpus.inventory_scope == "selected":
        selection = tuple(doc.source_path for doc in inventory.documents)
    current = prepare_source_inventory(
        Path(run.source_manifest_path), selected_source_paths=selection
    )
    if current.model_dump() != saved_inventory:
        raise ValueError(f"analysis preflight {run_dir}: inventory/source binding changed")
    hashes = {
        "run_metadata": hashlib.sha256(metadata_bytes).hexdigest(),
        "source_inventory": hashlib.sha256(inventory_bytes).hexdigest(),
    }
    return run, inventory, hashes


DISCLOSURE = "\n\n[Pipeline disclosure: only a prefix of the original document is supplied. The remainder was omitted to fit the saved input budget. Assess only the supplied excerpt.]\n"


def _compact(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )


def _request(run, source_path, original, selected, prefix):
    supplied = original[:selected] + (DISCLOSURE if prefix else "")
    context = {
        "source_path": source_path,
        "selection": "prefix" if prefix else "full",
        "original_characters": len(original),
        "selected_characters": selected,
    }
    settings = run.analysis_spec["request_settings"]
    return {
        **settings,
        "model": run.analysis_spec["requested_model"],
        "instructions": run.prompt_template,
        "text": {
            **settings["text"],
            "format": {**settings["text"]["format"], "schema": run.response_schema},
        },
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": _compact(context)},
                    {"type": "input_text", "text": supplied},
                ],
            }
        ],
    }


def _fit_request(run, doc, encoding):
    docs_dir = (Path(run.source_manifest_path).parent / "docs").resolve()
    path = (docs_dir / doc.output_filename).resolve()
    if not path.is_relative_to(docs_dir):
        raise ValueError(f"analysis input {doc.source_id}: path escapes docs")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != doc.content_sha256:
        raise ValueError(f"analysis input {doc.source_id}: source hash changed")
    original = content.decode("utf-8")
    limit = (
        run.input_policy["max_estimated_input_tokens"] - run.input_policy["safety_margin_tokens"]
    )
    selected, prefix = len(original), False
    request = _request(run, doc.source_path, original, selected, prefix)
    if len(encoding.encode_ordinary(_compact(request))) > limit:
        selected, prefix = 1, True
        request = _request(run, doc.source_path, original, selected, prefix)
        if not original or len(encoding.encode_ordinary(_compact(request))) > limit:
            raise ValueError(f"analysis input {doc.source_id}: minimum request cannot fit")
        low, high = 2, len(original) - 1
        while low <= high:
            candidate = (low + high) // 2
            request = _request(run, doc.source_path, original, candidate, True)
            if len(encoding.encode_ordinary(_compact(request))) <= limit:
                selected, low = candidate, candidate + 1
            else:
                high = candidate - 1
    request = _request(run, doc.source_path, original, selected, prefix)
    if len(encoding.encode_ordinary(_compact(request))) > limit:
        raise ValueError(f"analysis input {doc.source_id}: final request cannot fit")
    supplied = request["input"][0]["content"][1]["text"]
    provenance = {
        "text_sha256": hashlib.sha256(supplied.encode("utf-8")).hexdigest(),
        "original_characters": len(original),
        "submitted_characters": len(supplied),
        "truncated": prefix,
        "selection": "prefix" if prefix else "full",
        "transformation": "UTF-8 decoding; character-prefix selection; appended pipeline disclosure; no newline normalization"
        if prefix
        else "UTF-8 decoding; no newline normalization",
    }
    return request, provenance, original[:selected]


def _now():
    return datetime.fromtimestamp(time.time(), UTC).isoformat().replace("+00:00", "Z")


def _save_checkpoint(path, checkpoint):
    AnalysisCheckpoint.model_validate(checkpoint)
    content = artifact_bytes(checkpoint, str(path))
    with tempfile.NamedTemporaryFile(
        mode="wb", prefix=".analysis-", dir=path.parent, delete=False
    ) as temporary:
        temporary.write(content)
    os.replace(temporary.name, path)


def _provider_code(status, code, kind=None):
    codes = {value for value in (code, kind) if isinstance(value, str)}
    if codes & {
        "insufficient_quota",
        "quota_exceeded",
        "quota_exhausted",
        "credits_exhausted",
        "credit_balance_exhausted",
        "billing_hard_limit_reached",
        "billing_limit_reached",
        "usage_limit_reached",
        "spend_limit_reached",
    }:
        return "quota_exhausted"
    if status == 401:
        return "authentication_error"
    if status == 403:
        return "permission_denied"
    if codes & {"model_not_found", "model_unavailable", "model_not_available"}:
        return "model_unavailable"
    if codes & {"context_length_exceeded", "input_too_large", "request_too_large"} or status == 413:
        return "input_too_large"
    if status in (408, 409):
        return "provider_transient_error"
    if status is not None and 500 <= status <= 599 or status is None and "server_error" in codes:
        return "provider_server_error"
    if status in (None, 429) and codes & {
        "rate_limit_exceeded",
        "rate_limit_error",
        "requests_limit_exceeded",
        "tokens_limit_exceeded",
    }:
        return "provider_rate_limited"
    return "request_rejected"


def _call_provider(client, request, original):
    try:
        raw = client.responses.with_raw_response.create(**request)
        try:
            response = raw.parse()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None, "unexpected_provider_response", None, raw.headers
        if not isinstance(response, Response):
            return None, "unexpected_provider_response", None, raw.headers
        response = response.model_dump()
    except openai.APITimeoutError:
        return None, "provider_timeout", None, {}
    except openai.APIConnectionError:
        return None, "provider_connection_error", None, {}
    except openai.APIResponseValidationError as error:
        return None, "unexpected_provider_response", None, error.response.headers
    except openai.APIStatusError as error:
        return (
            None,
            _provider_code(error.status_code, error.code, error.type),
            None,
            error.response.headers,
        )
    prediction, code, model = _response_result(response, original)
    return prediction, code, model, raw.headers


def _validate_prediction(text, original, model):
    if not text.strip():
        return None, "empty_output", model
    try:
        value = json.loads(text)
    except ValueError:
        return None, "invalid_json", model
    try:
        prediction = DocumentAnalysis.model_validate(value)
        evidence = prediction.injection_evidence
        if (prediction.injection_suspected and (not evidence or evidence not in original)) or (
            not prediction.injection_suspected and evidence
        ):
            return None, "invalid_analysis", model
        prediction.model_dump_json()
    except (ValidationError, ValueError, UnicodeError):
        return None, "invalid_analysis", model
    return prediction.model_dump(), None, model


def _response_output(output, original, model):
    if not isinstance(output, list):
        return None, "unexpected_provider_response", model
    texts = []
    for item in output:
        if not isinstance(item, dict):
            return None, "unexpected_provider_response", model
        if item.get("type") == "reasoning":
            continue
        if (
            item.get("type") != "message"
            or item.get("role") != "assistant"
            or item.get("status") != "completed"
            or not isinstance(item.get("content"), list)
        ):
            return None, "unexpected_provider_response", model
        for block in item["content"]:
            if not isinstance(block, dict):
                return None, "unexpected_provider_response", model
            if block.get("type") == "refusal" and isinstance(block.get("refusal"), str):
                return None, "model_refusal", model
            if block.get("type") != "output_text" or not isinstance(block.get("text"), str):
                return None, "unexpected_provider_response", model
            texts.append(block["text"])
    return _validate_prediction("".join(texts), original, model)


def _response_result(response, original):
    model = response.get("model")
    if model is not None and (not isinstance(model, str) or not model):
        return None, "unexpected_provider_response", None
    status = response.get("status")
    if status == "failed":
        error = response.get("error")
        if not isinstance(error, dict) or not isinstance(error.get("code"), str):
            return None, "unexpected_provider_response", model
        return None, _provider_code(None, error["code"], error.get("type")), model
    if status == "incomplete":
        details = response.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, dict) else None
        if not isinstance(reason, str):
            return None, "unexpected_provider_response", model
        return (
            None,
            {"max_output_tokens": "output_token_limit", "content_filter": "content_filtered"}.get(
                reason, "unexpected_provider_response"
            ),
            model,
        )
    if (
        status != "completed"
        or response.get("error") is not None
        or response.get("incomplete_details") is not None
    ):
        return None, "unexpected_provider_response", model
    return _response_output(response.get("output"), original, model)


def _save_delay(checkpoint, headers, fallback):
    headers = {key.lower(): value for key, value in headers.items()}
    delay = None
    for name, divisor in (("retry-after-ms", 1000), ("retry-after", 1)):
        value = headers.get(name)
        if value is None:
            continue
        try:
            seconds = float(value) / divisor
        except (ValueError, TypeError):
            if name != "retry-after":
                continue
            try:
                instant = parsedate_to_datetime(value)
                if instant.tzinfo is None:
                    continue
                seconds = instant.timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                continue
        if math.isfinite(seconds) and seconds >= 0:
            delay = seconds
            break
    seconds = max(fallback, delay or 0)
    if seconds:
        try:
            checkpoint["retry_not_before"] = (
                datetime.fromtimestamp(time.time() + seconds, UTC)
                .isoformat()
                .replace("+00:00", "Z")
            )
        except (OverflowError, OSError, ValueError):
            raise ValueError("analysis cooldown: provider delay cannot be represented") from None


def _wait_for_cooldown(checkpoint):
    deadline = checkpoint["retry_not_before"]
    if deadline is None:
        return
    remaining = datetime.fromisoformat(deadline).timestamp() - time.time()
    if remaining > 60:
        raise RuntimeError(
            f"analysis cooldown: {remaining:g} seconds remain until {deadline}; invoke again later"
        )
    while remaining > 0:
        time.sleep(remaining)
        remaining = datetime.fromisoformat(deadline).timestamp() - time.time()
    checkpoint["retry_not_before"] = None


def _recover_reservation(path, checkpoint):
    for doc in checkpoint["documents"]:
        if not doc["cycles"]:
            continue
        cycle = doc["cycles"][-1]
        attempt = cycle["attempts"][-1]
        if attempt["state"] != "reserved":
            continue
        attempt["state"] = "interrupted"
        number = len(cycle["attempts"])
        if number == 3:
            code = "interrupted_attempts_exhausted"
            cycle.update(
                outcome="analysis_failed",
                analysis_error={"code": code, "attempts": 3, "message": ERROR_MESSAGES[code]},
                finished_at=_now(),
            )
        else:
            _save_delay(checkpoint, {}, 5 if number == 1 else 15)
        _save_checkpoint(path, checkpoint)


def _load_checkpoint(path, run, inventory, hashes):
    checkpoint = {
        "schema_version": "1.0",
        "run_id": run.run_id,
        "input_hashes": hashes,
        "retry_not_before": None,
        "documents": [
            {"source_id": doc.source_id, "supplied_document": None, "cycles": []}
            for doc in inventory.documents
        ],
    }
    if path.exists() or path.is_symlink():
        _, checkpoint = _read_json(path)
        AnalysisCheckpoint.model_validate(checkpoint)
        if (
            checkpoint["run_id"] != run.run_id
            or checkpoint["input_hashes"] != hashes
            or [doc["source_id"] for doc in checkpoint["documents"]]
            != [doc.source_id for doc in inventory.documents]
        ):
            raise ValueError(f"analysis preflight {path}: checkpoint binding mismatch")
    return checkpoint


def _prepare_inputs(run, inventory, checkpoint, encoding):
    inputs = []
    for source, doc in zip(inventory.documents, checkpoint["documents"], strict=True):
        fitted = _fit_request(run, source, encoding)
        if doc["supplied_document"] is not None and doc["supplied_document"] != fitted[1]:
            raise ValueError(f"analysis preflight {source.source_id}: supplied provenance changed")
        for cycle in doc["cycles"]:
            if cycle["model_analysis"] is not None:
                prediction = cycle["model_analysis"]
                evidence = prediction["injection_evidence"]
                if (
                    prediction["injection_suspected"]
                    and (not evidence or evidence not in fitted[2])
                ) or (not prediction["injection_suspected"] and evidence):
                    raise ValueError(
                        f"analysis preflight {source.source_id}: invalid saved injection evidence"
                    )
        inputs.append(fitted)
    return inputs


def _reserve_attempt(doc, provenance, cycle):
    if cycle is None:
        cycle = {
            "cycle": len(doc["cycles"]) + 1,
            "attempts": [],
            "outcome": None,
            "model_analysis": None,
            "analysis_error": None,
            "finished_at": None,
        }
        doc["supplied_document"] = provenance
        doc["cycles"].append(cycle)
    number = len(cycle["attempts"]) + 1
    attempt = {
        "attempt": number,
        "state": "reserved",
        "reserved_at": _now(),
        "finished_at": None,
        "response_model": None,
        "error": None,
    }
    cycle["attempts"].append(attempt)
    return cycle, attempt, number


def _record_result(checkpoint, cycle, attempt, number, response):
    prediction, code, response_model, headers = response
    finished = _now()
    attempt.update(
        state="failed" if code else "succeeded",
        finished_at=finished,
        response_model=response_model,
    )
    if code:
        attempt["error"] = {"code": code, "message": ERROR_MESSAGES[code]}
        fallback = (5 if number == 1 else 15) if code in TRANSIENT_CODES and number < 3 else 0
        _save_delay(checkpoint, headers, fallback)
        if code not in TRANSIENT_CODES or number == 3:
            cycle.update(
                outcome="analysis_failed",
                analysis_error={**attempt["error"], "attempts": number},
                finished_at=finished,
            )
    else:
        cycle.update(
            outcome="quarantined" if prediction["injection_suspected"] else "ready",
            model_analysis=prediction,
            finished_at=finished,
        )
    return code


def _retry_candidates(checkpoint):
    return {
        doc["source_id"]
        for doc in checkpoint["documents"]
        if doc["cycles"]
        and doc["cycles"][-1]["outcome"] == "analysis_failed"
        and doc["cycles"][-1]["analysis_error"]["code"] in RETRY_CODES
    }


def _analyze(run_dir, retry):
    run, inventory, hashes = _read_run(run_dir)
    encoding = tiktoken.get_encoding("o200k_base")
    path = run_dir / "analysis_results.json"
    checkpoint = _load_checkpoint(path, run, inventory, hashes)
    inputs = _prepare_inputs(run, inventory, checkpoint, encoding)
    retry_ids = _retry_candidates(checkpoint) if retry else set()
    _recover_reservation(path, checkpoint)
    client = None
    try:
        for source, doc, fitted in zip(
            inventory.documents, checkpoint["documents"], inputs, strict=True
        ):
            request, provenance, original = fitted
            if retry:
                if doc["source_id"] not in retry_ids:
                    continue
                cycle = None
            else:
                cycle = doc["cycles"][-1] if doc["cycles"] else None
                if cycle is not None and cycle["outcome"] is not None:
                    continue
            while cycle is None or cycle["outcome"] is None:
                _wait_for_cooldown(checkpoint)
                request, provenance, original = _fit_request(run, source, encoding)
                if doc["supplied_document"] is not None and doc["supplied_document"] != provenance:
                    raise ValueError(
                        f"analysis input {source.source_id}: supplied provenance changed"
                    )
                if client is None:
                    try:
                        client = openai.OpenAI(
                            timeout=run.execution_policy["client_timeout_seconds"], max_retries=0
                        )
                    except openai.OpenAIError:
                        raise ValueError(
                            f"analysis {run_dir}: client setup failed; check credentials and configuration"
                        ) from None
                cycle, attempt, number = _reserve_attempt(doc, provenance, cycle)
                _save_checkpoint(path, checkpoint)
                response = _call_provider(client, request, original)
                code = _record_result(checkpoint, cycle, attempt, number, response)
                _save_checkpoint(path, checkpoint)
                if code in STOP_CODES:
                    raise RuntimeError(
                        f"analysis {source.source_id}: {code}: {ERROR_MESSAGES[code]}"
                    )
    finally:
        if client is not None:
            client.close()
    if not path.exists():
        _save_checkpoint(path, checkpoint)
    return path


def analyze_run(run_dir: Path) -> Path:
    return _analyze(run_dir, retry=False)


def retry_failed(run_dir: Path) -> Path:
    return _analyze(run_dir, retry=True)
