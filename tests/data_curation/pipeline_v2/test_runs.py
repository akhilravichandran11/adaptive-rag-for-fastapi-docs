import builtins
import hashlib
import importlib
import json
import socket
import sys
import uuid
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from unittest.mock import Mock

import pytest

import data_curation.pipeline_v2 as pipeline_v2
from data_curation.pipeline_v2 import runs
from data_curation.pipeline_v2.runs import prepare_run
from data_curation.pipeline_v2.schemas import AnalysisRecipe, RunMetadata
from data_curation.pipeline_v2.sources import prepare_source_inventory


@pytest.fixture
def recipe_data():
    return json.loads("""{
  "api": "responses",
  "analysis_spec": {
    "provider": "openai",
    "requested_model": "gpt-5.4-mini-2026-03-17",
    "request_settings": {
      "reasoning": {"effort": "low"},
      "text": {
        "verbosity": "low",
        "format": {
          "type": "json_schema",
          "name": "document_analysis_v1",
          "strict": true
        }
      },
      "max_output_tokens": 8000,
      "truncation": "disabled",
      "store": false,
      "service_tier": "default",
      "tools": []
    }
  },
  "prompt_template": "OFFLINE PERSISTENCE FIXTURE ONLY. Return an object with the note field set to fixture. Do not use this recipe for document analysis.",
  "response_schema": {
    "type": "object",
    "properties": {
      "note": {
        "type": "string",
        "description": "A fixture note used only to verify snapshot persistence."
      }
    },
    "required": ["note"],
    "additionalProperties": false
  },
  "execution_policy": {
    "concurrency": 1,
    "client_timeout_seconds": 240,
    "sdk_max_retries": 0,
    "retry_owner": "pipeline",
    "max_attempts_per_cycle": 3,
    "conversation_mode": "independent_documents"
  },
  "input_policy": {
    "selection": "full_or_prefix",
    "max_estimated_input_tokens": 32000,
    "safety_margin_tokens": 2000,
    "estimator": {
      "package": "tiktoken",
      "package_version": "fixture-only-1",
      "encoding": "o200k_base"
    }
  }
}""")


def test_recipe_preserves_complete_snapshot(recipe_data):
    assert AnalysisRecipe.model_validate(recipe_data).model_dump() == recipe_data


@pytest.mark.parametrize(
    "path,value",
    [
        (("analysis_spec", "request_settings", "store"), 0),
        (("analysis_spec", "request_settings", "max_output_tokens"), 8000.0),
        (("analysis_spec", "request_settings", "tools"), ["web_search"]),
        (("analysis_spec", "request_settings", "temperature"), 0),
        (("execution_policy", "concurrency"), True),
        (("input_policy", "estimator", "package_version"), ""),
        (("response_schema", "properties"), {}),
        (("response_schema", "required"), ["unknown"]),
        (("response_schema", "additionalProperties"), 0),
        (("response_schema", "extra"), float("nan")),
        (("response_schema", "extra"), (1, 2)),
        (("response_schema", "extra"), {1: "value"}),
        (("prompt_template",), "  "),
        (("prompt_template",), "\ud800"),
    ],
)
def test_recipe_rejects_unsupported_data(recipe_data, path, value):
    target = recipe_data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        AnalysisRecipe.model_validate(recipe_data)


@pytest.fixture
def manifest(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    entries = []
    for name, content in [("z", "café\r\n"), ("a", "hello\n")]:
        body = content.encode()
        (docs / f"{name}.md").write_bytes(body)
        entries.append(
            dict(
                source_path=f"guide/{name}.md",
                output_filename=f"{name}.md",
                url=f"https://example.invalid/{name}",
                bucket="guide",
                bytes=len(body),
                source_id=f"id-{name}",
            )
        )
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            dict(
                schema_version=1,
                stage="normalized",
                source=dict(id="github-docs", type="git", ref="a" * 40),
                files=entries,
            )
        )
    )
    return path


@pytest.mark.parametrize("selection", [None, ("guide/z.md",), ("guide/z.md", "guide/a.md")])
def test_prepare_persists_inventory_and_recipe(
    manifest, recipe_data, tmp_path, monkeypatch, selection
):

    monkeypatch.setattr(metadata, "version", lambda name: "fixture-only-1")
    recipe_data["prompt_template"] += "\n café\r\n"
    recipe = AnalysisRecipe.model_validate(recipe_data)
    result = prepare_run(
        manifest, recipe=recipe, output_root=tmp_path / "runs", selected_source_paths=selection
    )
    assert result.is_absolute()
    assert sorted(p.name for p in result.iterdir()) == [
        "run_metadata.json",
        "source_inventory.json",
    ]
    expected = prepare_source_inventory(manifest, selected_source_paths=selection).model_dump()
    expected_bytes = (
        json.dumps(
            expected,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            separators=(",", ": "),
        )
        + "\n"
    ).encode()
    assert (result / "source_inventory.json").read_bytes() == expected_bytes
    saved = json.loads((result / "run_metadata.json").read_bytes())
    assert RunMetadata.model_validate(saved).model_dump() == saved
    assert saved["run_id"] == result.name
    assert saved["source_manifest_path"] == str(manifest.resolve())
    assert saved["source_inventory_sha256"] == hashlib.sha256(expected_bytes).hexdigest()
    assert (
        saved["analysis_spec"].pop("prompt_template_sha256")
        == hashlib.sha256(recipe_data["prompt_template"].encode()).hexdigest()
    )
    schema_bytes = json.dumps(
        recipe_data["response_schema"],
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode()
    assert (
        saved["analysis_spec"].pop("response_schema_sha256")
        == hashlib.sha256(schema_bytes).hexdigest()
    )
    assert {key: saved[key] for key in recipe_data} == recipe_data
    assert recipe.model_dump() == recipe_data


@pytest.fixture
def fixed_run(monkeypatch):
    clock = Mock(wraps=datetime)
    clock.now.return_value = datetime(2026, 11, 19, 14, 23, 45, 987654, tzinfo=UTC)
    identity = Mock(return_value=uuid.UUID("abcd0000-0000-0000-0000-000000000000"))
    monkeypatch.setattr(runs, "datetime", clock)
    monkeypatch.setattr(uuid, "uuid4", identity)
    monkeypatch.setattr(metadata, "version", lambda name: "fixture-only-1")
    return "20261119_142345_abcd", clock, identity


def test_one_identity_matches_utc_time(manifest, recipe_data, tmp_path, fixed_run, monkeypatch):
    name, clock, identity = fixed_run
    monkeypatch.chdir(tmp_path)
    result = prepare_run(
        Path("manifest.json"), recipe=AnalysisRecipe(**recipe_data), output_root=Path("runs")
    )
    saved = json.loads((result / "run_metadata.json").read_bytes())
    assert result == tmp_path / "runs" / name
    assert saved["created_at"] == "2026-11-19T14:23:45Z"
    assert saved["source_manifest_path"] == str(manifest)
    clock.now.assert_called_once_with(UTC)
    identity.assert_called_once_with()


@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "broken_symlink"])
def test_occupied_destination_is_preserved(manifest, recipe_data, tmp_path, fixed_run, kind):
    name, clock, identity = fixed_run
    root = tmp_path / "runs"
    root.mkdir()
    final = root / name
    if kind == "file":
        final.write_bytes(b"keep")
    elif kind == "directory":
        final.mkdir()
    else:
        final.symlink_to(manifest if kind == "symlink" else tmp_path / "missing")
    with pytest.raises(FileExistsError, match=name):
        prepare_run(manifest, recipe=AnalysisRecipe(**recipe_data), output_root=root)
    assert list(root.iterdir()) == [final]
    if kind == "file":
        assert final.read_bytes() == b"keep"
    if "symlink" in kind:
        assert final.is_symlink()
    clock.now.assert_called_once_with(UTC)
    identity.assert_called_once_with()


@pytest.mark.parametrize(
    "failure", ["write", "read", "corrupt", "promote", "recheck", "after_promote"]
)
def test_failed_preparation_preserves_evidence(
    manifest, recipe_data, tmp_path, fixed_run, monkeypatch, failure
):
    name, _, _ = fixed_run
    root = tmp_path / "runs"
    final = root / name
    original_write = Path.write_bytes
    original_read = Path.read_bytes
    original_rename = Path.rename

    def write(path, data):
        if path.name == "run_metadata.json":
            if failure == "write":
                original_write(path, data[:10])
                raise OSError("injected write failure")
            if failure == "corrupt":
                data = b"{}"
        return original_write(path, data)

    def read(path):
        if path.name == "run_metadata.json":
            if failure == "read":
                raise OSError("injected read failure")
            if failure == "recheck":
                final.symlink_to(tmp_path / "missing")
        return original_read(path)

    def rename(path, target):
        if failure == "promote":
            raise OSError("injected promotion failure")
        result = original_rename(path, target)
        if failure == "after_promote":
            raise KeyboardInterrupt("injected after promotion")
        return result

    monkeypatch.setattr(Path, "write_bytes", write)
    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(Path, "rename", rename)
    error = (
        KeyboardInterrupt
        if failure == "after_promote"
        else ValueError
        if failure == "corrupt"
        else OSError
    )
    with pytest.raises(error):
        prepare_run(manifest, recipe=AnalysisRecipe(**recipe_data), output_root=root)
    temporary = list(root.glob(".prepare-*"))
    if failure == "after_promote":
        assert not temporary
        assert sorted(p.name for p in final.iterdir()) == [
            "run_metadata.json",
            "source_inventory.json",
        ]
        RunMetadata.model_validate(json.loads(original_read(final / "run_metadata.json")))
    else:
        assert len(temporary) == 1
        assert (temporary[0] / "source_inventory.json").is_file()
        assert not final.exists()
        assert final.is_symlink() == (failure == "recheck")


@pytest.mark.parametrize("version", ["different", None])
def test_estimator_mismatch_creates_no_run(manifest, recipe_data, tmp_path, monkeypatch, version):
    def installed(name):
        assert name == "tiktoken"
        if version is None:
            raise metadata.PackageNotFoundError(name)
        return version

    monkeypatch.setattr(metadata, "version", installed)
    root = tmp_path / "runs"
    before = manifest.read_bytes()
    with pytest.raises(ValueError, match="estimator"):
        prepare_run(manifest, recipe=AnalysisRecipe(**recipe_data), output_root=root)
    assert not root.exists()
    assert manifest.read_bytes() == before


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("analysis_spec",),
        ("analysis_spec", "request_settings"),
        ("analysis_spec", "request_settings", "reasoning"),
        ("analysis_spec", "request_settings", "text"),
        ("analysis_spec", "request_settings", "text", "format"),
        ("execution_policy",),
        ("input_policy",),
        ("input_policy", "estimator"),
    ],
)
@pytest.mark.parametrize("change", ["missing", "extra"])
def test_known_objects_reject_missing_or_extra_fields(recipe_data, path, change):
    target = recipe_data
    for key in path:
        target = target[key]
    if change == "missing":
        del target[next(iter(target))]
    else:
        target["unexpected"] = "SECRET_SENTINEL"
    with pytest.raises(ValueError) as error:
        AnalysisRecipe.model_validate(recipe_data)
    assert "SECRET_SENTINEL" not in str(error.value)


def test_prepare_revalidates_mutated_recipe(manifest, recipe_data, tmp_path, fixed_run):
    recipe = AnalysisRecipe(**recipe_data)
    recipe.execution_policy["concurrency"] = True
    with pytest.raises(ValueError, match="concurrency"):
        prepare_run(manifest, recipe=recipe, output_root=tmp_path / "runs")
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("created_at", "2026-11-19T14:23:46Z"),
        ("created_at", "2026-99-19T14:23:45Z"),
        ("run_id", "bad"),
        ("source_inventory_sha256", "A" * 64),
        ("source_manifest_path", "relative"),
        ("schema_version", "2.0"),
        ("completed", True),
    ],
)
def test_saved_boundary_rejects_invalid_artifact(
    manifest, recipe_data, tmp_path, fixed_run, field, value
):
    result = prepare_run(
        manifest, recipe=AnalysisRecipe(**recipe_data), output_root=tmp_path / "runs"
    )
    saved = json.loads((result / "run_metadata.json").read_bytes())
    saved[field] = value
    with pytest.raises(ValueError):
        RunMetadata.model_validate(saved)


def test_hash_rules_preserve_strings_and_schema_order(manifest, recipe_data, tmp_path, fixed_run):
    hashes = []
    for index in range(5):
        if index == 1:
            recipe_data["response_schema"] = dict(
                reversed(list(recipe_data["response_schema"].items()))
            )
        elif index == 2:
            recipe_data["response_schema"]["properties"]["note"]["description"] += " café\n"
            recipe_data["response_schema"]["properties"]["note"]["enum"] = ["a", "b"]
        elif index == 3:
            recipe_data["response_schema"]["properties"]["note"]["enum"].reverse()
        elif index == 4:
            recipe_data["prompt_template"] += "\n"
        result = prepare_run(
            manifest, recipe=AnalysisRecipe(**recipe_data), output_root=tmp_path / str(index)
        )
        hashes.append(json.loads((result / "run_metadata.json").read_bytes())["analysis_spec"])
    assert hashes[0] == hashes[1]
    assert hashes[1]["response_schema_sha256"] != hashes[2]["response_schema_sha256"]
    assert hashes[2]["response_schema_sha256"] != hashes[3]["response_schema_sha256"]
    assert hashes[3]["response_schema_sha256"] == hashes[4]["response_schema_sha256"]
    assert hashes[3]["prompt_template_sha256"] != hashes[4]["prompt_template_sha256"]


def test_fresh_import_and_prepare_are_offline(manifest, recipe_data, tmp_path, monkeypatch):
    original_import = builtins.__import__
    forbidden = (
        "openai",
        "dotenv",
        "tiktoken",
        "data_curation.legacy",
        "data_curation.stage1_analyze_content_quality",
    )

    def guarded_import(name, *args, **kwargs):
        assert not any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
        return original_import(name, *args, **kwargs)

    def no_network(*args, **kwargs):
        raise AssertionError("network access forbidden")

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(metadata, "version", lambda name: "fixture-only-1")
    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    names = ("runs", "sources", "schemas")
    original_modules = {name: sys.modules["data_curation.pipeline_v2." + name] for name in names}
    original_attributes = {name: getattr(pipeline_v2, name) for name in names}
    original_recipe_class = original_modules["schemas"].AnalysisRecipe
    with monkeypatch.context() as isolated:
        for name in names:
            isolated.delattr(pipeline_v2, name)
            isolated.delitem(sys.modules, "data_curation.pipeline_v2." + name)
        fresh = importlib.import_module("data_curation.pipeline_v2.runs")
        recipe = fresh.AnalysisRecipe(**recipe_data)
        assert fresh.prepare_run(manifest, recipe=recipe, output_root=tmp_path / "offline").is_dir()
    for name in names:
        assert sys.modules["data_curation.pipeline_v2." + name] is original_modules[name]
        assert getattr(pipeline_v2, name) is original_attributes[name]
    assert (
        importlib.import_module("data_curation.pipeline_v2.schemas").AnalysisRecipe
        is original_recipe_class
    )
    assert pipeline_v2.schemas.AnalysisRecipe is original_recipe_class


def test_cyclic_schema_is_rejected_as_invalid_json(recipe_data):
    recipe_data["response_schema"]["cycle"] = recipe_data["response_schema"]
    with pytest.raises(ValueError, match="JSON"):
        AnalysisRecipe(**recipe_data)


def test_mutated_model_errors_do_not_disclose_input(
    manifest, recipe_data, tmp_path, fixed_run, recwarn
):
    recipe = AnalysisRecipe(**recipe_data)
    recipe.prompt_template = ["SECRET_SENTINEL"]
    with pytest.raises(ValueError) as error:
        prepare_run(manifest, recipe=recipe, output_root=tmp_path / "runs")
    assert "SECRET_SENTINEL" not in str(error.value)
    assert not recwarn.list


@pytest.mark.parametrize("failed_path", ["manifest", "output_root"])
def test_resolution_symlink_loops_raise_filesystem_error(
    manifest, recipe_data, tmp_path, fixed_run, monkeypatch, failed_path
):
    output_root = tmp_path / "runs"
    target = manifest if failed_path == "manifest" else output_root
    original_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == target:
            raise RuntimeError("Symlink loop from supported Python 3.11/3.12")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(OSError) as error:
        prepare_run(manifest, recipe=AnalysisRecipe(**recipe_data), output_root=output_root)
    assert str(target) in str(error.value)
    assert not output_root.exists()
