"""Source facts returned by the offline v2 inventory preparation step."""

import math
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class InventoryCorpus(BaseModel):
    source_commit: str = Field(pattern=r"^[0-9a-fA-F]{40}$")
    collection_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    inventory_scope: Literal["full", "selected"]


class SourceDocument(BaseModel):
    source_id: str
    source_path: str
    output_filename: str
    source_url: str
    bucket: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SourceInventory(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    corpus: InventoryCorpus
    documents: list[SourceDocument]


def _check_json(value, field):
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError(f"{field}: JSON object keys must be strings")
            _check_json(key, field)
            _check_json(item, field)
    elif type(value) is list:
        for item in value:
            _check_json(item, field)
    elif type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError(f"{field}: text must encode as UTF-8") from None
    elif type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{field}: numbers must be finite")
    elif value is not None and type(value) not in (bool, int):
        raise ValueError(f"{field}: must contain only JSON values")


def _check_settings(value, expected, field):
    if type(value) is not type(expected):
        raise ValueError(f"{field}: unsupported setting type")
    if isinstance(expected, dict):
        if value.keys() != expected.keys():
            raise ValueError(f"{field}: missing or unknown settings")
        for key in expected:
            _check_settings(value[key], expected[key], f"{field}.{key}")
    elif value != expected:
        raise ValueError(f"{field}: unsupported setting")


def _validate_input_policy(value):
    policy = value.get("input_policy")
    estimator = policy.get("estimator") if isinstance(policy, dict) else None
    version = estimator.get("package_version") if isinstance(estimator, dict) else None
    if not isinstance(version, str) or not version.strip():
        raise ValueError("input_policy.estimator.package_version: must be nonblank")
    _check_settings(
        policy,
        {
            "selection": "full_or_prefix",
            "max_estimated_input_tokens": 32000,
            "safety_margin_tokens": 2000,
            "estimator": {
                "package": "tiktoken",
                "package_version": version,
                "encoding": "o200k_base",
            },
        },
        "input_policy",
    )


def _validate_snapshots(value):
    prompt = value.get("prompt_template")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt_template: must be nonblank")
    schema = value.get("response_schema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("response_schema: must have an object root")
    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, dict) or not properties:
        raise ValueError("response_schema.properties: must be nonempty")
    if (
        not isinstance(required, list)
        or any(type(key) is not str for key in required)
        or len(required) != len(properties)
        or set(required) != set(properties)
    ):
        raise ValueError("response_schema.required: must name exactly its properties")
    if schema.get("additionalProperties") is not False:
        raise ValueError("response_schema.additionalProperties: must be false")


def _validate_recipe_fields(value, saved=False):
    if not isinstance(value, dict):
        raise ValueError("recipe: must be an object")
    try:
        _check_json(value, "recipe")
    except RecursionError:
        raise ValueError("recipe: JSON nesting is cyclic or too deep") from None
    spec = value.get("analysis_spec")
    spec_keys = {"provider", "requested_model", "request_settings"}
    if saved:
        spec_keys |= {"prompt_template_sha256", "response_schema_sha256"}
    if not isinstance(spec, dict) or spec.keys() != spec_keys:
        raise ValueError("analysis_spec: missing or unknown fields")
    _check_settings(spec["provider"], "openai", "analysis_spec.provider")
    _check_settings(
        spec["requested_model"], "gpt-5.4-mini-2026-03-17", "analysis_spec.requested_model"
    )
    _check_settings(
        spec["request_settings"],
        {
            "reasoning": {"effort": "low"},
            "text": {
                "verbosity": "low",
                "format": {"type": "json_schema", "name": "document_analysis_v1", "strict": True},
            },
            "max_output_tokens": 8000,
            "truncation": "disabled",
            "store": False,
            "service_tier": "default",
            "tools": [],
        },
        "analysis_spec.request_settings",
    )
    _check_settings(
        value.get("execution_policy"),
        {
            "concurrency": 1,
            "client_timeout_seconds": 240,
            "sdk_max_retries": 0,
            "retry_owner": "pipeline",
            "max_attempts_per_cycle": 3,
            "conversation_mode": "independent_documents",
        },
        "execution_policy",
    )
    _validate_input_policy(value)
    _validate_snapshots(value)
    return value


class AnalysisRecipe(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "hide_input_in_errors": True}

    api: Literal["responses"]
    analysis_spec: dict
    prompt_template: str
    response_schema: dict
    execution_policy: dict
    input_policy: dict

    @model_validator(mode="before")
    @classmethod
    def validate_recipe(cls, value):
        return _validate_recipe_fields(value)


class RunMetadata(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "hide_input_in_errors": True}

    schema_version: Literal["1.0"]
    run_id: str
    created_at: str
    source_manifest_path: str
    source_inventory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    api: Literal["responses"]
    analysis_spec: dict
    prompt_template: str
    response_schema: dict
    execution_policy: dict
    input_policy: dict

    @model_validator(mode="before")
    @classmethod
    def validate_metadata(cls, value):
        _validate_recipe_fields(value, saved=True)
        for name in ("prompt_template_sha256", "response_schema_sha256"):
            digest = value["analysis_spec"][name]
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError(f"analysis_spec.{name}: must be a lowercase SHA-256")
        run_id = value.get("run_id")
        created_at = value.get("created_at")
        if not isinstance(run_id, str) or not re.fullmatch(
            r"[0-9]{8}_[0-9]{6}_[0-9a-f]{4}", run_id
        ):
            raise ValueError("run_id: invalid format")
        if not isinstance(created_at, str) or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", created_at
        ):
            raise ValueError("created_at: must be UTC to seconds")
        try:
            instant = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            raise ValueError("created_at: invalid timestamp") from None
        if instant.strftime("%Y%m%d_%H%M%S") != run_id[:15]:
            raise ValueError("run_id: does not match created_at")
        path = value.get("source_manifest_path")
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise ValueError("source_manifest_path: must be absolute")
        return value
