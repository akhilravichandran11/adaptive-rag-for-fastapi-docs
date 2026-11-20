import hashlib
import json
import tempfile
import uuid
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

from data_curation.pipeline_v2.schemas import AnalysisRecipe, RunMetadata, SourceInventory
from data_curation.pipeline_v2.sources import prepare_source_inventory


def _artifact_bytes(value, name):
    try:
        return (
            json.dumps(
                value,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                separators=(",", ": "),
            )
            + "\n"
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError):
        raise ValueError(f"{name}: cannot encode JSON artifact") from None


def _snapshot_hashes(recipe):
    schema = json.dumps(
        recipe.response_schema,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "prompt_template_sha256": hashlib.sha256(
            recipe.prompt_template.encode("utf-8")
        ).hexdigest(),
        "response_schema_sha256": hashlib.sha256(schema).hexdigest(),
    }


def _validate_written_run(temporary, final, buffers):
    saved = {}
    for name, expected in buffers.items():
        content = (temporary / name).read_bytes()
        try:
            saved[name] = json.loads(content)
        except (ValueError, UnicodeError):
            raise ValueError(f"{temporary / name}: invalid JSON") from None
        if content != expected:
            raise ValueError(f"{temporary / name}: readback differs from prepared bytes")
    SourceInventory.model_validate(saved["source_inventory.json"])
    run = RunMetadata.model_validate(saved["run_metadata.json"])
    if run.run_id != final.name:
        raise ValueError(f"{temporary}: run_id differs from intended directory")
    if run.source_inventory_sha256 != hashlib.sha256(buffers["source_inventory.json"]).hexdigest():
        raise ValueError(f"{temporary}: source_inventory_sha256 mismatch")
    for key, digest in _snapshot_hashes(run).items():
        if run.analysis_spec[key] != digest:
            raise ValueError(f"{temporary}: {key} mismatch")


def prepare_run(
    manifest_path: Path,
    *,
    recipe: AnalysisRecipe,
    output_root: Path,
    selected_source_paths: tuple[str, ...] | None = None,
) -> Path:
    instant = datetime.now(UTC)
    run_id = f"{instant:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:4]}"
    try:
        output_root = output_root.resolve()
    except RuntimeError:
        # Python 3.11/3.12 use RuntimeError for a symlink loop during resolve.
        raise OSError(f"{output_root}: cannot resolve output root (symlink loop)") from None
    final = output_root / run_id
    if final.exists() or final.is_symlink():
        raise FileExistsError(f"Run destination occupied: {final}")
    if not isinstance(recipe, AnalysisRecipe):
        raise ValueError("recipe: must be an AnalysisRecipe")
    recipe = AnalysisRecipe.model_validate(dict(recipe))
    try:
        installed_version = metadata.version("tiktoken")
    except metadata.PackageNotFoundError:
        raise ValueError("input_policy.estimator: tiktoken distribution is missing") from None
    if recipe.input_policy["estimator"]["package_version"] != installed_version:
        raise ValueError("input_policy.estimator.package_version: installed version mismatch")
    try:
        manifest_path = manifest_path.resolve()
    except RuntimeError:
        raise OSError(f"{manifest_path}: cannot resolve manifest (symlink loop)") from None
    inventory = prepare_source_inventory(manifest_path, selected_source_paths=selected_source_paths)
    inventory_bytes = _artifact_bytes(inventory.model_dump(), "source_inventory")
    values = recipe.model_dump()
    values["analysis_spec"] = {**values["analysis_spec"], **_snapshot_hashes(recipe)}
    run = RunMetadata(
        schema_version="1.0",
        run_id=run_id,
        created_at=instant.strftime("%Y-%m-%dT%H:%M:%SZ"),
        source_manifest_path=str(manifest_path),
        source_inventory_sha256=hashlib.sha256(inventory_bytes).hexdigest(),
        **values,
    )
    buffers = {
        "source_inventory.json": inventory_bytes,
        "run_metadata.json": _artifact_bytes(run.model_dump(), "run_metadata"),
    }
    output_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".prepare-{run_id}-", dir=output_root))
    for name, content in buffers.items():
        (temporary / name).write_bytes(content)
    _validate_written_run(temporary, final, buffers)
    if final.exists() or final.is_symlink():
        raise FileExistsError(f"Run destination occupied: {final}")
    temporary.rename(final)
    return final
