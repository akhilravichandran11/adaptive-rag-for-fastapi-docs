"""
Saved chunks: the handoff from stage 2 (chunking) to stage 3 (indexing).

Stage 2 saves each strategy's chunks before any embedder sees them, and stage 3
indexes only from saved chunks (D38). Both embedders then index the same chunks, in
any order and across separate runs, so ``semantic__bge`` and ``semantic__voyage``
share one paid boundary pass (D29)::

    from data_ingestion.chunk_snapshot import check_same_corpus, read_chunks, write_chunks

    write_chunks(run_dir, "naive_small", chunks)       # stage 2
    check_same_corpus(run_meta, MANIFEST_PATH)          # stage 3, before reading
    chunks = read_chunks(run_dir, "naive_small")

A strategy's chunks go to ``<run_dir>/chunks/<strategy>.jsonl``, one
``{"content": ..., "meta": {...}}`` object per line, in the order the strategy
produced them. Chunk metadata is plain JSON values (``heading_path`` is a string), so
a chunk reads back with the same content and metadata. Haystack recomputes
``Document.id`` when it builds the chunk again; stage 3 sets its own point ids (D34),
so nothing depends on it.

A chunk run describes one build of the corpus. Stage 2 records the sha256 of the
corpus ``manifest.json`` in the run's ``run_meta.json``, and ``check_same_corpus``
refuses the run once today's manifest differs. The corpus commit cannot tell builds
apart (GDS-02 changed two documents without changing it), so the file hash does.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from haystack import Document

from data_ingestion.pipeline import CHUNKS_DIR


def _chunks_path(run_dir: Path, strategy: str) -> Path:
    """Where ``strategy``'s chunks are saved inside a run directory."""
    return run_dir / CHUNKS_DIR / f"{strategy}.jsonl"


def write_chunks(run_dir: Path, strategy: str, chunks: Iterable[Document]) -> Path:
    """Save ``chunks`` in order to ``<run_dir>/chunks/<strategy>.jsonl`` and return the path.

    Creates the folder if needed and replaces an earlier file for the same strategy.
    Non-ASCII text is written as UTF-8, not escaped, so the file reads as the docs do.
    """
    path = _chunks_path(run_dir, strategy)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as out:
        for chunk in chunks:
            row = {"content": chunk.content, "meta": chunk.meta}
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def read_chunks(run_dir: Path, strategy: str) -> list[Document]:
    """Read ``strategy``'s saved chunks back as documents, in the order they were saved."""
    with _chunks_path(run_dir, strategy).open(encoding="utf-8") as lines:
        return [
            Document(content=row["content"], meta=row["meta"]) for row in map(json.loads, lines)
        ]


def manifest_sha256(manifest_path: Path) -> str:
    """The sha256 of the corpus manifest's bytes, as lowercase hex: the corpus build's id."""
    return hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest()


def check_same_corpus(run_meta: Mapping[str, Any], manifest_path: Path) -> None:
    """Refuse a chunk run made from a different corpus build than today's.

    ``run_meta`` is the chunk run's ``run_meta.json``. Raises ``ValueError`` when its
    ``manifest_sha256`` differs from the sha256 of ``manifest_path`` now, or when it
    records none (it is not a chunk run's metadata).
    """
    run = run_meta.get("run_id", "?")
    recorded = run_meta.get("manifest_sha256")
    if not recorded:
        raise ValueError(
            f"run_meta.json of run {run} records no manifest_sha256; "
            "pass a chunk run made by stage 2 (stage2_chunk_corpus.py)"
        )
    current = manifest_sha256(manifest_path)
    if recorded != current:
        raise ValueError(
            f"The corpus changed since chunk run {run}: it was made from manifest sha256 "
            f"{recorded}, but {manifest_path} is now {current}. "
            "Run stage 2 (stage2_chunk_corpus.py) again and index the new chunk run."
        )
