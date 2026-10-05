"""Fetch the code examples referenced by the collected FastAPI docs.

The docs embed example source via MkDocs include macros, e.g.

    {* ../../docs_src/stream_data/tutorial001_py310.py ln[1:23] hl[20,23] *}
    {! ../../docs_src/templates/templates/item.html !}

Those referenced files (mostly `docs_src/*.py`) are NOT part of the docs
markdown, so the docs alone leave dangling placeholders. This script scans the
already-collected docs, resolves every reference to a repo path, and fetches
each file **at the same pinned commit** the docs came from, writing them under:

    data/fastapi/raw/github-docs/v1/
      code/<repo_path>          e.g. code/docs_src/stream_data/tutorial001_py310.py
      code_manifest.json        provenance + which docs reference each file

Run the docs collector (`collect_data_from_fastapi_github.py`) first; this reads
its manifest to stay pinned to the same snapshot.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

# --- Locate the docs snapshot -------------------------------------------------
SOURCE_ID = "github-docs"
SOURCE_VERSION = "v1"

DATA_ROOT = Path(__file__).resolve().parent.parent / "data" / "fastapi"
VERSION_DIR = DATA_ROOT / "raw" / SOURCE_ID / SOURCE_VERSION
DOCS_DIR = VERSION_DIR / "docs"
DOCS_MANIFEST = VERSION_DIR / "manifest.json"

CODE_DIRNAME = "code"
CODE_DIR = VERSION_DIR / CODE_DIRNAME
CODE_MANIFEST = VERSION_DIR / "code_manifest.json"
MANIFEST_SCHEMA_VERSION = 1

# --- Reference parsing --------------------------------------------------------
# `{* PATH <options> *}` — options are things like `ln[1:23] hl[20,23]`.
MACRO_RE = re.compile(r"\{\*\s*(\S+)\s*([^*]*?)\s*\*\}")
# `{! PATH !}` and `{!> PATH !}` — legacy includes.
LEGACY_RE = re.compile(r"\{!>?\s*(\S+?)\s*!\}")

# References resolve to repo-root paths under these top-level dirs.
ALLOWED_ROOTS = ("docs_src/", "fastapi/")

RAW_TIMEOUT = 30
RAW_RETRIES = 2


def repo_path(reference: str) -> str:
    """Resolve a macro reference to a repo-root-relative path.

    FastAPI's include base is the repo root, so the leading `../` sequence is a
    fixed convention: strip it and the remainder is the real path.
    """
    ref = reference.replace("\\", "/").lstrip("./")
    while ref.startswith("../"):
        ref = ref[3:]
    return ref


def iter_references(text: str):
    """Yield (repo_path, options) for every resolvable include in a doc."""
    for match in MACRO_RE.finditer(text):
        yield repo_path(match.group(1)), match.group(2).strip()
    for match in LEGACY_RE.finditer(text):
        yield repo_path(match.group(1)), ""


def collect_references() -> dict[str, list[dict]]:
    """Map repo_path -> list of {doc, options} backlinks, across all docs."""
    manifest = json.loads(DOCS_MANIFEST.read_text(encoding="utf-8"))
    source_of = {f["output_filename"]: f["source_path"] for f in manifest["files"]}

    references: dict[str, list[dict]] = defaultdict(list)
    for md_path in sorted(DOCS_DIR.glob("*.md")):
        doc = source_of.get(md_path.name, md_path.name)
        for path, options in iter_references(md_path.read_text(encoding="utf-8")):
            if not path.startswith(ALLOWED_ROOTS):
                continue
            references[path].append({"doc": doc, "options": options})
    return references


def raw_url(repo: str, commit: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{commit}/{path}"


def fetch_bytes(url: str) -> bytes:
    last_error: Exception | None = None
    for _attempt in range(RAW_RETRIES):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "fastapi-docs-collector"})
            with urllib.request.urlopen(request, timeout=RAW_TIMEOUT) as response:
                return response.read()
        except urllib.error.URLError as error:  # includes HTTPError
            last_error = error
    raise last_error  # type: ignore[misc]


def write_code_manifest(source: dict, files: list[dict], failures: list[dict]) -> None:
    total_bytes = sum(record["bytes"] for record in files)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "kind": "referenced-code",
        "source": {
            "id": SOURCE_ID,
            "version": SOURCE_VERSION,
            "repo": source["repo"],
            "repo_url": source["repo_url"],
            "ref": source["ref"],
        },
        "referenced_from": DOCS_MANIFEST.name,
        "collection": {
            "fetched_at": datetime.now(UTC).isoformat(),
            "method": "raw.githubusercontent",
            "code_dir": CODE_DIRNAME,
        },
        "stats": {
            "total_files": len(files),
            "total_bytes": total_bytes,
            "failures": len(failures),
            "by_ext": dict(Counter(record["ext"] for record in files)),
        },
        "files": sorted(files, key=lambda record: record["repo_path"]),
        "failures": sorted(failures, key=lambda record: record["repo_path"]),
    }
    CODE_MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    source = json.loads(DOCS_MANIFEST.read_text(encoding="utf-8"))["source"]
    repo, commit = source["repo"], source["ref"]

    references = collect_references()
    total = len(references)
    print(f"resolved {total} distinct referenced files from {DOCS_DIR}")

    files: list[dict] = []
    failures: list[dict] = []

    for index, path in enumerate(sorted(references), start=1):
        url = raw_url(repo, commit, path)
        try:
            data = fetch_bytes(url)
        except Exception as error:  # noqa: BLE001 - record and continue
            failures.append({"repo_path": path, "url": url, "error": str(error)})
            print(f"[{index}/{total}] FAIL {path}: {error}")
            continue

        target = CODE_DIR / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

        files.append(
            {
                "repo_path": path,
                "output_path": f"{CODE_DIRNAME}/{path}",
                "url": url,
                "ext": Path(path).suffix,
                "bytes": len(data),
                "referenced_by": references[path],
            }
        )
        if index % 25 == 0 or index == total:
            print(f"[{index}/{total}] fetched")

    write_code_manifest(source, files, failures)
    total_kb = sum(record["bytes"] for record in files) / 1024
    print(f"\nwrote {len(files)} files ({total_kb:.1f} KB) to {CODE_DIR}")
    if failures:
        print(f"{len(failures)} failures (see code_manifest.json)")
    print(f"manifest written: {CODE_MANIFEST}")


if __name__ == "__main__":
    main()
