"""
Prove that saved chunks read back unchanged, and that a chunk run from another corpus
build is refused.

``data_ingestion.chunk_snapshot`` is the handoff from stage 2 to stage 3 (D38): stage 2
writes each strategy's chunks to ``<run_dir>/chunks/<strategy>.jsonl`` and stage 3 reads
them back. Only its agreed seams are tested: ``write_chunks``, ``read_chunks`` and
``check_same_corpus``. The chunks are built here, by hand and with two free chunkers
(``naive_small`` and ``markdown_ast``, which need no cached model), so nothing reads the
corpus. Nothing here can reach the network, so the module installs no guard (C006).

Run offline::

    uv run pytest tests/data_ingestion/test_chunk_snapshot.py
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest
from haystack import Document

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))  # lets pytest import the project from any directory

from data_ingestion.chunk_snapshot import (  # noqa: E402  (needs the path set above)
    check_same_corpus,
    read_chunks,
    write_chunks,
)
from data_ingestion.chunkers import get_chunker  # noqa: E402

# The metadata corpus loading and enrichment put on a parent document.
PARENT_META = {
    "output_filename": "tutorial__body.md",
    "source_path": "docs/en/docs/tutorial/body.md",
    "bucket": "tutorial",
    "url": "https://fastapi.tiangolo.com/tutorial/body/",
    "corpus_commit": "abc1234",
    "quality_score": 8.5,
    "knowledge_domain": "Request handling",
    "curation_run": "20260101_000000",
}

MARKDOWN = """# Request Body

When you need to send data from a client to your API, you send it as a request body.
The café's "menu" — naïve or not — is read as UTF-8, and nothing is escaped away.

## Declare the model

```python
class Item(BaseModel):
    name: str
    price: float
```

The handler then receives an `Item`, already converted and validated.
"""


def hand_built_chunks() -> list[Document]:
    """Chunks whose metadata covers every plain JSON value a chunk carries."""
    return [
        Document(
            content=f"Chunk {index}: «quoted» text\nwith a newline, a tab\tand a \\ backslash",
            meta={
                **PARENT_META,
                "strategy": "toy",
                "chunk_index": index,
                "chunk_count": 3,
                "chunk_size": 200,
                "chunk_overlap": 25,
                "chunk_unit": "word",
                "heading_path": "Tutorial > Body > Nested Models",
                "percentile": 0.95,
                "optional": None,
                "flagged": False,
            },
        )
        for index in range(3)
    ]


def as_rows(chunks: list[Document]) -> list[tuple[str | None, dict]]:
    return [(chunk.content, chunk.meta) for chunk in chunks]


def write_manifest(tmp_path: Path, text: str) -> tuple[Path, str]:
    """A manifest file and the sha256 of its bytes, as stage 2 records it."""
    path = tmp_path / "manifest.json"
    path.write_text(text, encoding="utf-8")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


# --- write_chunks and read_chunks -----------------------------------------------------


def test_written_chunks_read_back_with_the_same_content_and_metadata_in_order(
    tmp_path: Path,
) -> None:
    chunks = hand_built_chunks()

    write_chunks(tmp_path, "toy", chunks)

    assert as_rows(read_chunks(tmp_path, "toy")) == as_rows(chunks)


@pytest.mark.parametrize("strategy", ["naive_small", "markdown_ast"])
def test_a_real_strategys_chunks_round_trip(tmp_path: Path, strategy: str) -> None:
    parent = Document(content=MARKDOWN, meta=dict(PARENT_META))
    chunks = get_chunker(strategy).run([parent])["documents"]
    assert chunks

    write_chunks(tmp_path, strategy, chunks)

    assert as_rows(read_chunks(tmp_path, strategy)) == as_rows(chunks)


def test_chunks_are_saved_one_json_object_per_line_under_chunks(tmp_path: Path) -> None:
    chunks = hand_built_chunks()

    path = write_chunks(tmp_path, "toy", chunks)

    assert path == tmp_path / "chunks" / "toy.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [set(row) for row in rows] == [{"content", "meta"}] * len(chunks)
    assert "«quoted»" in path.read_text(encoding="utf-8")  # stored as text, not \u escapes


def test_a_strategy_with_no_chunks_round_trips_as_an_empty_file(tmp_path: Path) -> None:
    path = write_chunks(tmp_path, "toy", [])

    assert path.read_text(encoding="utf-8") == ""
    assert read_chunks(tmp_path, "toy") == []


# --- check_same_corpus ---------------------------------------------------------------


def test_the_same_manifest_passes(tmp_path: Path) -> None:
    manifest, sha256 = write_manifest(tmp_path, '{"source": {"ref": "abc1234"}, "files": []}\n')

    check_same_corpus({"run_id": "20261012_101500", "manifest_sha256": sha256}, manifest)


def test_a_changed_manifest_is_refused_with_a_pointer_to_stage_2(tmp_path: Path) -> None:
    manifest, sha256 = write_manifest(tmp_path, '{"source": {"ref": "abc1234"}, "files": []}\n')
    # Same corpus commit, different build: the case only the file hash can see.
    manifest.write_text('{"source": {"ref": "abc1234"}, "files": [ ]}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="20261012_101500") as error:
        check_same_corpus({"run_id": "20261012_101500", "manifest_sha256": sha256}, manifest)
    assert "stage 2" in str(error.value)


def test_a_run_meta_without_a_manifest_hash_is_refused(tmp_path: Path) -> None:
    manifest, _ = write_manifest(tmp_path, "{}\n")

    with pytest.raises(ValueError, match="manifest_sha256"):
        check_same_corpus({"run_id": "20261012_101500", "stage": "index"}, manifest)
