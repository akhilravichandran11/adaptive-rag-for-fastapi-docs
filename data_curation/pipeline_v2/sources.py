"""Prepare an in-memory inventory without changing the normalized corpus."""

import hashlib
import json
import re
from pathlib import Path

from data_curation.pipeline_v2.schemas import InventoryCorpus, SourceDocument, SourceInventory


def _validate_manifest(manifest: object, manifest_path: Path) -> dict[str, Path]:
    if not isinstance(manifest, dict):
        raise ValueError(f"{manifest_path}: manifest must be an object")
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        raise ValueError(f"{manifest_path}: schema_version must be integer 1")
    if manifest.get("stage") != "normalized":
        raise ValueError(f"{manifest_path}: stage must be normalized")
    source = manifest.get("source")
    if (
        not isinstance(source, dict)
        or source.get("id") != "github-docs"
        or source.get("type") != "git"
    ):
        raise ValueError(f"{manifest_path}: source must identify github-docs of type git")
    if not isinstance(source.get("ref"), str) or not re.fullmatch(
        r"[0-9a-fA-F]{40}", source["ref"]
    ):
        raise ValueError(f"{manifest_path}: source.ref must be a full 40-character commit SHA")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{manifest_path}: files must be a nonempty list")
    seen = {"source_path": set(), "output_filename": set(), "source_id": set()}
    try:
        docs_dir = (manifest_path.parent / "docs").resolve()
    except RuntimeError:
        # Python 3.11/3.12 report symlink loops as RuntimeError.
        raise ValueError(f"{manifest_path}: docs directory contains a symlink loop") from None
    document_paths = {}
    for index, entry in enumerate(entries):
        context = f"{manifest_path}: files[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{context} must be an object")
        for field in ("source_path", "output_filename", "url", "bucket"):
            if not isinstance(entry.get(field), str) or not entry[field]:
                raise ValueError(f"{context}: {field} must be a nonempty string")
        if "source_id" in entry and (
            not isinstance(entry["source_id"], str) or not entry["source_id"]
        ):
            raise ValueError(f"{context}: source_id must be a nonempty string when present")
        if type(entry.get("bytes")) is not int or entry["bytes"] < 0:
            raise ValueError(f"{context}: bytes must be a nonnegative integer")
        source_path = entry["source_path"]
        if (
            "\\" in source_path
            or "\x00" in source_path
            or any(part in ("", ".", "..") for part in source_path.split("/"))
        ):
            raise ValueError(f"{context}: source_path must be a safe relative POSIX identity")
        filename = entry["output_filename"]
        if (
            "/" in filename
            or "\\" in filename
            or "\x00" in filename
            or Path(filename).suffix.lower() != ".md"
        ):
            raise ValueError(f"{context}: output_filename must be a single Markdown filename")
        try:
            document_path = (docs_dir / filename).resolve()
        except RuntimeError:
            raise ValueError(f"{context}: output_filename contains a symlink loop") from None
        if not document_path.is_relative_to(docs_dir):
            raise ValueError(f"{context}: output_filename resolves outside the docs directory")
        document_paths[source_path] = document_path
        for field in seen:
            value = entry.get(field, entry["source_path"])
            if value in seen[field]:
                raise ValueError(f"{context}: duplicate {field}")
            seen[field].add(value)
    return document_paths


def prepare_source_inventory(
    manifest_path: Path, *, selected_source_paths: tuple[str, ...] | None = None
) -> SourceInventory:
    """Capture source facts and complete-byte hashes from a normalized manifest."""
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes)
    except (ValueError, UnicodeError):
        raise ValueError(f"{manifest_path}: cannot parse manifest JSON") from None
    document_paths = _validate_manifest(manifest, manifest_path)
    entries = manifest["files"]
    if selected_source_paths is not None:
        if (
            not isinstance(selected_source_paths, tuple)
            or not selected_source_paths
            or any(not isinstance(path, str) for path in selected_source_paths)
        ):
            raise ValueError(f"{manifest_path}: selection must be a nonempty tuple of strings")
        if len(set(selected_source_paths)) != len(selected_source_paths):
            raise ValueError(f"{manifest_path}: selection contains duplicate source paths")
        known_paths = {entry["source_path"] for entry in entries}
        unknown = [path for path in selected_source_paths if path not in known_paths]
        if unknown:
            raise ValueError(
                f"{manifest_path}: selection contains unknown source paths: {unknown!r}"
            )
        entries = [entry for entry in entries if entry["source_path"] in selected_source_paths]
    documents = []
    for entry in sorted(entries, key=lambda entry: entry["source_path"]):
        content = document_paths[entry["source_path"]].read_bytes()
        context = f"{manifest_path}: source {entry['source_path']!r}"
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError(f"{context}: document must decode as UTF-8") from None
        if len(content) != entry["bytes"]:
            raise ValueError(f"{context}: document byte count does not match manifest bytes")
        documents.append(
            SourceDocument(
                source_id=entry.get("source_id", entry["source_path"]),
                source_path=entry["source_path"],
                output_filename=entry["output_filename"],
                source_url=entry["url"],
                bucket=entry["bucket"],
                content_sha256=hashlib.sha256(content).hexdigest(),
            )
        )
    return SourceInventory(
        corpus=InventoryCorpus(
            source_commit=manifest["source"]["ref"],
            collection_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
            inventory_scope="full" if selected_source_paths is None else "selected",
        ),
        documents=documents,
    )
