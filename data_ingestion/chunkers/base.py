"""
Shared chunk fields for every chunking strategy in the FastAPI Q&A RAG project.

The chunking study compares seven strategies on the same corpus, so the chunks
they produce have to be described the same way whatever splitter made them. Each
strategy declares its identity and its sizes in a :class:`ChunkSettings`, and
lets the helpers here turn split texts into documents:

    strategy        the strategy name, as listed in ``chunkers.STRATEGIES``
    chunk_index     0-based position within the parent document
    chunk_count     how many chunks that parent produced
    source_path     the parent's source path, carried through unchanged
    chunk_size      the split length the strategy used
    chunk_overlap   the overlap the strategy used
    chunk_unit      what those two are counted in ("word", "sentence", "char")

Everything the parent carried in ``meta`` is copied onto each of its chunks; the
bookkeeping a Haystack splitter adds (``source_id``, ``split_id``,
``split_idx_start``, ``_split_overlap``, ``page_number``, ``parent_id``) is
dropped, so the seven strategies are directly comparable and a chunk's metadata
does not depend on which splitter produced it. Indexing derives deterministic
point ids from ``strategy`` + ``source_path`` + ``chunk_index``, which is why
:func:`split_documents` feeds the splitter one parent at a time: the index is
then a position inside that parent and never shifts because a neighbouring
document changed.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from haystack import Document

logger = logging.getLogger(__name__)

# What ``chunk_size``/``chunk_overlap`` are counted in.
WORD_UNIT = "word"
SENTENCE_UNIT = "sentence"
CHAR_UNIT = "char"

# Written by a Haystack splitter, not by us: internal to one splitter and
# meaningless (or absent) for the others, so it never reaches a chunk.
_SPLITTER_META_KEYS = frozenset(
    {"source_id", "parent_id", "split_id", "split_idx_start", "_split_overlap", "page_number"}
)


@dataclass(frozen=True)
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


class Splitter(Protocol):
    """The part of a Haystack splitter component the helpers here rely on.

    Both Haystack splitters warm themselves up on their first ``run``, so
    :func:`split_documents` only ever calls ``run``.
    """

    def run(self, documents: list[Document]) -> dict[str, list[Document]]: ...


class Chunker(Protocol):
    """What every strategy in this package offers its caller.

    ``get_chunker`` returns this, so a chunker fetched by name has a known shape
    without the package importing any strategy module.
    """

    settings: ChunkSettings

    def warm_up(self) -> None: ...

    def run(self, documents: list[Document]) -> dict[str, list[Document]]: ...


def source_path_of(document: Document) -> str | None:
    """The parent's source path, or ``None`` when it has not been given one.

    Corpus metadata sets ``source_path``; a document read straight off disk by
    ``TextFileToDocument`` only has ``file_path`` (Haystack 3.0 records the file
    name there, not the path it was read from), so fall back to that to keep the
    demos and a bare loader usable before metadata is attached.
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
    """Turn one parent's split texts into chunk documents carrying the shared fields.

    Strategies that do their own splitting (rather than handing a Haystack
    splitter to :func:`split_documents`) call this directly, and may pass
    ``per_chunk_meta`` to add a field of their own to each chunk — a heading
    path, for instance. Those extras are merged last, so a strategy can also
    override a carried-over parent field on purpose.

    Chunks are rebuilt as new documents rather than edited in place: Haystack
    derives a document's id from its content and metadata when it is created, so
    a chunk that was mutated afterwards would carry an id computed from the
    splitter's metadata instead of its own.
    """
    if per_chunk_meta is not None and len(per_chunk_meta) != len(texts):
        raise ValueError(
            f"per_chunk_meta has {len(per_chunk_meta)} entries for {len(texts)} chunks of "
            f"{source_path_of(parent) or parent.id}"
        )

    carried = {key: value for key, value in parent.meta.items() if key not in _SPLITTER_META_KEYS}
    source_path = source_path_of(parent)
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
    """Refuse a batch whose documents cannot be told apart by their source path.

    Indexing builds a chunk's point id from ``strategy`` + ``source_path`` +
    ``chunk_index``, so two documents sharing a source path would produce two
    chunks with the same id and the second would overwrite the first — a whole
    document quietly missing from a collection. Two documents with no source
    path at all collide the same way, but that is a warning rather than an
    error: a document built in memory (a demo, a test, an ad-hoc run) has no
    path to carry, and only indexing needs one.

    Raises ``ValueError`` naming the repeated path. Strategies that split for
    themselves call this before :func:`build_chunks`; :func:`split_documents`
    already does.
    """
    seen: set[str] = set()
    repeated: list[str] = []
    missing = 0
    for document in documents:
        path = source_path_of(document)
        if path is None:
            missing += 1
        elif path in seen:
            repeated.append(path)
        else:
            seen.add(path)

    if repeated:
        raise ValueError(
            f"{strategy}: two documents share a source path, so their chunks would collide: "
            f"{', '.join(sorted(set(repeated)))}"
        )
    if missing:
        logger.warning(
            "%s: %d document(s) have neither source_path nor file_path; their chunks "
            "cannot be told apart by source",
            strategy,
            missing,
        )


def split_documents(
    documents: list[Document],
    splitter: Splitter,
    settings: ChunkSettings,
) -> list[Document]:
    """Split every document with ``splitter`` and stamp the shared chunk fields.

    The splitter sees one parent per call, so the chunks that come back are that
    parent's, in order: their ``chunk_index`` runs 0…n-1 with no gaps and does
    not depend on how many documents were passed in.

    A document with no content is skipped with a warning, the same way for every
    strategy — left to the splitters, one raises on ``content=None`` and another
    treats it as empty.
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
        if len(texts) < len(split):
            logger.info(
                "%s: dropped %d blank chunk(s) from %s",
                settings.strategy,
                len(split) - len(texts),
                name,
            )
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
    """One small markdown document with headings, paragraphs, and a code fence."""
    return Document(content=DEMO_MARKDOWN, meta={"source_path": "demo/query-parameters.md"})


def log_chunk_summary(chunks: Sequence[Document], *, log: logging.Logger | None = None) -> None:
    """Log one line per chunk: its index, its length, and how it starts."""
    log = log or logger
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
