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


class AnalysisScores(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "hide_input_in_errors": True}

    information_density: int = Field(ge=1, le=10)
    practical_value: int = Field(ge=1, le=10)
    clarity: int = Field(ge=1, le=10)
    completeness: int = Field(ge=1, le=10)
    self_contained: int = Field(ge=1, le=10)


class DocumentAnalysis(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "hide_input_in_errors": True}

    scores: AnalysisScores
    topics: list[str]
    injection_suspected: bool
    injection_evidence: str = Field(max_length=200)
    knowledge_domain: Literal[
        "core_concepts",
        "implementation_patterns",
        "api_and_framework_details",
        "integration_scenarios",
        "troubleshooting",
    ]
    content_type: Literal["tutorial", "reference", "overview", "specification", "examples"]
    content_kind: Literal["docs", "code"]
    code_examples_present: bool
    one_sentence_summary: str
    key_concepts: list[str]
    fastapi_features: list[str]

    @model_validator(mode="before")
    @classmethod
    def validate_json(cls, value):
        _check_json(value, "analysis")
        return value


def _checkpoint_keys(value, names, field):
    if not isinstance(value, dict) or value.keys() != set(names.split()):
        raise ValueError(f"checkpoint {field}: missing or unknown fields")


def _checkpoint_time(value, nullable=False):
    if value is None and nullable:
        return
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value
    ):
        raise ValueError("checkpoint: expected UTC timestamp with Z")
    datetime.fromisoformat(value)


def _checkpoint_provenance(value):
    _checkpoint_keys(
        value,
        "text_sha256 original_characters submitted_characters truncated selection transformation",
        "provenance",
    )
    if not isinstance(value["text_sha256"], str) or not re.fullmatch(
        "[0-9a-f]{64}", value["text_sha256"]
    ):
        raise ValueError("checkpoint: invalid supplied-text hash")
    original, submitted = value["original_characters"], value["submitted_characters"]
    if (
        type(original) is not int
        or type(submitted) is not int
        or original < 0
        or submitted < 0
        or type(value["truncated"]) is not bool
    ):
        raise ValueError("checkpoint: invalid supplied-text counts")
    if value["selection"] == "full":
        if (
            value["truncated"]
            or submitted != original
            or value["transformation"] != "UTF-8 decoding; no newline normalization"
        ):
            raise ValueError("checkpoint: invalid full-text provenance")
    elif value["selection"] == "prefix":
        if (
            not value["truncated"]
            or not 1 <= submitted - 168 < original
            or value["transformation"]
            != "UTF-8 decoding; character-prefix selection; appended pipeline disclosure; no newline normalization"
        ):
            raise ValueError("checkpoint: invalid prefix provenance")
    else:
        raise ValueError("checkpoint: unsupported supplied-text selection")


def _checkpoint_attempt(attempt, number):
    _checkpoint_keys(
        attempt, "attempt state reserved_at finished_at response_model error", "attempt"
    )
    if type(attempt["attempt"]) is not int or attempt["attempt"] != number:
        raise ValueError("checkpoint: attempts must be sequential")
    state = attempt["state"]
    if state not in ("reserved", "interrupted", "succeeded", "failed"):
        raise ValueError("checkpoint: unknown attempt state")
    _checkpoint_time(attempt["reserved_at"])
    _checkpoint_time(attempt["finished_at"], nullable=state in ("reserved", "interrupted"))
    if state in ("reserved", "interrupted") and any(
        attempt[key] is not None for key in ("finished_at", "response_model", "error")
    ):
        raise ValueError("checkpoint: unresolved attempt cannot have response evidence")
    if attempt["response_model"] is not None and (
        not isinstance(attempt["response_model"], str) or not attempt["response_model"]
    ):
        raise ValueError("checkpoint: invalid returned model")
    if state == "failed":
        _checkpoint_keys(attempt["error"], "code message", "attempt error")
        if any(
            not isinstance(attempt["error"][key], str) or not attempt["error"][key]
            for key in ("code", "message")
        ):
            raise ValueError("checkpoint: invalid attempt error")
    elif attempt["error"] is not None:
        raise ValueError("checkpoint: unexpected attempt error")

    if state == "failed" and (
        attempt["error"]["code"] not in ERROR_MESSAGES
        or attempt["error"]["code"] == "interrupted_attempts_exhausted"
        or attempt["error"]["message"] != ERROR_MESSAGES[attempt["error"]["code"]]
    ):
        raise ValueError("checkpoint: unsupported or unsanitized attempt error")
    if attempt["finished_at"] is not None and datetime.fromisoformat(
        attempt["finished_at"]
    ) < datetime.fromisoformat(attempt["reserved_at"]):
        raise ValueError("checkpoint: attempt finishes before reservation")


def _checkpoint_failure(cycle, attempts, last):
    _checkpoint_keys(cycle["analysis_error"], "code attempts message", "analysis error")
    error = cycle["analysis_error"]
    if (
        not isinstance(error["code"], str)
        or error["code"] not in ERROR_MESSAGES
        or error["message"] != ERROR_MESSAGES[error["code"]]
    ):
        raise ValueError("checkpoint: unsupported or unsanitized final error")
    if error["code"] in TRANSIENT_CODES and len(attempts) != 3:
        raise ValueError("checkpoint: temporary failure finalized before exhaustion")
    if (
        cycle["model_analysis"] is not None
        or type(error["attempts"]) is not int
        or error["attempts"] != len(attempts)
    ):
        raise ValueError("checkpoint: failure has predictions or wrong attempt count")
    if last["state"] == "failed":
        if {key: error[key] for key in ("code", "message")} != last["error"]:
            raise ValueError("checkpoint: final and attempt errors disagree")
    elif (
        last["state"] != "interrupted"
        or len(attempts) != 3
        or error["code"] != "interrupted_attempts_exhausted"
    ):
        raise ValueError("checkpoint: invalid final failure")
    _checkpoint_time(cycle["finished_at"])
    if last["state"] == "failed" and cycle["finished_at"] != last["finished_at"]:
        raise ValueError("checkpoint: failed finish times disagree")


def _checkpoint_cycle_attempts(cycle):
    attempts = cycle["attempts"]
    if not isinstance(attempts, list) or not 1 <= len(attempts) <= 3:
        raise ValueError("checkpoint: cycles need one to three attempts")
    for index, attempt in enumerate(attempts, 1):
        _checkpoint_attempt(attempt, index)
        if (
            index < len(attempts)
            and attempt["state"] == "failed"
            and attempt["error"]["code"] not in TRANSIENT_CODES
        ):
            raise ValueError("checkpoint: nonretryable attempt followed by another attempt")
        if index < len(attempts) and attempt["state"] not in ("failed", "interrupted"):
            raise ValueError("checkpoint: only last attempt can be reserved or successful")
    return attempts


def _checkpoint_cycle(cycle, number):
    _checkpoint_keys(
        cycle, "cycle attempts outcome model_analysis analysis_error finished_at", "cycle"
    )
    if type(cycle["cycle"]) is not int or cycle["cycle"] != number:
        raise ValueError("checkpoint: cycles must be sequential")
    attempts = _checkpoint_cycle_attempts(cycle)
    outcome, last = cycle["outcome"], attempts[-1]
    if outcome is None:
        if (
            last["state"] == "failed"
            and (last["error"]["code"] not in TRANSIENT_CODES or len(attempts) == 3)
            or last["state"] == "interrupted"
            and len(attempts) == 3
        ):
            raise ValueError("checkpoint: terminal failure cannot be unfinished")
        if (
            any(
                cycle[key] is not None
                for key in ("model_analysis", "analysis_error", "finished_at")
            )
            or last["state"] == "succeeded"
        ):
            raise ValueError("checkpoint: unfinished cycle has final evidence")
    elif outcome in ("ready", "quarantined"):
        prediction = DocumentAnalysis.model_validate(cycle["model_analysis"])
        if (
            last["state"] != "succeeded"
            or cycle["analysis_error"] is not None
            or prediction.injection_suspected != (outcome == "quarantined")
        ):
            raise ValueError("checkpoint: successful outcome disagrees with result")
        _checkpoint_time(cycle["finished_at"])
        if cycle["finished_at"] != last["finished_at"]:
            raise ValueError("checkpoint: successful finish times disagree")
    elif outcome == "analysis_failed":
        _checkpoint_failure(cycle, attempts, last)
    else:
        raise ValueError("checkpoint: unknown outcome")


def _checkpoint_document(doc, seen):
    reserved = 0
    _checkpoint_keys(doc, "source_id supplied_document cycles", "document")
    if not isinstance(doc["source_id"], str) or not doc["source_id"] or doc["source_id"] in seen:
        raise ValueError("checkpoint: invalid or duplicate source_id")
    seen.add(doc["source_id"])
    if not isinstance(doc["cycles"], list):
        raise ValueError("checkpoint: cycles must be a list")
    for number, cycle in enumerate(doc["cycles"], 1):
        _checkpoint_cycle(cycle, number)
        if number < len(doc["cycles"]) and cycle["outcome"] != "analysis_failed":
            raise ValueError("checkpoint: only final failures can have a later cycle")
        if number < len(doc["cycles"]) and cycle["analysis_error"]["code"] not in RETRY_CODES:
            raise ValueError("checkpoint: ineligible failure received another cycle")
        reserved += sum(attempt["state"] == "reserved" for attempt in cycle["attempts"])
    if doc["supplied_document"] is not None:
        _checkpoint_provenance(doc["supplied_document"])
    if doc["cycles"] and doc["supplied_document"] is None:
        raise ValueError("checkpoint: attempted document needs provenance")
    return reserved


class AnalysisCheckpoint(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "hide_input_in_errors": True}

    schema_version: Literal["1.0"]
    run_id: str
    input_hashes: dict
    retry_not_before: str | None
    documents: list[dict]

    @model_validator(mode="after")
    def validate_checkpoint(self):
        _check_json(self.model_dump(), "checkpoint")
        _checkpoint_keys(self.input_hashes, "run_metadata source_inventory", "input hashes")
        if any(
            not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value)
            for value in self.input_hashes.values()
        ):
            raise ValueError("checkpoint: invalid input hashes")
        _checkpoint_time(self.retry_not_before, nullable=True)
        reserved, seen = 0, set()
        for doc in self.documents:
            reserved += _checkpoint_document(doc, seen)
        if reserved > 1:
            raise ValueError("checkpoint: multiple outstanding reservations")
        return self


ERROR_MESSAGES = {
    "provider_timeout": "Analysis did not complete within the configured client timeout.",
    "provider_connection_error": "The provider connection failed.",
    "provider_transient_error": "The provider reported a temporary request failure.",
    "provider_rate_limited": "The provider temporarily throttled the request.",
    "provider_server_error": "The provider reported a server failure.",
    "output_token_limit": "Analysis exceeded the saved output token limit.",
    "content_filtered": "The provider filtered the analysis output.",
    "model_refusal": "The model refused the analysis request.",
    "empty_output": "The completed response contained no analysis text.",
    "invalid_json": "The analysis output was not valid JSON.",
    "invalid_analysis": "The analysis output failed schema or evidence validation.",
    "unexpected_provider_response": "The provider returned an unsupported response envelope.",
    "authentication_error": "The provider rejected authentication.",
    "permission_denied": "The provider denied access.",
    "quota_exhausted": "The provider reported exhausted quota or credits.",
    "model_unavailable": "The requested model is unavailable.",
    "input_too_large": "The provider rejected the request input size; inspect input counting.",
    "request_rejected": "The provider rejected the saved request; inspect configuration.",
    "interrupted_attempts_exhausted": "The final reserved attempt has no saved result; no attempts remain in this cycle.",
}
TRANSIENT_CODES = (
    "provider_timeout",
    "provider_connection_error",
    "provider_transient_error",
    "provider_rate_limited",
    "provider_server_error",
)
RETRY_CODES = (
    *TRANSIENT_CODES,
    "interrupted_attempts_exhausted",
    "authentication_error",
    "permission_denied",
    "quota_exhausted",
)
STOP_CODES = (
    "unexpected_provider_response",
    "authentication_error",
    "permission_denied",
    "quota_exhausted",
    "model_unavailable",
    "input_too_large",
    "request_rejected",
)


def normalize_topics(topics: list[str]) -> list[str]:
    normalized = []
    for topic in topics:
        if not isinstance(topic, str):
            raise ValueError("publication topics: labels must be strings")
        label = re.sub(r"[\s_-]+", "_", topic.lower()).strip("_")
        if any(character.isalnum() for character in label) and label not in normalized:
            normalized.append(label)
    return normalized


def _manifest_document(doc, context):
    source_keys = [
        "source_id",
        "source_path",
        "output_filename",
        "source_url",
        "bucket",
        "content_sha256",
    ]
    _checkpoint_keys(
        doc,
        " ".join(source_keys) + " outcome exclusion_reason overall_quality model_analysis"
        " assigned_topics topic_reviews analysis_provenance analysis_error",
        "publication document",
    )
    SourceDocument.model_validate({key: doc[key] for key in source_keys}, strict=True)
    if any(not doc[key] for key in source_keys) or doc["topic_reviews"] != []:
        raise ValueError(f"{context}: empty source fact or unexpected topic reviews")
    provenance = doc["analysis_provenance"]
    _checkpoint_keys(
        provenance, "finished_at response_model supplied_document", "publication provenance"
    )
    _checkpoint_time(provenance["finished_at"])
    _checkpoint_provenance(provenance["supplied_document"])
    if provenance["response_model"] is not None and (
        not isinstance(provenance["response_model"], str) or not provenance["response_model"]
    ):
        raise ValueError(f"{context}: invalid returned model")
    reasons = {
        "ready": None,
        "quarantined": "injection_policy",
        "analysis_failed": "analysis_unavailable",
    }
    if not isinstance(doc["outcome"], str) or doc["outcome"] not in reasons:
        raise ValueError(f"{context}: invalid outcome")
    if doc["exclusion_reason"] != reasons[doc["outcome"]]:
        raise ValueError(f"{context}: exclusion reason disagrees with outcome")
    if doc["outcome"] == "analysis_failed":
        _manifest_failed_analysis(doc, context)
        return
    _manifest_prediction(doc, context)


def _manifest_failed_analysis(doc, context):
    error = doc["analysis_error"]
    _checkpoint_keys(error, "code attempts message", "publication error")
    if (
        not isinstance(error["code"], str)
        or error["code"] not in ERROR_MESSAGES
        or error["message"] != ERROR_MESSAGES[error["code"]]
        or type(error["attempts"]) is not int
        or not 1 <= error["attempts"] <= 3
        or (
            error["code"] in (*TRANSIENT_CODES, "interrupted_attempts_exhausted")
            and error["attempts"] != 3
        )
    ):
        raise ValueError(f"{context}: invalid controlled analysis error")
    if (
        doc["model_analysis"] is not None
        or doc["overall_quality"] is not None
        or doc["assigned_topics"] != []
    ):
        raise ValueError(f"{context}: failed analysis cannot have predictions, quality or topics")


def _manifest_prediction(doc, context):
    prediction = DocumentAnalysis.model_validate(doc["model_analysis"])
    if (
        doc["analysis_error"] is not None
        or prediction.injection_suspected != (doc["outcome"] == "quarantined")
        or bool(prediction.injection_evidence) != prediction.injection_suspected
    ):
        raise ValueError(f"{context}: successful result disagrees with outcome/evidence")
    quality = doc["overall_quality"]
    if type(quality) not in (int, float) or quality != round(
        sum(prediction.scores.model_dump().values()) / 5, 1
    ):
        raise ValueError(f"{context}: quality disagrees with the five scores")
    expected = [
        dict(topic=topic, assigned_by="model", review_status="unreviewed")
        for topic in normalize_topics(prediction.topics)
    ]
    if doc["assigned_topics"] != expected:
        raise ValueError(f"{context}: effective topics disagree with model topics")


def _manifest_analysis_spec(analysis_spec):
    _checkpoint_keys(
        analysis_spec,
        "provider requested_model request_settings prompt_template_sha256 response_schema_sha256",
        "publication analysis spec",
    )
    if (
        not isinstance(analysis_spec["provider"], str)
        or not analysis_spec["provider"]
        or not isinstance(analysis_spec["requested_model"], str)
        or not analysis_spec["requested_model"]
        or not isinstance(analysis_spec["request_settings"], dict)
    ):
        raise ValueError("publication: invalid analysis specification")
    _check_settings(analysis_spec["provider"], "openai", "publication provider")
    _check_settings(
        analysis_spec["requested_model"],
        "gpt-5.4-mini-2026-03-17",
        "publication requested model",
    )
    _check_settings(
        analysis_spec["request_settings"],
        {
            "reasoning": {"effort": "low"},
            "text": {
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "name": "document_analysis_v1",
                    "strict": True,
                },
            },
            "max_output_tokens": 8000,
            "truncation": "disabled",
            "store": False,
            "service_tier": "default",
            "tools": [],
        },
        "publication request settings",
    )


def _manifest_input_hashes(input_hashes, analysis_spec):
    _checkpoint_keys(
        input_hashes,
        "run_metadata source_inventory analysis_results topic_reviews",
        "publication input hashes",
    )
    hashes = [input_hashes[key] for key in ("run_metadata", "source_inventory", "analysis_results")]
    hashes.extend(
        analysis_spec[key] for key in ("prompt_template_sha256", "response_schema_sha256")
    )
    if any(
        not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value) for value in hashes
    ):
        raise ValueError("publication: invalid input/snapshot hashes")
    if input_hashes["topic_reviews"] is not None:
        raise ValueError("publication: no topic review input is supported")


class EnrichmentManifest(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "hide_input_in_errors": True}

    schema_version: Literal["1.0"]
    run_id: str = Field(pattern=r"^[0-9]{8}_[0-9]{6}_[0-9a-f]{4}$")
    publication_id: str = Field(pattern=r"^[0-9]{3,}$")
    published_at: str
    corpus: dict
    analysis_spec: dict
    input_hashes: dict
    documents: list[dict]

    @model_validator(mode="after")
    def validate_manifest(self):
        _check_json(self.model_dump(), "publication")
        _checkpoint_time(self.published_at)
        if int(self.publication_id) < 1 or self.publication_id != f"{int(self.publication_id):03d}":
            raise ValueError("publication: invalid revision number")
        corpus = InventoryCorpus.model_validate(self.corpus, strict=True)
        if corpus.model_dump() != self.corpus:
            raise ValueError("publication: unexpected corpus fields")
        _manifest_analysis_spec(self.analysis_spec)
        _manifest_input_hashes(self.input_hashes, self.analysis_spec)
        seen = {key: set() for key in ("source_id", "source_path", "output_filename")}
        if not self.documents:
            raise ValueError("publication: inventory must contain documents")
        for index, doc in enumerate(self.documents):
            context = f"publication documents[{index}]"
            _manifest_document(doc, context)
            for key, values in seen.items():
                if doc[key] in values:
                    raise ValueError(f"{context}: duplicate {key}")
                values.add(doc[key])
        return self
