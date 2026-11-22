"""Offline source-inventory behavior through the approved preparation seam."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

from data_curation.pipeline_v2.sources import prepare_source_inventory


@pytest.fixture
def corpus(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "z.md").write_bytes(b"abc")
    (docs / "a.md").write_bytes(b"")
    manifest = {
        "schema_version": 1,
        "stage": "normalized",
        "source": {"id": "github-docs", "type": "git", "ref": "Ab" * 20},
        "files": [
            {
                "source_path": "tutorial/Z.md",
                "output_filename": "z.md",
                "url": "https://example.invalid/Z",
                "bucket": "Tutorial",
                "bytes": 3,
                "source_id": "Upstream-Z",
            },
            {
                "source_path": "guide/a.md",
                "output_filename": "a.md",
                "url": "https://example.invalid/a",
                "bucket": "guide",
                "bytes": 0,
            },
        ],
        "normalization": {"unused": True},
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path, manifest


def test_full_inventory_preserves_facts_and_serializes_only_approved_fields(corpus):
    path, _ = corpus
    manifest_bytes = path.read_bytes()
    result = prepare_source_inventory(path)

    assert json.loads(result.model_dump_json()) == {
        "schema_version": "1.0",
        "corpus": {
            "source_commit": "Ab" * 20,
            "collection_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "inventory_scope": "full",
        },
        "documents": [
            {
                "source_id": "guide/a.md",
                "source_path": "guide/a.md",
                "output_filename": "a.md",
                "source_url": "https://example.invalid/a",
                "bucket": "guide",
                "content_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            },
            {
                "source_id": "Upstream-Z",
                "source_path": "tutorial/Z.md",
                "output_filename": "z.md",
                "source_url": "https://example.invalid/Z",
                "bucket": "Tutorial",
                "content_sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            },
        ],
    }
    assert path.read_bytes() == manifest_bytes
    assert (path.parent / "docs/z.md").read_bytes() == b"abc"
    assert (path.parent / "docs/a.md").read_bytes() == b""
    assert sorted(p.name for p in path.parent.iterdir()) == ["docs", "manifest.json"]


@pytest.mark.parametrize(
    "selection, expected",
    [
        (("tutorial/Z.md",), ["tutorial/Z.md"]),
        (("tutorial/Z.md", "guide/a.md"), ["guide/a.md", "tutorial/Z.md"]),
    ],
)
def test_explicit_selection_is_exact_and_always_marked_selected(corpus, selection, expected):
    path, _ = corpus
    # A missing unselected body must not prevent a selected inventory.
    if len(selection) == 1:
        (path.parent / "docs/a.md").unlink()
    inventory = prepare_source_inventory(path, selected_source_paths=selection)
    assert inventory.corpus.inventory_scope == "selected"
    assert [doc.source_path for doc in inventory.documents] == expected


@pytest.mark.parametrize(
    "selection",
    [
        (),
        ("guide/a.md", "guide/a.md"),
        ("a.md",),
        ("guide/",),
        ("Guide/a.md",),
        ("unknown.md", "also-unknown.md"),
        "guide/a.md",
        ["guide/a.md"],
        (None,),
        ({},),
    ],
)
def test_invalid_selection_fails_with_context(corpus, selection):
    path, _ = corpus
    with pytest.raises(ValueError, match="manifest.json.*selection"):
        prepare_source_inventory(path, selected_source_paths=selection)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("schema_version", 1.0),
        ("schema_version", "1"),
        ("schema_version", 2),
        ("stage", "raw"),
        ("source", None),
        ("files", []),
        ("files", {}),
        ("files", [None]),
    ],
)
def test_malformed_manifest_fields_are_contextual_value_errors(corpus, field, value):
    path, manifest = corpus
    manifest[field] = value
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest.json"):
        prepare_source_inventory(path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "other"),
        ("type", "http"),
        ("ref", "a" * 39),
        ("ref", "g" * 40),
        ("ref", None),
        ("ref", "a" * 40 + "\n"),
    ],
)
def test_invalid_source_provenance_is_rejected(corpus, field, value):
    path, manifest = corpus
    manifest["source"][field] = value
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest.json.*source"):
        prepare_source_inventory(path)


@pytest.mark.parametrize("raw", [b"[]", b"null", b"{", b'"secret-sentinel"', b"\xff"])
def test_malformed_json_does_not_echo_input(corpus, raw):
    path, _ = corpus
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="manifest.json") as error:
        prepare_source_inventory(path)
    assert "secret-sentinel" not in str(error.value)


@pytest.mark.parametrize("field", ["source_path", "output_filename", "url", "bucket", "bytes"])
def test_missing_entry_fields_are_rejected_before_selection(corpus, field):
    path, manifest = corpus
    del manifest["files"][1][field]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match=rf"manifest.json.*files\[1\].*{field}"):
        prepare_source_inventory(path, selected_source_paths=("tutorial/Z.md",))


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_path", ""),
        ("source_path", None),
        ("source_path", 1),
        ("output_filename", ""),
        ("url", ""),
        ("url", ["secret-sentinel"]),
        ("bucket", None),
        ("source_id", ""),
        ("source_id", None),
        ("source_id", 12),
        ("bytes", True),
        ("bytes", False),
        ("bytes", -1),
        ("bytes", 0.0),
        ("bytes", "0"),
    ],
)
def test_invalid_entry_fields_are_rejected_before_selection(corpus, field, value):
    path, manifest = corpus
    manifest["files"][1][field] = value
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match=rf"manifest.json.*files\[1\].*{field}") as error:
        prepare_source_inventory(path, selected_source_paths=("tutorial/Z.md",))
    assert "secret-sentinel" not in str(error.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_path", "tutorial/Z.md"),
        ("output_filename", "z.md"),
        ("source_id", "Upstream-Z"),
        ("source_id", "tutorial/Z.md"),
    ],
)
def test_duplicate_manifest_identities_are_rejected_before_selection(corpus, field, value):
    path, manifest = corpus
    if value == "tutorial/Z.md" and field == "source_id":
        del manifest["files"][0]["source_id"]
    manifest["files"][1][field] = value
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match=rf"manifest.json.*duplicate.*{field}"):
        prepare_source_inventory(path, selected_source_paths=("tutorial/Z.md",))


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_path", "/a.md"),
        ("source_path", "a//b.md"),
        ("source_path", "a/./b.md"),
        ("source_path", "a/../b.md"),
        ("source_path", "a\\b.md"),
        ("source_path", "a/"),
        ("source_path", ".."),
        ("source_path", "a\x00.md"),
        ("output_filename", "../a.md"),
        ("output_filename", "dir/a.md"),
        ("output_filename", "/a.md"),
        ("output_filename", "a\\b.md"),
        ("output_filename", "."),
        ("output_filename", ".."),
        ("output_filename", "a.txt"),
        ("output_filename", "a.md/"),
        ("output_filename", "a\x00.md"),
    ],
)
def test_unsafe_names_fail_even_when_unselected(corpus, field, value):
    path, manifest = corpus
    manifest["files"][1][field] = value
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match=rf"manifest.json.*files\[1\].*{field}"):
        prepare_source_inventory(path, selected_source_paths=("tutorial/Z.md",))


@pytest.mark.parametrize("selected", [None, ("tutorial/Z.md",)])
def test_symlink_escape_fails_even_when_unselected(corpus, selected):
    path, _ = corpus
    outside = path.parent / "outside.md"
    outside.write_bytes(b"")
    doc = path.parent / "docs/a.md"
    doc.unlink()
    doc.symlink_to(outside)
    with pytest.raises(ValueError, match=r"manifest.json.*files\[1\].*outside"):
        prepare_source_inventory(path, selected_source_paths=selected)
    assert outside.read_bytes() == b""


def test_internal_symlink_keeps_manifest_identity(corpus):
    path, _ = corpus
    doc = path.parent / "docs/z.md"
    doc.rename(path.parent / "docs/body.md")
    doc.symlink_to("body.md")
    result = prepare_source_inventory(path)
    assert result.documents[1].output_filename == "z.md"
    assert result.documents[1].content_sha256 == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


@pytest.mark.parametrize(
    "body,declared_bytes,expected",
    [
        (b"secret-sentinel\xff", 16, "UTF-8"),
        (b"abc", 2, "byte count"),
        ("é".encode(), 1, "byte count"),
    ],
)
def test_invalid_selected_body_fails_without_modification(corpus, body, declared_bytes, expected):
    path, manifest = corpus
    doc = path.parent / "docs/z.md"
    doc.write_bytes(body)
    manifest["files"][0]["bytes"] = declared_bytes
    path.write_text(json.dumps(manifest), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match=f"manifest.json.*tutorial/Z.md.*{expected}") as error:
        prepare_source_inventory(path)
    assert "secret-sentinel" not in str(error.value)
    assert path.read_bytes() == before
    assert doc.read_bytes() == body
    assert (path.parent / "docs/a.md").read_bytes() == b""
    assert sorted(p.name for p in path.parent.iterdir()) == ["docs", "manifest.json"]


@pytest.mark.parametrize("filename", ["manifest.json", "docs/z.md"])
def test_missing_input_remains_oserror(corpus, filename):
    path, _ = corpus
    (path.parent / filename).unlink()
    with pytest.raises(FileNotFoundError):
        prepare_source_inventory(path)


@pytest.mark.parametrize("filename", ["manifest.json", "z.md"])
def test_unreadable_input_remains_oserror(corpus, monkeypatch, filename):
    path, _ = corpus
    original_read = Path.read_bytes

    def unreadable(file):
        if file.name == filename:
            raise PermissionError("fixture read denied")
        return original_read(file)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    with pytest.raises(PermissionError, match="fixture read denied"):
        prepare_source_inventory(path)


def test_reordering_manifest_only_changes_manifest_hash(corpus):
    path, manifest = corpus
    first = prepare_source_inventory(path)
    manifest["files"].reverse()
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    second = prepare_source_inventory(path)
    assert first.documents == second.documents
    assert first.corpus.collection_manifest_sha256 != second.corpus.collection_manifest_sha256
    assert second.corpus.collection_manifest_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("body", [b"abc\r\n", b"abc\n", "é\r\n".encode()])
def test_changed_body_hashes_exact_bytes_including_line_endings(corpus, body):
    path, manifest = corpus
    before = prepare_source_inventory(path)
    (path.parent / "docs/z.md").write_bytes(body)
    manifest["files"][0]["bytes"] = len(body)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    after = prepare_source_inventory(path)
    assert before.documents[1].content_sha256 != after.documents[1].content_sha256
    assert after.documents[1].content_sha256 == hashlib.sha256(body).hexdigest()


def test_hash_and_validation_use_single_captured_buffers(corpus, monkeypatch):
    path, _ = corpus
    original_read = Path.read_bytes
    captured_manifest = original_read(path)
    reads = []

    def change_after_read(file):
        reads.append(file.name)
        captured = original_read(file)
        file.write_bytes(b"changed after capture")
        return captured

    monkeypatch.setattr(Path, "read_bytes", change_after_read)
    result = prepare_source_inventory(path, selected_source_paths=("tutorial/Z.md",))
    assert reads == ["manifest.json", "z.md"]
    assert result.corpus.collection_manifest_sha256 == hashlib.sha256(captured_manifest).hexdigest()
    assert result.documents[0].content_sha256 == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_manifest_validation_precedes_all_body_reads(corpus, monkeypatch):
    path, manifest = corpus
    manifest["files"][1]["bucket"] = None
    path.write_text(json.dumps(manifest), encoding="utf-8")
    original_read = Path.read_bytes

    def manifest_only(file):
        assert file == path, "document body read before manifest validation finished"
        return original_read(file)

    monkeypatch.setattr(Path, "read_bytes", manifest_only)
    with pytest.raises(ValueError, match="bucket"):
        prepare_source_inventory(path, selected_source_paths=("tutorial/Z.md",))


def test_extra_documents_are_not_discovered_and_unselected_bodies_are_not_validated(corpus):
    path, _ = corpus
    (path.parent / "docs/extra.md").write_bytes(b"\xff")
    (path.parent / "docs/a.md").write_bytes(b"\xff")
    inventory = prepare_source_inventory(path, selected_source_paths=("tutorial/Z.md",))
    assert [doc.source_path for doc in inventory.documents] == ["tutorial/Z.md"]


@pytest.mark.parametrize("legacy_first", [True, False])
def test_import_orders_and_execution_are_isolated_without_credentials(corpus, legacy_first):
    import os
    import subprocess

    path, _ = corpus
    script = """
import builtins
import importlib
import socket
import sys
from pathlib import Path

original_import = builtins.__import__
blocked = ("dotenv", "openai", "data_collection", "data_ingestion",
           "data_curation.config", "data_curation.pipeline", "data_curation.stage")

def guarded_import(name, *args, **kwargs):
    if name.startswith(blocked) and not name.startswith("data_curation.pipeline_v2"):
        raise AssertionError("unexpected dependency: " + name)
    return original_import(name, *args, **kwargs)

def no_network(*args, **kwargs):
    raise AssertionError("network access attempted")

builtins.__import__ = guarded_import
socket.socket.connect = no_network
socket.socket.connect_ex = no_network
socket.create_connection = no_network
names = ["data_curation.schemas", "data_curation.pipeline_v2.sources"]
if sys.argv[2] == "False":
    names.reverse()
for name in names:
    importlib.import_module(name)
from data_curation.pipeline_v2.sources import prepare_source_inventory
inventory = prepare_source_inventory(Path(sys.argv[1]))
assert type(inventory).__module__ == "data_curation.pipeline_v2.schemas"
assert len(inventory.documents) == 2
assert "schemas" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), str(legacy_first)],
        cwd=path.parent,
        env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name,context", [("docs", "docs directory"), ("a.md", r"files\[1\]")])
def test_python_311_symlink_loop_resolution_has_manifest_context(
    corpus, monkeypatch, name, context
):
    path, _ = corpus
    original_resolve = Path.resolve

    def symlink_loop(file, *args, **kwargs):
        if file.name == name:
            # Python 3.11/3.12 use RuntimeError here; 3.13 changed resolve behavior.
            raise RuntimeError("Symlink loop from fixture")
        return original_resolve(file, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", symlink_loop)
    with pytest.raises(ValueError, match=f"manifest.json.*{context}.*symlink loop"):
        prepare_source_inventory(path, selected_source_paths=("tutorial/Z.md",))
