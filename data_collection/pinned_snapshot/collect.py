"""Read a pinned local FastAPI checkout into a fresh raw candidate."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from data_collection.collect_referenced_code import LEGACY_RE, MACRO_RE

SOURCE_COMMIT = "6a6002298106d89d3a65fd62527dc0bdc4e681fd"
REPO = "fastapi/fastapi"
REPO_URL = f"https://github.com/{REPO}"
DOCS_PREFIX = "docs/en/docs/"
INCLUDED_SUBDIRS = ("tutorial", "advanced", "reference", "how-to", "deployment")
TOP_LEVEL_ALLOWLIST = {
    "async.md",
    "environment-variables.md",
    "python-types.md",
    "virtual-environments.md",
    "fastapi-cli.md",
    "features.md",
    "editor-support.md",
}
MIN_FILE_BYTES = 500
DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "fastapi"


def git(snapshot: Path, *args: str) -> bytes:
    """Run a read-only Git query, preserving stderr for the CLI diagnostic."""
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(snapshot), *args], capture_output=True
    )
    if result.returncode:
        raise ValueError(f"{snapshot}: git {args[0]}: {result.stderr.decode().strip()}")
    return result.stdout


def read_source(snapshot: Path, path: str, tracked: dict[str, tuple[str, str]]) -> bytes:
    """Read only regular tracked files whose bytes equal the pinned Git blob."""
    if path not in tracked:
        raise ValueError(f"{path}: not a tracked file in the pinned snapshot")
    mode, blob = tracked[path]
    if mode not in ("100644", "100755"):
        raise ValueError(f"{path}: not a regular file (Git mode {mode})")
    source = snapshot / path
    for part in (source, *source.parents):
        if part == snapshot:
            break
        if part.is_symlink():
            raise ValueError(f"{path}: symlink at {part}")
    data = source.read_bytes()
    if data != git(snapshot, "cat-file", "blob", blob):
        raise ValueError(f"{path}: checkout bytes differ from pinned blob")
    return data


def collect_snapshot(snapshot: Path, output: Path) -> None:
    tracked = {}
    for entry in git(snapshot, "ls-tree", "-r", "-z", SOURCE_COMMIT).split(b"\0"):
        if entry:
            metadata, path = entry.decode("utf-8").split("\t", 1)
            mode, _kind, blob = metadata.split()
            tracked[path] = (mode, blob)

    output.mkdir(parents=True)
    (output / "docs").mkdir()
    (output / "code").mkdir()
    files = []
    names = set()
    references = defaultdict(list)
    breakdown = defaultdict(lambda: {"files": 0, "bytes": 0})
    for path in sorted(tracked):
        if not path.startswith(DOCS_PREFIX):
            continue
        relative = Path(path[len(DOCS_PREFIX) :])
        if relative.suffix != ".md":
            continue
        if len(relative.parts) == 1:
            if relative.name not in TOP_LEVEL_ALLOWLIST:
                continue
            bucket = "top-level"
        else:
            bucket = relative.parts[0]
            if bucket not in INCLUDED_SUBDIRS:
                continue
        data = read_source(snapshot, path, tracked)
        if len(data) < MIN_FILE_BYTES:
            continue
        name = relative.as_posix().replace("/", "__")
        if name in names:
            raise ValueError(f"{path}: flattened filename collision: {name}")
        names.add(name)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"{path}: document is not UTF-8: {error}") from error
        for pattern in (MACRO_RE, LEGACY_RE):
            for match in pattern.finditer(text):
                reference = match.group(1).replace("\\", "/")
                # MkDocs includes use leading ../ as a repo-root convention.
                while reference.startswith(("../", "./")):
                    reference = reference.split("/", 1)[1]
                parts = reference.split("/")
                if parts[0] not in ("docs_src", "fastapi") or any(
                    part in ("", ".", "..") for part in parts
                ):
                    raise ValueError(f"{path}: unsupported or escaping include {match.group(1)}")
                options = match.group(2).strip() if pattern is MACRO_RE else ""
                references[reference].append({"doc": relative.as_posix(), "options": options})
        (output / "docs" / name).write_bytes(data)
        breakdown[bucket]["files"] += 1
        breakdown[bucket]["bytes"] += len(data)
        files.append(
            {
                "output_filename": name,
                "source_path": relative.as_posix(),
                "bucket": bucket,
                "url": f"{REPO_URL}/blob/{SOURCE_COMMIT}/{path}",
                "bytes": len(data),
            }
        )
    if not files:
        raise ValueError(f"{snapshot}: no documents meet the selection rules")
    code_files = []
    for path, backlinks in sorted(references.items()):
        data = read_source(snapshot, path, tracked)
        target = output / "code" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        code_files.append(
            {
                "repo_path": path,
                "output_path": f"code/{path}",
                "url": f"https://raw.githubusercontent.com/{REPO}/{SOURCE_COMMIT}/{path}",
                "ext": Path(path).suffix,
                "bytes": len(data),
                "referenced_by": backlinks,
            }
        )
    expected = {f"docs/{r['output_filename']}": r["bytes"] for r in files}
    expected.update({r["output_path"]: r["bytes"] for r in code_files})
    actual = {
        p.relative_to(output).as_posix(): p.stat().st_size for p in output.rglob("*") if p.is_file()
    }
    if actual != expected:
        raise ValueError(f"{output}: copied file membership or byte totals differ")
    source = {
        "id": "github-docs",
        "type": "git",
        "version": "v1",
        "repo": REPO,
        "repo_url": REPO_URL,
        "ref": SOURCE_COMMIT,
    }
    manifest = {
        "schema_version": 1,
        "source": {
            **source,
            "docs_path": DOCS_PREFIX.rstrip("/"),
            "ref_url": f"{REPO_URL}/tree/{SOURCE_COMMIT}/{DOCS_PREFIX.rstrip('/')}",
        },
        "collection": {
            "fetched_at": datetime.now(UTC).isoformat(),
            "tool": "direct-snapshot",
            "docs_dir": "docs",
            "min_file_bytes": MIN_FILE_BYTES,
            "included_subdirs": list(INCLUDED_SUBDIRS),
            "top_level_allowlist": sorted(TOP_LEVEL_ALLOWLIST),
        },
        "stats": {
            "total_files": len(files),
            "total_bytes": sum(r["bytes"] for r in files),
            "by_bucket": dict(breakdown),
        },
        "files": sorted(files, key=lambda r: r["output_filename"]),
    }
    code_manifest = {
        "schema_version": 1,
        "kind": "referenced-code",
        "source": {k: v for k, v in source.items() if k != "type"},
        "referenced_from": "manifest.json",
        "collection": {
            "fetched_at": datetime.now(UTC).isoformat(),
            "method": "direct-snapshot",
            "code_dir": "code",
        },
        "stats": {
            "total_files": len(code_files),
            "total_bytes": sum(r["bytes"] for r in code_files),
            "failures": 0,
            "by_ext": dict(Counter(r["ext"] for r in code_files)),
        },
        "files": code_files,
        "failures": [],
    }
    try:
        (output / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        (output / "code_manifest.json").write_text(
            json.dumps(code_manifest, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        (output / "manifest.json").unlink(missing_ok=True)
        (output / "code_manifest.json").unlink(missing_ok=True)
        raise
    print(f"Collected {len(files)} documents and {len(code_files)} referenced files into {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        snapshot = args.snapshot_dir.resolve(strict=True)
        output = args.output_dir.resolve()
        root = Path(git(snapshot, "rev-parse", "--show-toplevel").decode().strip()).resolve()
        if root != snapshot:
            raise ValueError(f"{snapshot}: must be the checkout root {root}")
        commit = git(snapshot, "rev-parse", "HEAD").decode().strip()
        if commit != SOURCE_COMMIT:
            raise ValueError(f"{snapshot}: expected commit {SOURCE_COMMIT}, got {commit}")
        if git(snapshot, "status", "--porcelain", "--untracked-files=no"):
            raise ValueError(f"{snapshot}: staged or unstaged tracked changes")
        for protected in (snapshot, DATA_ROOT / "raw", DATA_ROOT / "normalized"):
            if output.is_relative_to(protected.resolve()):
                raise ValueError(f"{output}: output is inside protected tree {protected}")
        if args.output_dir.is_symlink() or output.exists():
            raise ValueError(f"{output}: output must be a new directory")
        collect_snapshot(snapshot, output)
    except (OSError, ValueError) as error:
        print(f"Collection failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
