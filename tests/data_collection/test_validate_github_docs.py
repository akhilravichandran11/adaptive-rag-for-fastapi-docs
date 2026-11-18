"""Verify GitHub-docs validation and publication through the agreed seams."""

import hashlib
import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from data_collection.data_normalization import validate_github_docs as validator  # noqa: E402


@pytest.mark.parametrize("omitted", ["all", "raw-dir", "normalized-dir", "output-dir"])
def test_cli_requires_all_three_directories(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], omitted: str
) -> None:
    argv = []
    for option in ("raw-dir", "normalized-dir", "output-dir"):
        if omitted not in ("all", option):
            argv.extend([f"--{option}", str(tmp_path / option)])

    with pytest.raises(SystemExit) as error:
        validator.main(argv)

    assert error.value.code == 2
    assert "required" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("input_name", ["raw", "normalized"])
@pytest.mark.parametrize("relationship", ["same", "child", "parent", "symlink"])
def test_cli_rejects_output_input_overlap_without_writing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    input_name: str,
    relationship: str,
) -> None:
    raw = tmp_path / "raw"
    normalized = tmp_path / "normalized"
    raw.mkdir()
    normalized.mkdir()
    input_dir = tmp_path / input_name
    if relationship == "same":
        output = input_dir
    elif relationship == "child":
        output = input_dir / "runs"
    elif relationship == "parent":
        output = tmp_path
    else:
        output = tmp_path / "alias"
        output.symlink_to(input_dir, target_is_directory=True)
    before = sorted(tmp_path.rglob("*"))

    with pytest.raises(SystemExit) as error:
        validator.main(
            [
                "--raw-dir",
                str(raw),
                "--normalized-dir",
                str(normalized),
                "--output-dir",
                str(output),
            ]
        )

    assert error.value.code == 2
    assert "overlap" in capsys.readouterr().err
    assert sorted(tmp_path.rglob("*")) == before


@pytest.mark.parametrize("raw_bytes", [None, b"{", b"\xff"])
def test_unreadable_membership_returns_blocked_report_without_writing(
    tmp_path: Path, raw_bytes: bytes | None
) -> None:
    raw = tmp_path / "raw"
    normalized = tmp_path / "normalized"
    raw.mkdir()
    normalized.mkdir()
    manifests = [raw / "manifest.json", raw / "code_manifest.json", normalized / "manifest.json"]
    if raw_bytes is not None:
        manifests[0].write_bytes(raw_bytes)
    for path in manifests[1:]:
        path.write_bytes(b"{}")
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    report = validator.validate_snapshot(raw, normalized)

    assert set(report) == {
        "schema_version",
        "validator_version",
        "run_id",
        "started_at",
        "finished_at",
        "source",
        "inputs",
        "validation_status",
        "publication_status",
        "accepted_path",
        "summary",
        "global_diagnostics",
        "files",
        "extras",
    }
    assert report["schema_version"] == report["validator_version"] == 1
    assert report["validation_status"] == "blocked"
    assert report["publication_status"] == "not_published"
    assert report["accepted_path"] is None
    assert report["source"] is None
    assert report["files"] == []
    assert report["extras"] == []
    assert report["summary"] == {
        "candidates": None,
        "passed": 0,
        "failed": 0,
        "needs_review": 0,
        "not_checked": None,
    }
    assert report["run_id"] and Path(report["run_id"]).name == report["run_id"]
    started = datetime.fromisoformat(report["started_at"])
    finished = datetime.fromisoformat(report["finished_at"])
    assert started.utcoffset().total_seconds() == 0
    assert finished.utcoffset().total_seconds() == 0
    assert started <= finished
    assert report["inputs"]["raw_dir"] == str(raw.resolve())
    assert report["inputs"]["normalized_dir"] == str(normalized.resolve())
    assert report["inputs"]["manifest_sha256"] == {
        str(path.resolve()): hashlib.sha256(content).hexdigest() for path, content in before.items()
    }
    diagnostics = report["global_diagnostics"]
    assert any(
        item["code"] == "input_manifest_invalid" and item["path"] == str(manifests[0])
        for item in diagnostics
    )
    for item in diagnostics:
        assert set(item) == {"code", "message", "path", "line", "reference"}
        assert item["message"]
        assert item["line"] is None
        assert item["reference"] is None
    json.dumps(report)
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize(
    "raw_manifest",
    [
        None,
        [],
        {},
        {"schema_version": True, "files": []},
        {"schema_version": 2, "files": []},
        {"schema_version": 1, "files": None},
        {"schema_version": 1, "files": {}},
        {"schema_version": 1, "files": [None]},
        {"schema_version": 1, "files": [{"source_path": "guide.md"}]},
        {"schema_version": 1, "files": [{"source_path": 7, "output_filename": "guide.md"}]},
        {"schema_version": 1, "files": [{"source_path": "guide.md", "output_filename": ""}]},
    ],
)
def test_invalid_membership_shape_keeps_candidate_counts_unknown(
    tmp_path: Path, raw_manifest: object
) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "manifest.json").write_text(json.dumps(raw_manifest), encoding="utf-8")

    report = validator.validate_snapshot(raw, tmp_path / "normalized")

    assert report["validation_status"] == "blocked"
    assert report["publication_status"] == "not_published"
    assert report["summary"] == {
        "candidates": None,
        "passed": 0,
        "failed": 0,
        "needs_review": 0,
        "not_checked": None,
    }
    assert report["files"] == []
    assert any(
        item["code"] == "input_manifest_invalid" and item["path"] == str(raw / "manifest.json")
        for item in report["global_diagnostics"]
    )


@pytest.mark.parametrize("members", [[], ["guide.md", "topic.md"]])
def test_global_block_accounts_for_manifest_members_only(
    tmp_path: Path, members: list[str]
) -> None:
    raw = tmp_path / "raw"
    normalized = tmp_path / "normalized"
    (raw / "docs").mkdir(parents=True)
    (normalized / "docs").mkdir(parents=True)
    (raw / "docs" / "unlisted.md").write_text("Not a candidate.\n", encoding="utf-8")
    (normalized / "docs" / "unlisted.md").write_text("Not a candidate.\n", encoding="utf-8")
    source = {
        "id": "github-docs",
        "type": "git",
        "version": "v1",
        "repo": "example/docs",
        "repo_url": "https://github.com/example/docs",
        "ref": "a" * 40,
        "docs_path": "docs",
        "ref_url": "https://github.com/example/docs/tree/" + "a" * 40 + "/docs",
    }
    entries = [
        {
            "source_path": name,
            "output_filename": name,
            "bucket": "top-level",
            "url": "https://github.com/example/docs/blob/" + "a" * 40 + "/docs/" + name,
            "bytes": 0,
        }
        for name in members
    ]
    manifest = {
        "schema_version": 1,
        "source": source,
        "collection": {
            "fetched_at": "2026-10-01T00:00:00+00:00",
            "tool": "gitingest",
            "ingest_file": "ingest/full_ingest.txt",
            "docs_dir": "docs",
            "min_file_bytes": 0,
            "included_subdirs": [],
            "top_level_allowlist": members,
            "include_patterns": [],
        },
        "stats": {
            "total_files": len(members),
            "total_bytes": 0,
            "by_bucket": {"top-level": {"files": len(members), "bytes": 0}} if members else {},
        },
        "files": entries,
    }
    (raw / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    report = validator.validate_snapshot(raw, normalized)

    assert report["validation_status"] == "blocked"
    assert report["publication_status"] == "not_published"
    assert report["accepted_path"] is None
    assert report["summary"] == {
        "candidates": len(members),
        "passed": 0,
        "failed": 0,
        "needs_review": 0,
        "not_checked": len(members),
    }
    assert report["files"] == [
        {
            "source_path": name,
            "output_filename": name,
            "status": "not_checked",
            "sha256": None,
            "diagnostics": [],
        }
        for name in members
    ]
    assert {
        item["path"]
        for item in report["global_diagnostics"]
        if item["code"] == "input_manifest_invalid"
    } >= {str(raw / "code_manifest.json"), str(normalized / "manifest.json")}


def write_snapshot(tmp_path: Path) -> tuple[Path, Path, dict]:
    """Write a small offline snapshot using the current version-1 manifest shapes."""
    raw = tmp_path / "raw"
    normalized = tmp_path / "normalized"
    (raw / "docs").mkdir(parents=True)
    (raw / "code" / "docs_src").mkdir(parents=True)
    (normalized / "docs").mkdir(parents=True)
    source = {
        "id": "github-docs",
        "type": "git",
        "version": "v1",
        "repo": "example/docs",
        "repo_url": "https://github.com/example/docs",
        "ref": "a" * 40,
    }
    raw_text = b"{!../docs_src/example.py!}\n"
    normalized_text = b"Example.\n"
    code_text = b"answer = 42\n"
    raw_entries = []
    normalized_entries = []
    for name in ("guide.md", "topic.md"):
        (raw / "docs" / name).write_bytes(raw_text)
        (normalized / "docs" / name).write_bytes(normalized_text)
        entry = {
            "source_path": name,
            "output_filename": name,
            "bucket": "top-level",
            "url": f"{source['repo_url']}/blob/{source['ref']}/docs/{name}",
            "bytes": len(raw_text),
        }
        raw_entries.append(entry)
        normalized_entries.append(
            {
                **entry,
                "bytes": len(normalized_text),
                "includes": [{"repo_path": "docs_src/example.py", "options": "", "resolved": True}],
            }
        )
    (raw / "code" / "docs_src" / "example.py").write_bytes(code_text)
    raw_manifest = {
        "schema_version": 1,
        "source": {
            **source,
            "docs_path": "docs",
            "ref_url": f"{source['repo_url']}/tree/{source['ref']}/docs",
        },
        "collection": {
            "fetched_at": "2026-10-01T00:00:00+00:00",
            "tool": "gitingest",
            "ingest_file": "ingest/full_ingest.txt",
            "docs_dir": "docs",
            "min_file_bytes": 0,
            "included_subdirs": [],
            "top_level_allowlist": ["guide.md", "topic.md"],
            "include_patterns": [],
        },
        "stats": {
            "total_files": 2,
            "total_bytes": 2 * len(raw_text),
            "by_bucket": {"top-level": {"files": 2, "bytes": 2 * len(raw_text)}},
        },
        "files": raw_entries,
    }
    code_manifest = {
        "schema_version": 1,
        "kind": "referenced-code",
        "source": {key: value for key, value in source.items() if key != "type"},
        "referenced_from": "manifest.json",
        "collection": {
            "fetched_at": "2026-10-01T00:00:00+00:00",
            "method": "raw.githubusercontent",
            "code_dir": "code",
        },
        "stats": {
            "total_files": 1,
            "total_bytes": len(code_text),
            "failures": 0,
            "by_ext": {".py": 1},
        },
        "files": [
            {
                "repo_path": "docs_src/example.py",
                "output_path": "code/docs_src/example.py",
                "url": f"https://raw.githubusercontent.com/{source['repo']}/{source['ref']}/docs_src/example.py",
                "ext": ".py",
                "bytes": len(code_text),
                "referenced_by": [
                    {"doc": name, "options": ""} for name in ("guide.md", "topic.md")
                ],
            }
        ],
        "failures": [],
    }
    normalized_manifest = {
        "schema_version": 1,
        "stage": "normalized",
        "source": source,
        "derived_from": {"docs_manifest": "manifest.json", "code_manifest": "code_manifest.json"},
        "normalization": {
            "generated_at": "2026-10-01T00:00:00+00:00",
            "inlined_includes": 2,
            "unresolved_includes": 0,
        },
        "stats": {
            "total_files": 2,
            "total_bytes": 2 * len(normalized_text),
            "by_bucket": {"top-level": {"files": 2, "bytes": 2 * len(normalized_text)}},
        },
        "files": normalized_entries,
    }
    manifests = {
        raw / "manifest.json": raw_manifest,
        raw / "code_manifest.json": code_manifest,
        normalized / "manifest.json": normalized_manifest,
    }
    for path, manifest in manifests.items():
        path.write_text(json.dumps(manifest), encoding="utf-8")
    return raw, normalized, manifests


@pytest.mark.parametrize(
    ("manifest_name", "field"),
    [
        ("raw", "source_path"),
        ("raw", "output_filename"),
        ("normalized", "source_path"),
        ("normalized", "output_filename"),
        ("code", "repo_path"),
    ],
)
def test_duplicate_manifest_identities_block_all_candidates(
    tmp_path: Path, manifest_name: str, field: str
) -> None:
    raw, normalized, manifests = write_snapshot(tmp_path)
    path = {
        "raw": raw / "manifest.json",
        "normalized": normalized / "manifest.json",
        "code": raw / "code_manifest.json",
    }[manifest_name]
    manifest = manifests[path]
    if manifest_name == "code":
        manifest["files"].append(deepcopy(manifest["files"][0]))
    else:
        manifest["files"][1][field] = manifest["files"][0][field]
    path.write_text(json.dumps(manifest), encoding="utf-8")

    report = validator.validate_snapshot(raw, normalized)

    assert report["validation_status"] == "blocked"
    assert report["publication_status"] == "not_published"
    assert report["summary"] == {
        "candidates": 2,
        "passed": 0,
        "failed": 0,
        "needs_review": 0,
        "not_checked": 2,
    }
    assert len(report["files"]) == 2
    assert all(entry["status"] == "not_checked" for entry in report["files"])
    assert any(
        item["code"] == "duplicate_identity"
        and item["path"] == str(path)
        and field in item["message"]
        and item["reference"] == manifest["files"][0][field]
        for item in report["global_diagnostics"]
    )


@pytest.mark.parametrize("manifest_name", ["normalized", "code"])
@pytest.mark.parametrize(
    "defect", ["null", "list", "version", "boolean_version", "files_mapping", "record", "identity"]
)
def test_invalid_supporting_manifest_structure_blocks_known_candidates(
    tmp_path: Path, manifest_name: str, defect: str
) -> None:
    raw, normalized, manifests = write_snapshot(tmp_path)
    path = (
        normalized / "manifest.json"
        if manifest_name == "normalized"
        else raw / "code_manifest.json"
    )
    manifest = manifests[path]
    if defect == "null":
        manifest = None
    elif defect == "list":
        manifest = []
    elif defect == "version":
        manifest["schema_version"] = 2
    elif defect == "boolean_version":
        manifest["schema_version"] = True
    elif defect == "files_mapping":
        manifest["files"] = {}
    elif defect == "record":
        manifest["files"][0] = "not a record"
    else:
        field = "source_path" if manifest_name == "normalized" else "repo_path"
        manifest["files"][0][field] = None
    path.write_text(json.dumps(manifest), encoding="utf-8")

    report = validator.validate_snapshot(raw, normalized)

    assert report["validation_status"] == "blocked"
    assert report["publication_status"] == "not_published"
    assert report["summary"]["candidates"] == report["summary"]["not_checked"] == 2
    assert all(entry["status"] == "not_checked" for entry in report["files"])
    assert any(
        item["code"] == "input_manifest_invalid" and item["path"] == str(path)
        for item in report["global_diagnostics"]
    )


@pytest.fixture
def type_check_snapshot(tmp_path: Path) -> tuple[Path, Path, dict]:
    """Keep an independent identity block while exercising other manifest fields."""
    raw, normalized, manifests = write_snapshot(tmp_path)
    manifests[normalized / "manifest.json"]["files"][1]["source_path"] = "guide.md"
    code = manifests[raw / "code_manifest.json"]
    code["failures"] = [
        {
            "repo_path": "docs_src/unavailable.py",
            "url": "https://raw.githubusercontent.com/example/docs/"
            + "a" * 40
            + "/docs_src/unavailable.py",
            "error": "",
        }
    ]
    code["stats"]["failures"] = 1
    for path, manifest in manifests.items():
        path.write_text(json.dumps(manifest), encoding="utf-8")
    return raw, normalized, manifests


@pytest.mark.parametrize("mutation", ["missing", "null"])
@pytest.mark.parametrize(
    ("kind", "field"),
    [
        *[
            (kind, field)
            for kind in ("raw", "normalized", "code")
            for field in (
                "source",
                "source/id",
                "source/version",
                "source/repo",
                "source/repo_url",
                "source/ref",
                "stats",
                "stats/total_files",
                "stats/total_bytes",
                "files/0/bytes",
                "files/0/url",
            )
        ],
        *[
            (kind, field)
            for kind in ("raw", "normalized")
            for field in (
                "source/type",
                "files/0/bucket",
                "stats/by_bucket",
                "stats/by_bucket/top-level/files",
                "stats/by_bucket/top-level/bytes",
            )
        ],
        *[
            ("raw", field)
            for field in (
                "source/docs_path",
                "source/ref_url",
                "collection",
                "collection/fetched_at",
                "collection/tool",
                "collection/ingest_file",
                "collection/docs_dir",
                "collection/min_file_bytes",
                "collection/included_subdirs",
                "collection/top_level_allowlist",
                "collection/include_patterns",
            )
        ],
        *[
            ("normalized", field)
            for field in (
                "stage",
                "derived_from",
                "derived_from/docs_manifest",
                "derived_from/code_manifest",
                "normalization",
                "normalization/generated_at",
                "normalization/inlined_includes",
                "normalization/unresolved_includes",
                "files/0/includes",
                "files/0/includes/0/repo_path",
                "files/0/includes/0/options",
                "files/0/includes/0/resolved",
            )
        ],
        *[
            ("code", field)
            for field in (
                "kind",
                "referenced_from",
                "collection",
                "collection/fetched_at",
                "collection/method",
                "collection/code_dir",
                "stats/failures",
                "stats/by_ext",
                "files/0/output_path",
                "files/0/ext",
                "files/0/referenced_by",
                "files/0/referenced_by/0/doc",
                "files/0/referenced_by/0/options",
                "failures",
                "failures/0/repo_path",
                "failures/0/url",
                "failures/0/error",
            )
        ],
    ],
)
def test_required_manifest_fields_are_checked_even_with_an_identity_block(
    type_check_snapshot: tuple[Path, Path, dict], kind: str, field: str, mutation: str
) -> None:
    raw, normalized, manifests = type_check_snapshot
    path = {
        "raw": raw / "manifest.json",
        "normalized": normalized / "manifest.json",
        "code": raw / "code_manifest.json",
    }[kind]
    record = manifests[path]
    keys = [int(part) if part.isdigit() else part for part in field.split("/")]
    for key in keys[:-1]:
        record = record[key]
    if mutation == "missing":
        del record[keys[-1]]
    else:
        record[keys[-1]] = None
    path.write_text(json.dumps(manifests[path]), encoding="utf-8")

    report = validator.validate_snapshot(raw, normalized)

    assert report["validation_status"] == "blocked"
    assert report["publication_status"] == "not_published"
    assert report["summary"]["not_checked"] == report["summary"]["candidates"] == 2
    assert any(
        item["code"] == "input_manifest_invalid" and item["path"] == str(path)
        for item in report["global_diagnostics"]
    )


@pytest.mark.parametrize("value", [True, -1, 1.5, "1"])
@pytest.mark.parametrize(
    ("kind", "field"),
    [
        *[
            (kind, field)
            for kind in ("raw", "normalized", "code")
            for field in ("files/0/bytes", "stats/total_files", "stats/total_bytes")
        ],
        *[
            (kind, field)
            for kind in ("raw", "normalized")
            for field in ("stats/by_bucket/top-level/files", "stats/by_bucket/top-level/bytes")
        ],
        ("raw", "collection/min_file_bytes"),
        ("normalized", "normalization/inlined_includes"),
        ("normalized", "normalization/unresolved_includes"),
        ("code", "stats/failures"),
        ("code", "stats/by_ext/.py"),
    ],
)
def test_manifest_counts_require_nonnegative_integers_not_booleans(
    type_check_snapshot: tuple[Path, Path, dict], kind: str, field: str, value: object
) -> None:
    raw, normalized, manifests = type_check_snapshot
    path = {
        "raw": raw / "manifest.json",
        "normalized": normalized / "manifest.json",
        "code": raw / "code_manifest.json",
    }[kind]
    record = manifests[path]
    keys = [int(part) if part.isdigit() else part for part in field.split("/")]
    for key in keys[:-1]:
        record = record[key]
    record[keys[-1]] = value
    path.write_text(json.dumps(manifests[path]), encoding="utf-8")

    report = validator.validate_snapshot(raw, normalized)

    assert report["validation_status"] == "blocked"
    assert any(
        item["code"] == "input_manifest_invalid" and item["path"] == str(path)
        for item in report["global_diagnostics"]
    )


@pytest.mark.parametrize("value", [0, 1, "true"])
def test_include_resolution_requires_a_boolean(
    type_check_snapshot: tuple[Path, Path, dict], value: object
) -> None:
    raw, normalized, manifests = type_check_snapshot
    path = normalized / "manifest.json"
    manifests[path]["files"][0]["includes"][0]["resolved"] = value
    path.write_text(json.dumps(manifests[path]), encoding="utf-8")

    report = validator.validate_snapshot(raw, normalized)

    assert any(
        item["code"] == "input_manifest_invalid" and item["path"] == str(path)
        for item in report["global_diagnostics"]
    )


def test_well_typed_manifests_allow_empty_options_and_fetch_error_text(
    type_check_snapshot: tuple[Path, Path, dict],
) -> None:
    raw, normalized, _ = type_check_snapshot

    report = validator.validate_snapshot(raw, normalized)

    assert report["validation_status"] == "blocked"
    assert any(item["code"] == "duplicate_identity" for item in report["global_diagnostics"])
    assert not any(
        item["code"] == "input_manifest_invalid" for item in report["global_diagnostics"]
    )


@pytest.mark.parametrize("value", [None, "bad", []])
@pytest.mark.parametrize(
    ("kind", "field"),
    [
        ("raw", "stats/by_bucket/top-level"),
        ("normalized", "stats/by_bucket/top-level"),
        ("normalized", "files/0/includes/0"),
        ("code", "files/0/referenced_by/0"),
        ("code", "failures/0"),
    ],
)
def test_nested_manifest_records_require_objects(
    type_check_snapshot: tuple[Path, Path, dict], kind: str, field: str, value: object
) -> None:
    raw, normalized, manifests = type_check_snapshot
    path = {
        "raw": raw / "manifest.json",
        "normalized": normalized / "manifest.json",
        "code": raw / "code_manifest.json",
    }[kind]
    record = manifests[path]
    keys = [int(part) if part.isdigit() else part for part in field.split("/")]
    for key in keys[:-1]:
        record = record[key]
    record[keys[-1]] = value
    path.write_text(json.dumps(manifests[path]), encoding="utf-8")

    report = validator.validate_snapshot(raw, normalized)

    assert any(
        item["code"] == "input_manifest_invalid" and item["path"] == str(path)
        for item in report["global_diagnostics"]
    )


@pytest.fixture
def consistency_snapshot(tmp_path: Path) -> tuple[Path, Path, dict]:
    """Keep a code identity block with correct totals while checking consistency."""
    raw, normalized, manifests = write_snapshot(tmp_path)
    code = manifests[raw / "code_manifest.json"]
    code["files"].append(deepcopy(code["files"][0]))
    code["stats"]["total_files"] = 2
    code["stats"]["total_bytes"] *= 2
    code["stats"]["by_ext"][".py"] = 2
    (raw / "code_manifest.json").write_text(json.dumps(code), encoding="utf-8")
    return raw, normalized, manifests


@pytest.mark.parametrize("kind", ["raw", "code", "normalized"])
@pytest.mark.parametrize("field", ["id", "version", "repo", "repo_url", "ref"])
def test_conflicting_source_metadata_blocks_the_snapshot(
    consistency_snapshot: tuple[Path, Path, dict], kind: str, field: str
) -> None:
    raw, normalized, manifests = consistency_snapshot
    path = {
        "raw": raw / "manifest.json",
        "normalized": normalized / "manifest.json",
        "code": raw / "code_manifest.json",
    }[kind]
    manifests[path]["source"][field] = "different-source-value"
    path.write_text(json.dumps(manifests[path]), encoding="utf-8")

    report = validator.validate_snapshot(raw, normalized)

    assert report["validation_status"] == "blocked"
    assert report["publication_status"] == "not_published"
    assert report["source"] is None
    assert report["summary"]["candidates"] == report["summary"]["not_checked"] == 2
    assert any(
        item["code"] == "source_mismatch" and field in item["message"]
        for item in report["global_diagnostics"]
    )


@pytest.mark.parametrize("field", ["source_path", "output_filename", "bucket", "url"])
def test_normalized_document_identity_and_provenance_must_match_raw(
    consistency_snapshot: tuple[Path, Path, dict], field: str
) -> None:
    raw, normalized, manifests = consistency_snapshot
    path = normalized / "manifest.json"
    manifests[path]["files"][0][field] = "different.md"
    path.write_text(json.dumps(manifests[path]), encoding="utf-8")

    report = validator.validate_snapshot(raw, normalized)

    assert report["validation_status"] == "blocked"
    assert report["source"] is None
    assert any(
        item["code"] == "source_mismatch" and item["path"] == str(path) and field in item["message"]
        for item in report["global_diagnostics"]
    )


@pytest.mark.parametrize("kind", ["raw", "normalized"])
def test_document_source_types_must_agree(
    consistency_snapshot: tuple[Path, Path, dict], kind: str
) -> None:
    raw, normalized, manifests = consistency_snapshot
    path = raw / "manifest.json" if kind == "raw" else normalized / "manifest.json"
    manifests[path]["source"]["type"] = "different-source-type"
    path.write_text(json.dumps(manifests[path]), encoding="utf-8")

    report = validator.validate_snapshot(raw, normalized)

    assert report["source"] is None
    assert any(
        item["code"] == "source_mismatch" and "type" in item["message"]
        for item in report["global_diagnostics"]
    )


@pytest.mark.parametrize(
    ("kind", "field"),
    [
        *[
            (kind, field)
            for kind in ("raw", "normalized", "code")
            for field in ("stats/total_files", "stats/total_bytes")
        ],
        *[
            (kind, field)
            for kind in ("raw", "normalized")
            for field in ("stats/by_bucket/top-level/files", "stats/by_bucket/top-level/bytes")
        ],
        ("normalized", "normalization/inlined_includes"),
        ("normalized", "normalization/unresolved_includes"),
        ("code", "stats/failures"),
        ("code", "stats/by_ext/.py"),
    ],
)
def test_aggregate_counts_must_match_the_manifest_records(
    consistency_snapshot: tuple[Path, Path, dict], kind: str, field: str
) -> None:
    raw, normalized, manifests = consistency_snapshot
    path = {
        "raw": raw / "manifest.json",
        "normalized": normalized / "manifest.json",
        "code": raw / "code_manifest.json",
    }[kind]
    record = manifests[path]
    keys = field.split("/")
    for key in keys[:-1]:
        record = record[key]
    record[keys[-1]] += 1
    path.write_text(json.dumps(manifests[path]), encoding="utf-8")

    report = validator.validate_snapshot(raw, normalized)

    assert report["validation_status"] == "blocked"
    assert report["publication_status"] == "not_published"
    assert report["summary"]["not_checked"] == 2
    assert any(
        item["code"] == "manifest_totals_mismatch" and item["path"] == str(path)
        for item in report["global_diagnostics"]
    )


@pytest.mark.parametrize("kind", ["raw", "normalized", "code"])
@pytest.mark.parametrize("change", ["missing_group", "extra_group"])
def test_aggregate_groups_must_match_the_manifest_records(
    consistency_snapshot: tuple[Path, Path, dict], kind: str, change: str
) -> None:
    raw, normalized, manifests = consistency_snapshot
    path = {
        "raw": raw / "manifest.json",
        "normalized": normalized / "manifest.json",
        "code": raw / "code_manifest.json",
    }[kind]
    groups = manifests[path]["stats"]["by_ext" if kind == "code" else "by_bucket"]
    if change == "missing_group":
        groups.clear()
    else:
        groups["unlisted"] = 0 if kind == "code" else {"files": 0, "bytes": 0}
    path.write_text(json.dumps(manifests[path]), encoding="utf-8")

    report = validator.validate_snapshot(raw, normalized)

    assert any(
        item["code"] == "manifest_totals_mismatch" and item["path"] == str(path)
        for item in report["global_diagnostics"]
    )


@pytest.mark.parametrize("change", ["unchanged", "missing_document", "extra_document"])
def test_consistent_subset_or_extra_does_not_invent_provenance_or_totals_errors(
    consistency_snapshot: tuple[Path, Path, dict], change: str
) -> None:
    raw, normalized, manifests = consistency_snapshot
    path = normalized / "manifest.json"
    manifest = manifests[path]
    if change == "missing_document":
        manifest["files"].pop()
        manifest["stats"] = {
            "total_files": 1,
            "total_bytes": 9,
            "by_bucket": {"top-level": {"files": 1, "bytes": 9}},
        }
        manifest["normalization"]["inlined_includes"] = 1
    elif change == "extra_document":
        extra = deepcopy(manifest["files"][0])
        extra.update(
            source_path="extra.md", output_filename="extra.md", url="https://example.invalid/extra"
        )
        manifest["files"].append(extra)
        manifest["stats"] = {
            "total_files": 3,
            "total_bytes": 27,
            "by_bucket": {"top-level": {"files": 3, "bytes": 27}},
        }
        manifest["normalization"]["inlined_includes"] = 3
    path.write_text(json.dumps(manifest), encoding="utf-8")

    report = validator.validate_snapshot(raw, normalized)

    assert report["source"] == manifests[raw / "manifest.json"]["source"]
    assert report["summary"]["candidates"] == report["summary"]["not_checked"] == 2
    assert not any(
        item["code"] in {"source_mismatch", "manifest_totals_mismatch", "input_manifest_invalid"}
        for item in report["global_diagnostics"]
    )


@pytest.mark.parametrize("field", ["docs_manifest", "code_manifest", "referenced_from"])
def test_manifests_name_the_inputs_the_validator_actually_reads(
    consistency_snapshot: tuple[Path, Path, dict], field: str
) -> None:
    raw, normalized, manifests = consistency_snapshot
    if field == "referenced_from":
        path = raw / "code_manifest.json"
        manifests[path][field] = "another-manifest.json"
    else:
        path = normalized / "manifest.json"
        manifests[path]["derived_from"][field] = "another-manifest.json"
    path.write_text(json.dumps(manifests[path]), encoding="utf-8")

    report = validator.validate_snapshot(raw, normalized)

    assert report["source"] is None
    assert any(
        item["code"] == "source_mismatch" and item["path"] == str(path) and field in item["message"]
        for item in report["global_diagnostics"]
    )


@pytest.fixture
def path_check_snapshot(consistency_snapshot: tuple[Path, Path, dict]) -> tuple[Path, Path, dict]:
    raw, normalized, manifests = consistency_snapshot
    code = manifests[raw / "code_manifest.json"]
    code["failures"] = [
        {
            "repo_path": "docs_src/unavailable.py",
            "url": "https://raw.githubusercontent.com/example/docs/"
            + "a" * 40
            + "/docs_src/unavailable.py",
            "error": "unavailable",
        }
    ]
    code["stats"]["failures"] = 1
    (raw / "code_manifest.json").write_text(json.dumps(code), encoding="utf-8")
    return raw, normalized, manifests


@pytest.mark.parametrize("value", ["../escape.md", "/outside.md"])
@pytest.mark.parametrize(
    ("kind", "field"),
    [
        ("raw", "collection/docs_dir"),
        ("raw", "collection/ingest_file"),
        ("raw", "source/docs_path"),
        ("raw", "files/0/source_path"),
        ("raw", "files/0/output_filename"),
        ("normalized", "files/0/source_path"),
        ("normalized", "files/0/output_filename"),
        ("normalized", "files/0/includes/0/repo_path"),
        ("normalized", "derived_from/docs_manifest"),
        ("normalized", "derived_from/code_manifest"),
        ("code", "collection/code_dir"),
        ("code", "referenced_from"),
        ("code", "files/0/repo_path"),
        ("code", "files/0/output_path"),
        ("code", "files/0/referenced_by/0/doc"),
        ("code", "failures/0/repo_path"),
    ],
)
def test_declared_paths_cannot_be_absolute_or_traverse_parents(
    path_check_snapshot: tuple[Path, Path, dict], kind: str, field: str, value: str
) -> None:
    raw, normalized, manifests = path_check_snapshot
    path = {
        "raw": raw / "manifest.json",
        "normalized": normalized / "manifest.json",
        "code": raw / "code_manifest.json",
    }[kind]
    record = manifests[path]
    keys = [int(part) if part.isdigit() else part for part in field.split("/")]
    for key in keys[:-1]:
        record = record[key]
    record[keys[-1]] = value
    path.write_text(json.dumps(manifests[path]), encoding="utf-8")

    report = validator.validate_snapshot(raw, normalized)

    assert report["validation_status"] == "blocked"
    assert report["publication_status"] == "not_published"
    assert report["summary"]["candidates"] == report["summary"]["not_checked"] == 2
    assert any(
        item["code"] == "unsafe_path" and item["path"] == str(path)
        for item in report["global_diagnostics"]
    )


@pytest.mark.parametrize(
    "value",
    ["nested/guide.md", "guide.txt", "./guide.md", "guide.md/", "C:/guide.md", "bad\x00.md"],
)
def test_normalized_filenames_are_plain_posix_markdown_names(
    path_check_snapshot: tuple[Path, Path, dict], value: str
) -> None:
    raw, normalized, manifests = path_check_snapshot
    path = normalized / "manifest.json"
    manifests[path]["files"][0]["output_filename"] = value
    path.write_text(json.dumps(manifests[path]), encoding="utf-8")

    report = validator.validate_snapshot(raw, normalized)

    assert any(
        item["code"] == "unsafe_path" and item["path"] == str(path) and item["reference"] == value
        for item in report["global_diagnostics"]
    )


@pytest.mark.parametrize("value", ["C:guide.md", r"nested\guide.md"])
def test_normalized_filenames_allow_posix_colon_and_backslash_characters(
    path_check_snapshot: tuple[Path, Path, dict], value: str
) -> None:
    raw, normalized, manifests = path_check_snapshot
    path = normalized / "manifest.json"
    manifests[path]["files"][0]["output_filename"] = value
    path.write_text(json.dumps(manifests[path]), encoding="utf-8")

    report = validator.validate_snapshot(raw, normalized)

    assert not any(item["code"] == "unsafe_path" for item in report["global_diagnostics"])


@pytest.mark.parametrize("output_path", ["docs/guide.md", "code", "code-neighbor/example.py"])
def test_code_output_files_must_stay_under_the_declared_code_directory(
    path_check_snapshot: tuple[Path, Path, dict], output_path: str
) -> None:
    raw, normalized, manifests = path_check_snapshot
    path = raw / "code_manifest.json"
    manifests[path]["files"][0]["output_path"] = output_path
    path.write_text(json.dumps(manifests[path]), encoding="utf-8")

    report = validator.validate_snapshot(raw, normalized)

    assert any(
        item["code"] == "unsafe_path"
        and item["path"] == str(path)
        and item["reference"] == output_path
        for item in report["global_diagnostics"]
    )


@pytest.mark.parametrize("kind", ["raw", "code", "normalized"])
@pytest.mark.parametrize("target_kind", ["inside", "outside", "broken"])
def test_symlinked_manifests_are_rejected_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, target_kind: str
) -> None:
    raw, normalized, _ = write_snapshot(tmp_path)
    path = {
        "raw": raw / "manifest.json",
        "normalized": normalized / "manifest.json",
        "code": raw / "code_manifest.json",
    }[kind]
    content = path.read_bytes()
    path.unlink()
    target = path.parent / "saved.json" if target_kind == "inside" else tmp_path / "outside.json"
    if target_kind != "broken":
        target.write_bytes(content)
    path.symlink_to(target)
    original_read = Path.read_bytes

    def checked_read(candidate: Path) -> bytes:
        if candidate == path:
            raise AssertionError("Unsafe manifest was read before its path was checked")
        return original_read(candidate)

    monkeypatch.setattr(Path, "read_bytes", checked_read)

    report = validator.validate_snapshot(raw, normalized)

    assert report["validation_status"] == "blocked"
    assert report["publication_status"] == "not_published"
    assert str(path) not in report["inputs"]["manifest_sha256"]
    assert any(
        item["code"] == "unsafe_path" and item["path"] == str(path)
        for item in report["global_diagnostics"]
    )
    expected_count = None if kind == "raw" else 2
    assert report["summary"]["candidates"] == report["summary"]["not_checked"] == expected_count


@pytest.mark.parametrize(
    "location",
    [
        "raw_document",
        "normalized_document",
        "code_file",
        "raw_docs_dir",
        "normalized_docs_dir",
        "code_dir",
        "code_parent",
    ],
)
@pytest.mark.parametrize("target_kind", ["inside", "outside"])
def test_declared_files_and_directories_cannot_resolve_through_symlinks(
    tmp_path: Path, path_check_snapshot: tuple[Path, Path, dict], location: str, target_kind: str
) -> None:
    raw, normalized, _ = path_check_snapshot
    path = {
        "raw_document": raw / "docs" / "guide.md",
        "normalized_document": normalized / "docs" / "guide.md",
        "code_file": raw / "code" / "docs_src" / "example.py",
        "raw_docs_dir": raw / "docs",
        "normalized_docs_dir": normalized / "docs",
        "code_dir": raw / "code",
        "code_parent": raw / "code" / "docs_src",
    }[location]
    target = (
        path.with_name(path.name + "-saved") if target_kind == "inside" else tmp_path / "outside"
    )
    is_directory = path.is_dir()
    path.rename(target)
    path.symlink_to(target, target_is_directory=is_directory)

    report = validator.validate_snapshot(raw, normalized)

    assert report["validation_status"] == "blocked"
    assert report["publication_status"] == "not_published"
    assert report["summary"]["not_checked"] == 2
    assert any(item["code"] == "unsafe_path" for item in report["global_diagnostics"])


def test_ordinary_declared_paths_are_safe_even_when_a_document_is_missing(
    path_check_snapshot: tuple[Path, Path, dict],
) -> None:
    raw, normalized, _ = path_check_snapshot
    (normalized / "docs" / "guide.md").unlink()

    report = validator.validate_snapshot(raw, normalized)

    assert not any(item["code"] == "unsafe_path" for item in report["global_diagnostics"])
