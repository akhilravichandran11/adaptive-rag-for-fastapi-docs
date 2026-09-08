"""Normalize the raw FastAPI GitHub docs into self-contained markdown.

The raw docs embed example code via MkDocs include macros instead of inline code:

    {* ../../docs_src/stream_data/tutorial001_py310.py ln[1:23] hl[20,23] *}
    {! ../../docs_src/templates/templates/item.html !}

This step does what MkDocs does at build time: it replaces each macro with a
fenced code block containing the referenced code, honoring the `ln[...]` line
selection (1-based, inclusive) and dropping `hl[...]` (a visual-only highlight).
Every block is prefixed with a `# <repo_path>` comment so the citation survives
into the chunk text.

    reads:  data/fastapi/raw/github-docs/v1/{docs,code} + manifest.json + code_manifest.json
    writes: data/fastapi/normalized/github-docs/v1/{docs/*.md, manifest.json}

Raw is left untouched (immutable provenance); this is a derived stage.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SOURCE_ID = "github-docs"
SOURCE_VERSION = "v1"

DATA_ROOT = Path(__file__).resolve().parent.parent / "data" / "fastapi"
RAW_DIR = DATA_ROOT / "raw" / SOURCE_ID / SOURCE_VERSION
RAW_DOCS = RAW_DIR / "docs"
RAW_CODE = RAW_DIR / "code"
DOCS_MANIFEST = RAW_DIR / "manifest.json"
CODE_MANIFEST = RAW_DIR / "code_manifest.json"

NORM_DIR = DATA_ROOT / "normalized" / SOURCE_ID / SOURCE_VERSION
NORM_DOCS = NORM_DIR / "docs"
NORM_MANIFEST = NORM_DIR / "manifest.json"
MANIFEST_SCHEMA_VERSION = 1

# `{* PATH <options> *}` (options like `ln[1:23] hl[20,23]`) and legacy `{! PATH !}`.
MACRO_RE = re.compile(r"\{\*\s*(\S+)\s*([^*]*?)\s*\*\}")
LEGACY_RE = re.compile(r"\{!\s*(\S+?)\s*!\}")
LN_RE = re.compile(r"ln\[([0-9:,]+)\]")

LANG_BY_EXT = {".py": "python", ".html": "html", ".css": "css"}


def repo_path(reference: str) -> str:
    """Resolve a macro reference to its repo-root path (strip the `../` prefix)."""
    ref = reference.replace("\\", "/").lstrip("./")
    while ref.startswith("../"):
        ref = ref[3:]
    return ref


def select_lines(all_lines: list[str], ln_body: str) -> list[str]:
    """Select 1-based inclusive line segments, e.g. '1:2,12:16,29' -> those lines."""
    selected: list[str] = []
    for segment in ln_body.split(","):
        segment = segment.strip()
        if ":" in segment:
            start, end = (int(part) for part in segment.split(":", 1))
            selected.extend(all_lines[start - 1 : end])
        elif segment:
            index = int(segment)
            selected.extend(all_lines[index - 1 : index])
    return selected


def comment_line(lang: str, path: str) -> str:
    if lang == "html":
        return f"<!-- {path} -->"
    if lang == "css":
        return f"/* {path} */"
    return f"# {path}"


def render_block(path: str, options: str, code_text: str) -> str:
    lang = LANG_BY_EXT.get(Path(path).suffix, Path(path).suffix.lstrip(".") or "text")
    all_lines = code_text.splitlines()

    ln_match = LN_RE.search(options)
    lines = select_lines(all_lines, ln_match.group(1)) if ln_match else all_lines

    body = "\n".join([comment_line(lang, path), *lines])
    return f"```{lang}\n{body}\n```"


def normalize_text(text: str, includes: list[dict]) -> str:
    """Replace every include macro in `text`, recording each into `includes`."""

    def replace(reference: str, options: str) -> str:
        path = repo_path(reference)
        code_file = RAW_CODE / path
        record = {"repo_path": path, "options": options.strip(), "resolved": False}
        includes.append(record)
        if not code_file.is_file():
            return f"<!-- unresolved include: {path} -->"
        record["resolved"] = True
        return render_block(path, options, code_file.read_text(encoding="utf-8"))

    text = MACRO_RE.sub(lambda m: replace(m.group(1), m.group(2)), text)
    text = LEGACY_RE.sub(lambda m: replace(m.group(1), ""), text)
    return text


def write_manifest(source: dict, files: list[dict], breakdown: dict[str, dict[str, int]]) -> None:
    total_files = sum(bucket["files"] for bucket in breakdown.values())
    total_bytes = sum(bucket["bytes"] for bucket in breakdown.values())
    total_includes = sum(len(record["includes"]) for record in files)
    unresolved = sum(
        1 for record in files for inc in record["includes"] if not inc["resolved"]
    )

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "stage": "normalized",
        "source": {
            "id": source["id"],
            "type": source["type"],
            "version": source["version"],
            "repo": source["repo"],
            "repo_url": source["repo_url"],
            "ref": source["ref"],
        },
        "derived_from": {
            "docs_manifest": DOCS_MANIFEST.name,
            "code_manifest": CODE_MANIFEST.name,
        },
        "normalization": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "inlined_includes": total_includes - unresolved,
            "unresolved_includes": unresolved,
        },
        "stats": {
            "total_files": total_files,
            "total_bytes": total_bytes,
            "by_bucket": breakdown,
        },
        "files": files,
    }
    NORM_MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    NORM_DOCS.mkdir(parents=True, exist_ok=True)

    docs_manifest = json.loads(DOCS_MANIFEST.read_text(encoding="utf-8"))
    source = docs_manifest["source"]

    files: list[dict] = []
    breakdown: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "bytes": 0})

    for record in sorted(docs_manifest["files"], key=lambda r: r["output_filename"]):
        raw_text = (RAW_DOCS / record["output_filename"]).read_text(encoding="utf-8")
        includes: list[dict] = []
        normalized = normalize_text(raw_text, includes)

        (NORM_DOCS / record["output_filename"]).write_text(normalized, encoding="utf-8")

        byte_size = len(normalized.encode("utf-8"))
        bucket = record["bucket"]
        breakdown[bucket]["files"] += 1
        breakdown[bucket]["bytes"] += byte_size
        files.append(
            {
                "output_filename": record["output_filename"],
                "source_path": record["source_path"],
                "bucket": bucket,
                "url": record["url"],
                "bytes": byte_size,
                "includes": includes,
            }
        )

    write_manifest(source, files, dict(breakdown))

    total_files = len(files)
    total_kb = sum(r["bytes"] for r in files) / 1024
    inlined = sum(1 for r in files for i in r["includes"] if i["resolved"])
    unresolved = sum(1 for r in files for i in r["includes"] if not i["resolved"])
    print(f"normalized {total_files} docs ({total_kb:.1f} KB) -> {NORM_DOCS}")
    print(f"inlined includes: {inlined}, unresolved: {unresolved}")
    print(f"manifest written: {NORM_MANIFEST}")


if __name__ == "__main__":
    main()
