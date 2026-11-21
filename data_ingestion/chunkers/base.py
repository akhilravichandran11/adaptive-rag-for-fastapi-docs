"""
Shared chunk fields, so every strategy the study compares describes its chunks
the same way whatever splitter made them.
"""

import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from haystack import Document

logger = logging.getLogger(__name__)

WORD_UNIT = "word"
SENTENCE_UNIT = "sentence"
CHAR_UNIT = "char"

# Only a parent that is itself a splitter's output carries these, and they describe
# that earlier split. A new split's own meta is never copied onto a chunk, only its text.
_SPLITTER_META_KEYS = frozenset(
    {"source_id", "parent_id", "split_id", "split_idx_start", "_split_overlap", "page_number"}
)


@dataclass
class ChunkSettings:
    """How one strategy splits, recorded on every chunk it produces.

    ``size`` and ``overlap`` are counted in ``unit``. A strategy owns these
    values in its own module rather than reading them from the environment, so
    a variant means the same thing in every run of the study.
    """

    strategy: str
    size: int
    overlap: int
    unit: str


def source_path_of(document: Document) -> str | None:
    """``source_path`` from corpus metadata, else ``file_path``.

    A document read by ``TextFileToDocument`` has only ``file_path`` (the file name, in
    Haystack 3) until metadata is attached.
    """
    for key in ("source_path", "file_path"):
        value = document.meta.get(key)
        if value:
            return str(value)
    return None


def build_chunks(
    parent: Document,
    texts: Sequence[str],
    settings: ChunkSettings,
    *,
    per_chunk_meta: Sequence[Mapping[str, Any]] | None = None,
) -> list[Document]:
    """Turn one parent's split texts into chunks carrying the shared fields.

    ``per_chunk_meta`` adds a strategy's own field to each chunk, such as a heading path.
    Each chunk is a new ``Document``, not an edited split: Haystack fixes the id from the
    content and meta at creation, so an edited split would keep the splitter's id.
    """
    source_path = source_path_of(parent)
    if per_chunk_meta is not None and len(per_chunk_meta) != len(texts):
        raise ValueError(
            f"per_chunk_meta has {len(per_chunk_meta)} entries for {len(texts)} chunks of "
            f"{source_path or parent.id}"
        )

    carried = {key: value for key, value in parent.meta.items() if key not in _SPLITTER_META_KEYS}
    chunk_count = len(texts)

    chunks: list[Document] = []
    for index, text in enumerate(texts):
        meta: dict[str, Any] = dict(carried)
        meta.update(
            strategy=settings.strategy,
            chunk_index=index,
            chunk_count=chunk_count,
            source_path=source_path,
            chunk_size=settings.size,
            chunk_overlap=settings.overlap,
            chunk_unit=settings.unit,
        )
        if per_chunk_meta is not None:
            meta.update(per_chunk_meta[index])
        chunks.append(Document(content=text, meta=meta))
    return chunks


def check_source_paths(documents: Sequence[Document], strategy: str) -> None:
    """Refuse two documents with the same source path.

    Indexing keys a chunk's point by its source path and ``chunk_index`` (``point_key``
    in stage 3), so the second document's chunks would overwrite the first's. A missing
    path only warns: a document built in memory has none, and only indexing needs one.
    """
    paths = [source_path_of(document) for document in documents]
    counts = Counter(path for path in paths if path is not None)
    repeated = sorted(path for path, count in counts.items() if count > 1)
    if repeated:
        raise ValueError(
            f"{strategy}: two documents share a source path, so their chunks would collide: "
            f"{', '.join(repeated)}"
        )
    missing = paths.count(None)
    if missing:
        logger.warning(
            "%s: %d document(s) have neither source_path nor file_path; their chunks "
            "cannot be told apart by source",
            strategy,
            missing,
        )


def split_documents(
    documents: list[Document],
    splitter,
    settings: ChunkSettings,
) -> list[Document]:
    """Split each document with a Haystack ``splitter`` and stamp the shared chunk fields.

    One parent per ``run`` call, so ``chunk_index`` is a position inside that parent and
    never shifts when a neighbour changes; ``run`` also warms the splitter up. An empty
    document is skipped here because, left to them, the splitters disagree on ``None``.
    """
    if not isinstance(documents, list):
        raise TypeError(f"Expected a list of Documents, got {type(documents).__name__}")
    check_source_paths(documents, settings.strategy)

    chunks: list[Document] = []
    for parent in documents:
        name = source_path_of(parent) or parent.id
        if not parent.content:
            logger.warning("%s: %s has no content; skipping it", settings.strategy, name)
            continue

        split = splitter.run([parent])["documents"]
        # A chunk of nothing but whitespace carries no meaning and would still
        # cost an embedding, so drop it before the indices are handed out.
        texts = [chunk.content for chunk in split if chunk.content and chunk.content.strip()]
        dropped = len(split) - len(texts)
        if dropped:
            logger.info("%s: dropped %d blank chunk(s) from %s", settings.strategy, dropped, name)
        if not texts:
            logger.warning("%s produced no chunks for %s", settings.strategy, name)
            continue
        chunks.extend(build_chunks(parent, texts, settings))
    return chunks


# --- demo support -----------------------------------------------------------
# Each strategy module ends with a short ``python -m`` demo. They all show the
# same thing — what this strategy does to one small markdown document — so the
# sample and the summary live here instead of being repeated in every module.

DEMO_MARKDOWN = """# Query Parameters

When you declare function parameters that are not part of the path, FastAPI
reads them from the query string. The declaration is an ordinary Python
parameter with a type hint, and the type hint is what tells the framework how to
convert and check the value that arrives.

## Defaults make a parameter optional

A parameter with a default value may be left out of the request. The handler
below accepts a page of items and falls back to the first ten when the client
asks for nothing in particular:

```python
@app.get("/items/")
async def read_items(skip: int = 0, limit: int = 10):
    return fake_items_db[skip : skip + limit]
```

Query strings carry text and nothing else, so the values start out as strings
and are converted to the declared type before the handler runs. A request for
`/items/?skip=20&limit=5` therefore reaches the function with two integers, not
two strings, and `/items/` alone reaches it with the defaults.

## Required parameters

Leave the default out and the parameter becomes required. A request that omits
it never reaches the handler: the client gets a 422 response naming the field
that was missing, alongside the location the framework looked for it in. This is
the same validation machinery that checks a request body, applied to the query
string, which is why the error shape is the same in both cases.

## Optional parameters that have no default

A parameter can be genuinely optional without a useful fallback. Declare it as
`str | None` with a default of `None` and the handler can tell the difference
between a value that was not sent and a value that was sent empty:

```python
@app.get("/items/{item_id}")
async def read_item(item_id: str, q: str | None = None):
    if q is None:
        return {"item_id": item_id}
    return {"item_id": item_id, "q": q}
```

## Booleans and the shapes they accept

A parameter annotated `bool` is converted from the spellings clients actually
send. `?short=true`, `?short=True`, `?short=1`, `?short=on`, and `?short=yes`
all arrive as `True`, and their counterparts arrive as `False`. Anything else is
a validation error rather than a silently false value, so a typo in a query
string is reported instead of quietly changing what the endpoint returns.

## Several parameters at once

Path parameters and query parameters can be declared side by side, in any
order. FastAPI matches each declared parameter by name: the ones that appear in
the path template are taken from the path, and everything left over is read from
the query string.
"""


def demo_document() -> Document:
    return Document(content=DEMO_MARKDOWN, meta={"source_path": "demo/query-parameters.md"})


def log_chunk_summary(chunks: Sequence[Document], log: logging.Logger) -> None:
    for chunk in chunks:
        content = chunk.content or ""
        preview = " ".join(content.split())[:70]
        log.info(
            "%s chunk %d/%d | %d chars | %s",
            chunk.meta["strategy"],
            chunk.meta["chunk_index"] + 1,
            chunk.meta["chunk_count"],
            len(content),
            preview,
        )


def configure_demo_logging() -> None:
    """Send the demo's log lines to stderr, in the format the pipeline stages use."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
