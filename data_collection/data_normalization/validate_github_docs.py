"""Validate normalized GitHub documents and publish accepted snapshots."""

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def _has_strings(record: object, fields: tuple[str, ...]) -> bool:
    """Check required nonempty strings in a manifest object."""
    return isinstance(record, dict) and all(
        isinstance(record.get(field), str) and record[field] for field in fields
    )


def _has_counts(record: object, fields: tuple[str, ...]) -> bool:
    """Check nonnegative integer counts, excluding booleans."""
    return isinstance(record, dict) and all(
        type(record.get(field)) is int and record[field] >= 0 for field in fields
    )


def _manifest_type_errors(manifest: dict, kind: str) -> list[str]:
    """Check typed fields after file identities and structure are readable."""
    errors = []
    source_fields = ("id", "version", "repo", "repo_url", "ref")
    if kind != "code":
        source_fields += ("type",)
    if kind == "raw":
        source_fields += ("docs_path", "ref_url")
    if not _has_strings(manifest.get("source"), source_fields):
        errors.append("source")

    stats = manifest.get("stats")
    count_fields = ("total_files", "total_bytes")
    if kind == "code":
        count_fields += ("failures",)
    if not _has_counts(stats, count_fields):
        errors.append("stats")
    if isinstance(stats, dict):
        if kind == "code":
            by_ext = stats.get("by_ext")
            if not isinstance(by_ext, dict) or not all(
                isinstance(ext, str) and type(count) is int and count >= 0
                for ext, count in by_ext.items()
            ):
                errors.append("stats.by_ext")
        else:
            by_bucket = stats.get("by_bucket")
            if not isinstance(by_bucket, dict) or not all(
                isinstance(bucket, str) and bucket and _has_counts(counts, ("files", "bytes"))
                for bucket, counts in by_bucket.items()
            ):
                errors.append("stats.by_bucket")

    if kind == "raw":
        collection = manifest.get("collection")
        if not (
            _has_strings(
                collection,
                ("fetched_at", "tool", "ingest_file", "docs_dir"),
            )
            and _has_counts(collection, ("min_file_bytes",))
            and all(
                isinstance(collection.get(field), list)
                and all(isinstance(item, str) and item for item in collection[field])
                for field in ("included_subdirs", "top_level_allowlist", "include_patterns")
            )
        ):
            errors.append("collection")
    elif kind == "normalized":
        if manifest.get("stage") != "normalized":
            errors.append("stage")
        if not _has_strings(manifest.get("derived_from"), ("docs_manifest", "code_manifest")):
            errors.append("derived_from")
        normalization = manifest.get("normalization")
        if not (
            _has_strings(normalization, ("generated_at",))
            and _has_counts(normalization, ("inlined_includes", "unresolved_includes"))
        ):
            errors.append("normalization")
    else:
        if manifest.get("kind") != "referenced-code" or not _has_strings(
            manifest, ("referenced_from",)
        ):
            errors.append("kind/referenced_from")
        if not _has_strings(manifest.get("collection"), ("fetched_at", "method", "code_dir")):
            errors.append("collection")
        failures = manifest.get("failures")
        if not isinstance(failures, list) or not all(
            _has_strings(failure, ("repo_path", "url")) and isinstance(failure.get("error"), str)
            for failure in failures
        ):
            errors.append("failures")

    for index, entry in enumerate(manifest["files"]):
        if not (_has_counts(entry, ("bytes",)) and _has_strings(entry, ("url",))):
            errors.append(f"files[{index}].bytes/url")
        if kind != "code" and not _has_strings(entry, ("bucket",)):
            errors.append(f"files[{index}].bucket")

        if kind == "normalized":
            includes = entry.get("includes")
            if not isinstance(includes, list) or not all(
                _has_strings(include, ("repo_path",))
                and isinstance(include.get("options"), str)
                and type(include.get("resolved")) is bool
                for include in includes
            ):
                errors.append(f"files[{index}].includes")
        elif kind == "code":
            if not (_has_strings(entry, ("output_path",)) and isinstance(entry.get("ext"), str)):
                errors.append(f"files[{index}].output_path/ext")
            references = entry.get("referenced_by")
            if not isinstance(references, list) or not all(
                _has_strings(reference, ("doc",)) and isinstance(reference.get("options"), str)
                for reference in references
            ):
                errors.append(f"files[{index}].referenced_by")

    return errors


def _manifest_totals_errors(manifest: dict, kind: str) -> list[str]:
    """Compare declared totals with this manifest's own typed records."""
    files = manifest["files"]
    expected = {
        "total_files": len(files),
        "total_bytes": sum(entry["bytes"] for entry in files),
    }
    if kind == "code":
        by_ext = {}
        for entry in files:
            ext = entry["ext"]
            by_ext[ext] = by_ext.get(ext, 0) + 1
        expected["by_ext"] = by_ext
        expected["failures"] = len(manifest["failures"])
    else:
        by_bucket = {}
        for entry in files:
            counts = by_bucket.setdefault(entry["bucket"], {"files": 0, "bytes": 0})
            counts["files"] += 1
            counts["bytes"] += entry["bytes"]
        expected["by_bucket"] = by_bucket

    errors = [
        f"stats.{field}" for field, value in expected.items() if manifest["stats"][field] != value
    ]
    if kind == "normalized":
        total = sum(len(entry["includes"]) for entry in files)
        resolved = sum(include["resolved"] for entry in files for include in entry["includes"])
        for field, value in (
            ("inlined_includes", resolved),
            ("unresolved_includes", total - resolved),
        ):
            if manifest["normalization"][field] != value:
                errors.append(f"normalization.{field}")
    return errors


def _path_problem(root: Path | None, value: str, plain_markdown: bool = False) -> str | None:
    """Check a relative path; inspect local components when a root is supplied."""
    relative = Path(value)
    if not relative.parts or "\x00" in value or relative.is_absolute() or ".." in relative.parts:
        return "Expected a relative path without parent traversal or null bytes"
    if plain_markdown and (relative.name != value or relative.suffix != ".md"):
        return "Normalized output_filename must be a plain .md filename"
    if root is None:
        return None

    try:
        candidate = root
        for part in relative.parts:
            candidate = candidate / part
            if candidate.is_symlink():
                return f"Path resolves through a symlink: {candidate}"
        if not candidate.resolve().is_relative_to(root):
            return "Path escapes its expected directory"
    except (OSError, RuntimeError) as error:
        return f"Cannot verify path safety: {error}"
    return None


def validate_snapshot(raw_dir: Path, normalized_dir: Path) -> dict:
    """Read manifests and report when raw membership cannot be decoded."""
    started = datetime.now(UTC)
    raw_dir = raw_dir.resolve()
    normalized_dir = normalized_dir.resolve()
    raw_manifest = raw_dir / "manifest.json"
    report = {
        "schema_version": 1,
        "validator_version": 1,
        "run_id": f"{started.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex}",
        "started_at": started.isoformat(),
        "finished_at": None,
        "source": None,
        "inputs": {
            "raw_dir": str(raw_dir),
            "normalized_dir": str(normalized_dir),
            "manifest_sha256": {},
        },
        "validation_status": "blocked",
        "publication_status": "not_published",
        "accepted_path": None,
        "summary": {
            "candidates": None,
            "passed": 0,
            "failed": 0,
            "needs_review": 0,
            "not_checked": None,
        },
        "global_diagnostics": [],
        "files": [],
        "extras": [],
    }
    manifests = {}
    for path in (
        raw_manifest,
        raw_dir / "code_manifest.json",
        normalized_dir / "manifest.json",
    ):
        problem = _path_problem(path.parent, path.name)
        if problem:
            report["global_diagnostics"].append(
                {
                    "code": "unsafe_path",
                    "message": problem,
                    "path": str(path),
                    "line": None,
                    "reference": path.name,
                }
            )
            continue
        try:
            content = path.read_bytes()
            report["inputs"]["manifest_sha256"][str(path)] = hashlib.sha256(content).hexdigest()
            manifests[path] = json.loads(content.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            report["global_diagnostics"].append(
                {
                    "code": "input_manifest_invalid",
                    "message": f"Cannot read manifest: {error}",
                    "path": str(path),
                    "line": None,
                    "reference": None,
                }
            )

    if raw_manifest in manifests:
        raw = manifests[raw_manifest]
        membership_readable = (
            isinstance(raw, dict)
            and type(raw.get("schema_version")) is int
            and raw["schema_version"] == 1
            and isinstance(raw.get("files"), list)
        )
        if membership_readable:
            membership_readable = all(
                isinstance(entry, dict)
                and all(
                    isinstance(entry.get(field), str) and entry[field]
                    for field in ("source_path", "output_filename")
                )
                for entry in raw["files"]
            )

        if not membership_readable:
            report["global_diagnostics"].append(
                {
                    "code": "input_manifest_invalid",
                    "message": (
                        "Raw manifest requires schema_version 1 and a files list "
                        "with nonempty source_path and output_filename strings."
                    ),
                    "path": str(raw_manifest),
                    "line": None,
                    "reference": None,
                }
            )
        else:
            report["files"] = [
                {
                    "source_path": entry["source_path"],
                    "output_filename": entry["output_filename"],
                    "status": "not_checked",
                    "sha256": None,
                    "diagnostics": [],
                }
                for entry in raw["files"]
            ]
            report["summary"]["candidates"] = len(report["files"])
            report["summary"]["not_checked"] = len(report["files"])

    typed_manifests = {}
    for path, manifest in manifests.items():
        identity_fields = (
            ("repo_path",)
            if path == raw_dir / "code_manifest.json"
            else ("source_path", "output_filename")
        )
        structure_valid = (
            isinstance(manifest, dict)
            and type(manifest.get("schema_version")) is int
            and manifest["schema_version"] == 1
            and isinstance(manifest.get("files"), list)
        )
        if structure_valid:
            structure_valid = all(
                isinstance(entry, dict)
                and all(
                    isinstance(entry.get(field), str) and entry[field] for field in identity_fields
                )
                for entry in manifest["files"]
            )

        if not structure_valid:
            if path != raw_manifest:
                report["global_diagnostics"].append(
                    {
                        "code": "input_manifest_invalid",
                        "message": (
                            "Manifest requires schema_version 1 and a files list "
                            "with nonempty identity strings: " + ", ".join(identity_fields)
                        ),
                        "path": str(path),
                        "line": None,
                        "reference": None,
                    }
                )
            continue

        if path == raw_manifest:
            kind = "raw"
        elif path == raw_dir / "code_manifest.json":
            kind = "code"
        else:
            kind = "normalized"
        type_errors = _manifest_type_errors(manifest, kind)
        if type_errors:
            report["global_diagnostics"].append(
                {
                    "code": "input_manifest_invalid",
                    "message": "Invalid required fields: " + ", ".join(type_errors),
                    "path": str(path),
                    "line": None,
                    "reference": None,
                }
            )
        else:
            typed_manifests[path] = manifest
            totals_errors = _manifest_totals_errors(manifest, kind)
            if totals_errors:
                report["global_diagnostics"].append(
                    {
                        "code": "manifest_totals_mismatch",
                        "message": "Totals disagree with records: " + ", ".join(totals_errors),
                        "path": str(path),
                        "line": None,
                        "reference": None,
                    }
                )

            if kind == "raw":
                docs_dir = manifest["collection"]["docs_dir"]
                checks = [
                    (raw_dir, docs_dir, False),
                    (raw_dir, manifest["collection"]["ingest_file"], False),
                    (None, manifest["source"]["docs_path"], False),
                ]
            elif kind == "normalized":
                docs_dir = "docs"
                checks = [(normalized_dir, docs_dir, False)]
                checks.extend(
                    (raw_dir, manifest["derived_from"][field], False)
                    for field in ("docs_manifest", "code_manifest")
                )
            else:
                code_dir = manifest["collection"]["code_dir"]
                checks = [
                    (raw_dir, code_dir, False),
                    (raw_dir, manifest["referenced_from"], False),
                ]
                checks.extend(
                    (None, failure["repo_path"], False) for failure in manifest["failures"]
                )

            for entry in manifest["files"]:
                if kind == "code":
                    checks.extend(
                        [
                            (None, entry["repo_path"], False),
                            (raw_dir, entry["output_path"], False),
                        ]
                    )
                    checks.extend(
                        (None, reference["doc"], False) for reference in entry["referenced_by"]
                    )
                    output_path = Path(entry["output_path"])
                    code_root = Path(code_dir)
                    if output_path == code_root or not output_path.is_relative_to(code_root):
                        report["global_diagnostics"].append(
                            {
                                "code": "unsafe_path",
                                "message": "Code output_path must be inside collection.code_dir",
                                "path": str(path),
                                "line": None,
                                "reference": entry["output_path"],
                            }
                        )
                else:
                    docs_root = raw_dir if kind == "raw" else normalized_dir
                    checks.extend(
                        [
                            (None, entry["source_path"], False),
                            (None, entry["output_filename"], kind == "normalized"),
                            (docs_root, f"{docs_dir}/{entry['output_filename']}", False),
                        ]
                    )
                    if kind == "normalized":
                        checks.extend(
                            (None, include["repo_path"], False) for include in entry["includes"]
                        )

            for root, value, plain_markdown in checks:
                problem = _path_problem(root, value, plain_markdown)
                if problem:
                    report["global_diagnostics"].append(
                        {
                            "code": "unsafe_path",
                            "message": problem,
                            "path": str(path),
                            "line": None,
                            "reference": value,
                        }
                    )

        for field in identity_fields:
            seen = set()
            for entry in manifest["files"]:
                value = entry[field]
                if value in seen:
                    report["global_diagnostics"].append(
                        {
                            "code": "duplicate_identity",
                            "message": f"Repeated {field}: {value}",
                            "path": str(path),
                            "line": None,
                            "reference": value,
                        }
                    )
                seen.add(value)

    if len(typed_manifests) == 3:
        code_path = raw_dir / "code_manifest.json"
        normalized_path = normalized_dir / "manifest.json"
        raw = typed_manifests[raw_manifest]
        normalized = typed_manifests[normalized_path]
        raw_source = raw["source"]

        for path, manifest in typed_manifests.items():
            fields = ("id", "version", "repo", "repo_url", "ref")
            if path != code_path:
                fields += ("type",)
            conflicts = [
                f"source.{field}"
                for field in fields
                if manifest["source"][field] != raw_source[field]
            ]
            if path == normalized_path:
                for field, expected in (
                    ("docs_manifest", raw_manifest.name),
                    ("code_manifest", code_path.name),
                ):
                    if manifest["derived_from"][field] != expected:
                        conflicts.append(f"derived_from.{field}")
            if path == code_path and manifest["referenced_from"] != raw_manifest.name:
                conflicts.append("referenced_from")
            if conflicts:
                report["global_diagnostics"].append(
                    {
                        "code": "source_mismatch",
                        "message": "Provenance disagrees with raw inputs: " + ", ".join(conflicts),
                        "path": str(path),
                        "line": None,
                        "reference": None,
                    }
                )

        raw_by_name = {entry["output_filename"]: entry for entry in raw["files"]}
        raw_by_source = {entry["source_path"]: entry for entry in raw["files"]}
        if len(raw_by_name) == len(raw_by_source) == len(raw["files"]):
            for entry in normalized["files"]:
                raw_entry = raw_by_name.get(entry["output_filename"]) or raw_by_source.get(
                    entry["source_path"]
                )
                if raw_entry is None:
                    continue
                conflicts = [
                    field
                    for field in ("source_path", "output_filename", "bucket", "url")
                    if entry[field] != raw_entry[field]
                ]
                if conflicts:
                    report["global_diagnostics"].append(
                        {
                            "code": "source_mismatch",
                            "message": "Document disagrees with raw entry: " + ", ".join(conflicts),
                            "path": str(normalized_path),
                            "line": None,
                            "reference": entry["output_filename"],
                        }
                    )

        if not any(item["code"] == "source_mismatch" for item in report["global_diagnostics"]):
            report["source"] = dict(raw_source)

    if not report["global_diagnostics"]:
        raise NotImplementedError(
            "Remaining manifest and document validation are not implemented yet."
        )

    report["finished_at"] = datetime.now(UTC).isoformat()
    return report


def main(argv: list[str] | None = None) -> int:
    """Reject invalid command arguments before any output is written."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--normalized-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        raw_dir = args.raw_dir.resolve()
        normalized_dir = args.normalized_dir.resolve()
        output_dir = args.output_dir.resolve()
    except (OSError, RuntimeError) as error:
        parser.error(f"Cannot resolve directory paths: {error}")

    for input_dir in (raw_dir, normalized_dir):
        if (
            output_dir == input_dir
            or output_dir.is_relative_to(input_dir)
            or input_dir.is_relative_to(output_dir)
        ):
            parser.error("--output-dir must not overlap either input tree")

    print("Snapshot validation and publication are not implemented yet.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
