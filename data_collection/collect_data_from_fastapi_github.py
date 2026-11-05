"""Collect FastAPI documentation from the fastapi/fastapi GitHub repo.

This is one *source* collector. It writes a self-contained snapshot under
`data/fastapi/raw/github-docs/<version>/` with a manifest that follows the
shared per-source contract, so the curation step can merge it with other
sources (e.g. the docs website) without special-casing this one.

    data/fastapi/raw/github-docs/v1/
      manifest.json          provenance + per-file records (shared contract)
      ingest/full_ingest.txt untouched gitingest output (single blob)
      docs/<flattened>.md     per-document markdown, split from the ingest blob
"""

import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from gitingest import ingest

# --- Source identity (shared per-source contract) -----------------------------
SOURCE_ID = "github-docs"
SOURCE_TYPE = "git"
SOURCE_VERSION = "v1"

# --- Source (pinned for reproducibility) --------------------------------------
# `v1` is a fixed snapshot of the FastAPI docs at a specific commit. Re-running
# this script must reproduce the same corpus, so we pin a commit SHA rather than
# a moving branch like `master`. Bump SOURCE_COMMIT and SOURCE_VERSION together
# when re-collecting a newer snapshot (v2, v3, ...).
REPO = "fastapi/fastapi"
REPO_URL = f"https://github.com/{REPO}"
SOURCE_COMMIT = "6a6002298106d89d3a65fd62527dc0bdc4e681fd"
DOCS_PREFIX = Path("docs/en/docs")
SOURCE_URL = f"{REPO_URL}/tree/{SOURCE_COMMIT}/{DOCS_PREFIX.as_posix()}"

# --- Output layout ------------------------------------------------------------
DATA_ROOT = Path(__file__).resolve().parent.parent / "data" / "fastapi"
VERSION_DIR = DATA_ROOT / "raw" / SOURCE_ID / SOURCE_VERSION
INGEST_DIR = VERSION_DIR / "ingest"
DOCS_DIR = VERSION_DIR / "docs"
FULL_INGEST_FILE = INGEST_DIR / "full_ingest.txt"
MANIFEST_FILE = VERSION_DIR / "manifest.json"
MANIFEST_SCHEMA_VERSION = 1

# --- Filtering ----------------------------------------------------------------
MIN_FILE_BYTES = 500
GITINGEST_SEPARATOR = "=" * 48

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
TOP_LEVEL_BUCKET = "top-level"


def include_patterns() -> set[str]:
    patterns = set()
    prefix = DOCS_PREFIX.as_posix()

    for subdir in INCLUDED_SUBDIRS:
        patterns.add(f"{prefix}/{subdir}/*.md")
        patterns.add(f"{prefix}/{subdir}/**/*.md")

    for filename in TOP_LEVEL_ALLOWLIST:
        patterns.add(f"{prefix}/{filename}")

    return patterns


def docs_relative_path(path_text: str) -> Path:
    path_text = path_text.replace("\\", "/")
    prefix = DOCS_PREFIX.as_posix() + "/"

    if path_text.startswith(prefix):
        path_text = path_text[len(prefix) :]

    return Path(path_text)


def is_allowed_markdown(relative_path: Path) -> bool:
    path_parts = relative_path.parts

    if relative_path.suffix != ".md":
        return False

    if len(path_parts) == 1:
        return relative_path.name in TOP_LEVEL_ALLOWLIST

    return path_parts[0] in INCLUDED_SUBDIRS


def bucket_for(relative_path: Path) -> str:
    return relative_path.parts[0] if len(relative_path.parts) > 1 else TOP_LEVEL_BUCKET


def source_url_for(relative_path: Path) -> str:
    return f"{REPO_URL}/blob/{SOURCE_COMMIT}/{(DOCS_PREFIX / relative_path).as_posix()}"


def iter_ingested_files(content: str):
    header_pattern = re.compile(
        rf"^{re.escape(GITINGEST_SEPARATOR)}\nFILE: (.*?)\n"
        rf"{re.escape(GITINGEST_SEPARATOR)}\n",
        re.MULTILINE,
    )
    matches = list(header_pattern.finditer(content))

    for index, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        markdown = content[body_start:body_end]
        if markdown.endswith("\n\n"):
            markdown = markdown[:-2]
        yield match.group(1), markdown


def output_filename(relative_path: Path) -> str:
    return relative_path.as_posix().replace("/", "__")


def write_manifest(files: list[dict], breakdown: dict[str, dict[str, int]]) -> None:
    total_files = sum(bucket["files"] for bucket in breakdown.values())
    total_bytes = sum(bucket["bytes"] for bucket in breakdown.values())

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "source": {
            "id": SOURCE_ID,
            "type": SOURCE_TYPE,
            "version": SOURCE_VERSION,
            "repo": REPO,
            "repo_url": REPO_URL,
            "ref": SOURCE_COMMIT,
            "docs_path": DOCS_PREFIX.as_posix(),
            "ref_url": SOURCE_URL,
        },
        "collection": {
            "fetched_at": datetime.now(UTC).isoformat(),
            "tool": "gitingest",
            "ingest_file": FULL_INGEST_FILE.relative_to(VERSION_DIR).as_posix(),
            "docs_dir": DOCS_DIR.relative_to(VERSION_DIR).as_posix(),
            "min_file_bytes": MIN_FILE_BYTES,
            "included_subdirs": list(INCLUDED_SUBDIRS),
            "top_level_allowlist": sorted(TOP_LEVEL_ALLOWLIST),
            "include_patterns": sorted(include_patterns()),
        },
        "stats": {
            "total_files": total_files,
            "total_bytes": total_bytes,
            "by_bucket": {
                bucket: breakdown[bucket]
                for bucket in (TOP_LEVEL_BUCKET, *INCLUDED_SUBDIRS)
                if bucket in breakdown
            },
        },
        "files": sorted(files, key=lambda record: record["output_filename"]),
    }

    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def print_summary(breakdown: dict[str, dict[str, int]]) -> None:
    total_files = sum(bucket["files"] for bucket in breakdown.values())
    total_bytes = sum(bucket["bytes"] for bucket in breakdown.values())

    print(f"total files written: {total_files}")
    print(f"total size: {total_bytes / 1024:.1f} KB")
    print("breakdown by subdirectory:")

    for bucket in (TOP_LEVEL_BUCKET, *INCLUDED_SUBDIRS):
        stats = breakdown.get(bucket)
        if not stats:
            continue
        print(f"  {bucket}: {stats['files']} files, {stats['bytes'] / 1024:.1f} KB")


def main() -> None:
    INGEST_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    summary, tree, content = ingest(SOURCE_URL, include_patterns=include_patterns())
    full_ingest = f"{summary}\n\n{tree}\n{content}"
    FULL_INGEST_FILE.write_text(full_ingest, encoding="utf-8")

    files: list[dict] = []
    breakdown = defaultdict(lambda: {"files": 0, "bytes": 0})

    for path_text, markdown in iter_ingested_files(content):
        relative_path = docs_relative_path(path_text)
        markdown_bytes = len(markdown.encode("utf-8"))

        if not is_allowed_markdown(relative_path) or markdown_bytes < MIN_FILE_BYTES:
            continue

        out_name = output_filename(relative_path)
        (DOCS_DIR / out_name).write_text(markdown, encoding="utf-8")

        bucket = bucket_for(relative_path)
        breakdown[bucket]["files"] += 1
        breakdown[bucket]["bytes"] += markdown_bytes
        files.append(
            {
                "output_filename": out_name,
                "source_path": relative_path.as_posix(),
                "bucket": bucket,
                "url": source_url_for(relative_path),
                "bytes": markdown_bytes,
            }
        )

    write_manifest(files, dict(breakdown))
    print_summary(dict(breakdown))
    print(f"manifest written: {MANIFEST_FILE}")


if __name__ == "__main__":
    main()
