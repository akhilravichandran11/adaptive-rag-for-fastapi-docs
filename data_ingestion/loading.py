"""
Load the normalized FastAPI docs without losing their markdown structure.

This is the first stage of indexing: the files in
``data/fastapi/normalized/github-docs/v1/docs/`` are read as plain text and cleaned
just enough to index, and no further. Headings, code fences, blank lines, and the
indentation inside code blocks all survive, because every later stage leans on them
— the markdown_ast chunker splits on headings and fences, the recursive chunker
splits on blank lines, and a code block whose indentation was flattened is no longer
runnable Python for whoever reads the answer.

Two Haystack defaults are what destroy that structure, so this module avoids both
(plan decisions D14 and D24):

- ``MarkdownToDocument`` renders markdown to plain prose, which drops the ``#``
  markers and the fences entirely. ``TextFileToDocument`` is used instead.
- ``DocumentCleaner`` removes empty lines and collapses runs of whitespace by
  default, which erases paragraph breaks and code indentation.

The one cleanup kept is ``strip_whitespaces``: it trims whitespace at the very start
and end of a document and changes nothing in between.

Usage::

    from pathlib import Path

    from data_ingestion.loading import load_documents

    documents = load_documents(Path("data/fastapi/normalized/github-docs/v1/docs"))
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

from haystack import Document
from haystack.components.converters import TextFileToDocument
from haystack.components.preprocessors import DocumentCleaner

logger = logging.getLogger(__name__)

# Which files in a docs folder are loaded. The normalized corpus is markdown only.
DOCS_GLOB = "*.md"

# Metadata key holding the document's file name. The normalization manifest and the
# curation run both key their entries on this name, so the join done later (IDX-03)
# needs it on every loaded document.
FILENAME_META_KEY = "output_filename"


def build_converter() -> TextFileToDocument:
    """Return the converter that reads a docs file as UTF-8 text, markers intact.

    ``store_full_path=False`` keeps the git-ignored absolute corpus path out of the
    metadata; the file name is attached separately under ``FILENAME_META_KEY``.
    """
    return TextFileToDocument(encoding="utf-8", store_full_path=False)


def build_cleaner() -> DocumentCleaner:
    """Return the structure-preserving cleaner (D24).

    Every option that rewrites the inside of a document is off. Only the document's
    own leading and trailing whitespace is trimmed, which no chunker depends on.
    """
    return DocumentCleaner(
        remove_empty_lines=False,  # blank lines are paragraph and chunk boundaries
        remove_extra_whitespaces=False,  # indentation inside code fences is content
        remove_repeated_substrings=False,
        strip_whitespaces=True,  # the one cleanup: trim the file's outer edges
    )


def find_doc_files(docs_dir: Path, *, pattern: str = DOCS_GLOB) -> list[Path]:
    """Return the docs files in ``docs_dir``, sorted so runs are reproducible.

    :param docs_dir: folder holding the normalized docs.
    :param pattern: glob selecting the files to load.
    :raises FileNotFoundError: if the folder is missing or holds no matching file.
    """
    if not docs_dir.is_dir():
        raise FileNotFoundError(f"Docs folder not found: {docs_dir}")
    paths = sorted(path for path in docs_dir.glob(pattern) if path.is_file())
    if not paths:
        raise FileNotFoundError(f"No '{pattern}' files in docs folder: {docs_dir}")
    return paths


def load_documents(docs_dir: Path, *, pattern: str = DOCS_GLOB) -> list[Document]:
    """Load and clean every docs file in ``docs_dir``, structure intact.

    :param docs_dir: folder holding the normalized docs.
    :param pattern: glob selecting the files to load.
    :returns: one ``Document`` per file, in file-name order, each carrying its file
        name under ``FILENAME_META_KEY``.
    :raises FileNotFoundError: if the folder is missing or holds no matching file.
    :raises RuntimeError: if any file could not be read. A silently skipped file
        would leave a hole in the indexed corpus, so this fails loudly instead.
    """
    paths = find_doc_files(docs_dir, pattern=pattern)
    logger.info("Loading %d file(s) from %s", len(paths), docs_dir)

    sources: list[str | Path] = list(paths)
    meta = [{FILENAME_META_KEY: path.name} for path in paths]
    documents = build_converter().run(sources=sources, meta=meta)["documents"]

    if len(documents) != len(paths):
        # Counted, not a set membership test: a recursive ``pattern`` can put two
        # files with the same name in ``paths``, and naming the wrong one (or none)
        # in the error would send the reader looking in the wrong place.
        loaded = Counter(document.meta[FILENAME_META_KEY] for document in documents)
        unread = []
        for path in paths:
            if loaded[path.name]:
                loaded[path.name] -= 1
            else:
                unread.append(path.name)
        raise RuntimeError(
            f"{len(unread)} of {len(paths)} file(s) in {docs_dir} could not be read "
            f"(the converter logged the reason): {', '.join(unread)}"
        )

    documents = build_cleaner().run(documents=documents)["documents"]
    logger.info("Loaded %d document(s)", len(documents))
    return documents
