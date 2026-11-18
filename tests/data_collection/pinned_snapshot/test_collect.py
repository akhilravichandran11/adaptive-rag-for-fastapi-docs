"""Offline CLI contracts for the pinned snapshot collector."""

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from data_collection.pinned_snapshot import collect  # noqa: E402


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail_network(*args, **kwargs):
        raise AssertionError("Offline collector tests cannot use the network")

    monkeypatch.setattr(socket.socket, "connect", fail_network)
    monkeypatch.setattr(socket.socket, "connect_ex", fail_network)
    monkeypatch.setattr(socket, "create_connection", fail_network)


@pytest.fixture
def snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "upstream"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Offline fixture")
    doc = root / "docs/en/docs/tutorial/example.md"
    doc.parent.mkdir(parents=True)
    doc.write_bytes(b"# Example\n" + b"x" * 500 + b"\n")
    git(root, "add", ".")
    git(root, "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
    monkeypatch.setattr(collect, "SOURCE_COMMIT", git(root, "rev-parse", "HEAD"))
    return root


def test_module_requires_both_paths() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "data_collection.pinned_snapshot.collect"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "--snapshot-dir" in result.stderr and "--output-dir" in result.stderr


def test_wrong_revision_fails_without_output(snapshot, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(collect, "SOURCE_COMMIT", "0" * 40)
    output = tmp_path / "raw"
    assert collect.main(["--snapshot-dir", str(snapshot), "--output-dir", str(output)]) == 1
    assert "commit" in capsys.readouterr().err
    assert not output.exists()


def test_copies_selected_source_bytes_and_manifest(snapshot, tmp_path, monkeypatch):
    docs = snapshot / "docs/en/docs"
    samples = {
        "async.md": b"a" * 500,
        "advanced/nested/a.md": b"\xc3\xa9" * 250,
        "reference/ref.md": b"r" * 499 + b"\n",
        "how-to/task.md": b"h" * 500,
        "deployment/run.md": b"d" * 500,
        "tutorial/short.md": b"s" * 499,
        "tutorial/upper.MD": b"u" * 500,
        "index.md": b"i" * 500,
        "other/no.md": b"o" * 500,
    }
    for name, data in samples.items():
        path = docs / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    git(snapshot, "add", ".")
    git(snapshot, "-c", "commit.gpgsign=false", "commit", "-qm", "selection")
    monkeypatch.setattr(collect, "SOURCE_COMMIT", git(snapshot, "rev-parse", "HEAD"))
    (docs / "tutorial/untracked.md").write_bytes(b"?" * 600)
    output = tmp_path / "raw"
    assert collect.main(["--snapshot-dir", str(snapshot), "--output-dir", str(output)]) == 0
    manifest = json.loads((output / "manifest.json").read_text())
    assert {r["source_path"] for r in manifest["files"]} == {
        "tutorial/example.md",
        "async.md",
        "advanced/nested/a.md",
        "reference/ref.md",
        "how-to/task.md",
        "deployment/run.md",
    }
    for record in manifest["files"]:
        data = (docs / record["source_path"]).read_bytes()
        assert (output / "docs" / record["output_filename"]).read_bytes() == data
        assert record["bytes"] == len(data)
        assert record["url"].endswith("/docs/en/docs/" + record["source_path"])
    assert manifest["stats"]["total_files"] == 6
    assert manifest["stats"]["total_bytes"] == 3011
    assert manifest["source"]["ref"] == collect.SOURCE_COMMIT
    assert manifest["collection"]["tool"] == "direct-snapshot"
    assert "ingest_file" not in manifest["collection"]
    assert "include_patterns" not in manifest["collection"]


def test_includes_preserve_options_backlinks_and_copy_once(snapshot, tmp_path, monkeypatch):
    doc = snapshot / "docs/en/docs/tutorial/example.md"
    doc.write_bytes(
        doc.read_bytes()
        + (
            b"{* ../../docs_src/example.py ln[1:2] hl[2] *}\r\n"
            b"{! ../../docs_src/example.py !}\n"
            b"{!>../../fastapi/example.py!}\n"
        )
    )
    for name in ["docs_src/example.py", "fastapi/example.py"]:
        path = snapshot / name
        path.parent.mkdir()
        path.write_bytes(b"x = 1\r\ny = 2\r\n")
    git(snapshot, "add", ".")
    git(snapshot, "-c", "commit.gpgsign=false", "commit", "-qm", "includes")
    monkeypatch.setattr(collect, "SOURCE_COMMIT", git(snapshot, "rev-parse", "HEAD"))
    output = tmp_path / "raw"
    assert collect.main(["--snapshot-dir", str(snapshot), "--output-dir", str(output)]) == 0
    manifest = json.loads((output / "code_manifest.json").read_text())
    assert manifest["stats"] == {
        "total_files": 2,
        "total_bytes": 28,
        "failures": 0,
        "by_ext": {".py": 2},
    }
    assert manifest["collection"]["method"] == "direct-snapshot"
    assert manifest["failures"] == []
    assert manifest["files"][0]["referenced_by"] == [
        {"doc": "tutorial/example.md", "options": "ln[1:2] hl[2]"},
        {"doc": "tutorial/example.md", "options": ""},
    ]
    for record in manifest["files"]:
        assert (output / record["output_path"]).read_bytes() == b"x = 1\r\ny = 2\r\n"
        assert (
            record["url"]
            == f"https://raw.githubusercontent.com/fastapi/fastapi/{collect.SOURCE_COMMIT}/"
            + record["repo_path"]
        )
    assert (output / "docs/tutorial__example.md").read_bytes() == doc.read_bytes()


@pytest.mark.parametrize("change", ["unstaged", "staged", "deleted", "hidden-modification"])
def test_changed_input_is_rejected(snapshot, tmp_path, capsys, change):
    doc = snapshot / "docs/en/docs/tutorial/example.md"
    if change == "deleted":
        doc.unlink()
    else:
        doc.write_bytes(b"changed" * 100)
    if change == "staged":
        git(snapshot, "add", ".")
    if change == "hidden-modification":
        git(snapshot, "update-index", "--assume-unchanged", "docs/en/docs/tutorial/example.md")
    output = tmp_path / "raw"
    assert collect.main(["--snapshot-dir", str(snapshot), "--output-dir", str(output)]) == 1
    diagnostic = capsys.readouterr()
    assert diagnostic.err
    assert "Collected" not in diagnostic.out
    assert not (output / "manifest.json").exists()
    assert not (output / "code_manifest.json").exists()


@pytest.mark.parametrize(
    "target", ["existing", "checkout", "raw", "normalized", "alias-raw", "dangling"]
)
def test_output_refusal_preserves_existing_files(snapshot, tmp_path, monkeypatch, target, capsys):
    data_root = tmp_path / "data"
    (data_root / "raw").mkdir(parents=True)
    (data_root / "normalized").mkdir()
    monkeypatch.setattr(collect, "DATA_ROOT", data_root)
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep.txt").write_bytes(b"prior candidate")
    alias = tmp_path / "alias"
    alias.symlink_to(data_root / "raw", target_is_directory=True)
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "missing")
    output = {
        "existing": existing,
        "checkout": snapshot / "candidate",
        "raw": data_root / "raw/new",
        "normalized": data_root / "normalized/new",
        "alias-raw": alias / "new",
        "dangling": dangling,
    }[target]
    assert collect.main(["--snapshot-dir", str(snapshot), "--output-dir", str(output)]) == 1
    assert capsys.readouterr().err
    assert (existing / "keep.txt").read_bytes() == b"prior candidate"
    assert not (output / "manifest.json").exists()


@pytest.mark.parametrize("bad_input", ["subdirectory", "non-repository", "missing"])
def test_input_must_be_checkout_root(snapshot, tmp_path, bad_input, capsys):
    path = {
        "subdirectory": snapshot / "docs",
        "non-repository": tmp_path,
        "missing": tmp_path / "missing",
    }[bad_input]
    assert collect.main(["--snapshot-dir", str(path), "--output-dir", str(tmp_path / "raw")]) == 1
    assert str(path) in capsys.readouterr().err


@pytest.mark.parametrize("scenario", ["empty", "collision", "document-symlink", "invalid-utf8"])
def test_bad_document_selection_leaves_no_manifests(
    snapshot, tmp_path, monkeypatch, scenario, capsys
):
    doc = snapshot / "docs/en/docs/tutorial/example.md"
    if scenario == "empty":
        doc.write_bytes(b"small")
    elif scenario == "collision":
        nested = doc.parent / "a/b.md"
        nested.parent.mkdir()
        nested.write_bytes(b"x" * 500)
        (doc.parent / "a__b.md").write_bytes(b"y" * 500)
    elif scenario == "document-symlink":
        doc.unlink()
        doc.symlink_to(tmp_path / "external.md")
        (tmp_path / "external.md").write_bytes(b"x" * 600)
    else:
        doc.write_bytes(b"\xff" * 600)
    git(snapshot, "add", ".")
    git(snapshot, "-c", "commit.gpgsign=false", "commit", "-qm", "bad docs")
    monkeypatch.setattr(collect, "SOURCE_COMMIT", git(snapshot, "rev-parse", "HEAD"))
    output = tmp_path / "raw"
    assert collect.main(["--snapshot-dir", str(snapshot), "--output-dir", str(output)]) == 1
    assert capsys.readouterr().err
    assert not (output / "manifest.json").exists()
    assert not (output / "code_manifest.json").exists()
    # The incomplete directory is diagnostic evidence and cannot be reused.
    assert output.is_dir()
    assert collect.main(["--snapshot-dir", str(snapshot), "--output-dir", str(output)]) == 1
    assert "new directory" in capsys.readouterr().err


@pytest.mark.parametrize(
    "reference",
    [
        "../../docs_src/missing.py",
        "../../tests/example.py",
        "../../docs_src/../../outside.py",
        "/docs_src/example.py",
        "../../docs_src/../fastapi/example.py",
        "../../docs_src//example.py",
    ],
)
def test_missing_or_unsafe_reference_fails(snapshot, tmp_path, monkeypatch, reference, capsys):
    doc = snapshot / "docs/en/docs/tutorial/example.md"
    doc.write_bytes(doc.read_bytes() + f"{{* {reference} *}}".encode())
    git(snapshot, "add", ".")
    git(snapshot, "-c", "commit.gpgsign=false", "commit", "-qm", "reference")
    monkeypatch.setattr(collect, "SOURCE_COMMIT", git(snapshot, "rev-parse", "HEAD"))
    output = tmp_path / "raw"
    assert collect.main(["--snapshot-dir", str(snapshot), "--output-dir", str(output)]) == 1
    diagnostic = capsys.readouterr()
    assert "Collected" not in diagnostic.out
    assert reference in diagnostic.err or "docs_src/missing.py" in diagnostic.err
    assert not (output / "manifest.json").exists()
    assert not (output / "code_manifest.json").exists()


@pytest.mark.parametrize("scenario", ["untracked", "symlink", "unreadable", "hidden-modification"])
def test_reference_must_be_readable_regular_pinned_file(
    snapshot, tmp_path, monkeypatch, scenario, capsys
):
    doc = snapshot / "docs/en/docs/tutorial/example.md"
    doc.write_bytes(doc.read_bytes() + b"{!> ../../docs_src/example.py !}")
    code = snapshot / "docs_src/example.py"
    code.parent.mkdir()
    code.write_bytes(b"print(1)\n")
    if scenario == "symlink":
        code.unlink()
        code.symlink_to(doc)
    git(snapshot, "add", "docs")
    if scenario != "untracked":
        git(snapshot, "add", "docs_src")
    git(snapshot, "-c", "commit.gpgsign=false", "commit", "-qm", "example")
    monkeypatch.setattr(collect, "SOURCE_COMMIT", git(snapshot, "rev-parse", "HEAD"))
    if scenario == "hidden-modification":
        code.write_bytes(b"print(2)\n")
        git(snapshot, "update-index", "--assume-unchanged", "docs_src/example.py")
    if scenario == "unreadable":
        original_read = Path.read_bytes

        def fail_read(path):
            if path == code:
                raise PermissionError(f"{path}: simulated unreadable example")
            return original_read(path)

        monkeypatch.setattr(Path, "read_bytes", fail_read)
    output = tmp_path / "raw"
    assert collect.main(["--snapshot-dir", str(snapshot), "--output-dir", str(output)]) == 1
    assert "docs_src/example.py" in capsys.readouterr().err
    assert not (output / "manifest.json").exists()
    assert not (output / "code_manifest.json").exists()


@pytest.mark.parametrize("failure", ["copy", "manifest", "short-copy"])
def test_write_failure_cannot_leave_success_pair(snapshot, tmp_path, monkeypatch, failure, capsys):
    output = tmp_path / "raw"
    original_bytes = Path.write_bytes
    original_text = Path.write_text

    def write_bytes(path, data):
        if path.is_relative_to(output):
            if failure == "copy":
                raise OSError(f"{path}: simulated disk full")
            if failure == "short-copy":
                return original_bytes(path, data[:-1])
        return original_bytes(path, data)

    def write_text(path, data, *args, **kwargs):
        if failure == "manifest" and path == output / "code_manifest.json":
            original_text(path, "{partial")
            raise OSError(f"{path}: simulated disk full")
        return original_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    monkeypatch.setattr(Path, "write_text", write_text)
    assert collect.main(["--snapshot-dir", str(snapshot), "--output-dir", str(output)]) == 1
    diagnostic = capsys.readouterr()
    assert diagnostic.err and "Collected" not in diagnostic.out
    assert not (output / "manifest.json").exists()
    assert not (output / "code_manifest.json").exists()


def test_checkout_newline_filter_cannot_change_source_bytes(
    snapshot, tmp_path, monkeypatch, capsys
):
    doc = snapshot / "docs/en/docs/tutorial/example.md"
    (snapshot / ".gitattributes").write_text("*.md text eol=crlf\n")
    git(snapshot, "add", ".")
    git(snapshot, "-c", "commit.gpgsign=false", "commit", "-qm", "newline filter")
    monkeypatch.setattr(collect, "SOURCE_COMMIT", git(snapshot, "rev-parse", "HEAD"))
    doc.unlink()
    git(snapshot, "checkout", "--", "docs/en/docs/tutorial/example.md")
    assert b"\r\n" in doc.read_bytes()
    assert not git(snapshot, "status", "--porcelain", "--untracked-files=no")
    output = tmp_path / "raw"
    assert collect.main(["--snapshot-dir", str(snapshot), "--output-dir", str(output)]) == 1
    assert "checkout bytes differ" in capsys.readouterr().err
    assert not (output / "manifest.json").exists()


def test_rejects_symlinked_source_parent(snapshot, tmp_path, capsys):
    directory = snapshot / "docs/en/docs/tutorial"
    external = tmp_path / "external"
    directory.rename(external)
    directory.symlink_to(external, target_is_directory=True)
    output = tmp_path / "raw"
    assert collect.main(["--snapshot-dir", str(snapshot), "--output-dir", str(output)]) == 1
    assert capsys.readouterr().err
    assert not (output / "manifest.json").exists()


def test_all_top_level_allowlist_documents_and_no_checkout_mutation(
    snapshot, tmp_path, monkeypatch
):
    names = [
        "async.md",
        "environment-variables.md",
        "python-types.md",
        "virtual-environments.md",
        "fastapi-cli.md",
        "features.md",
        "editor-support.md",
    ]
    for name in names:
        (snapshot / "docs/en/docs" / name).write_bytes(b"x" * 500)
    git(snapshot, "add", ".")
    git(snapshot, "-c", "commit.gpgsign=false", "commit", "-qm", "allowlist")
    monkeypatch.setattr(collect, "SOURCE_COMMIT", git(snapshot, "rev-parse", "HEAD"))
    before = {
        str(p.relative_to(snapshot)): p.read_bytes() for p in snapshot.rglob("*") if p.is_file()
    }
    output = tmp_path / "raw"
    assert collect.main(["--snapshot-dir", str(snapshot), "--output-dir", str(output)]) == 0
    after = {
        str(p.relative_to(snapshot)): p.read_bytes() for p in snapshot.rglob("*") if p.is_file()
    }
    assert before == after
    manifest = json.loads((output / "manifest.json").read_text())
    assert {r["source_path"] for r in manifest["files"]} == {*names, "tutorial/example.md"}
    code = json.loads((output / "code_manifest.json").read_text())
    assert code["files"] == [] and code["stats"]["total_files"] == 0


def test_operational_git_failure_is_reported(snapshot, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", str(tmp_path / "no-executables"))
    assert (
        collect.main(["--snapshot-dir", str(snapshot), "--output-dir", str(tmp_path / "raw")]) == 1
    )
    assert "git" in capsys.readouterr().err
