"""
Prove that loading the FastAPI docs leaves their markdown structure untouched.

The earlier loader (``MarkdownToDocument`` with the default ``DocumentCleaner``) lost
three things the indexing study depends on: headings, code
fences, and the indentation inside code blocks. ``data_ingestion.loading`` is the
fix; these tests are the proof, and they are written so they cannot quietly stop
proving it:

- every document in the normalized corpus is compared line for line, before and
  after loading, on all three counts, and the loaded count is checked against the
  corpus manifest, which the loader never reads;
- a small in-test sample runs the same comparison, so the suite still means
  something on a checkout without the corpus (``data/`` is git-ignored);
- two tests pin the earlier loader's settings down as broken, so nobody can restore
  ``MarkdownToDocument`` or the default ``DocumentCleaner`` and still see green.

Run offline::

    uv run pytest tests/data_ingestion/test_loading.py
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))  # lets pytest import the project from any directory

from data_ingestion.loading import (  # noqa: E402  (needs the path set above)
    FILENAME_META_KEY,
    build_cleaner,
    build_converter,
    find_doc_files,
    load_documents,
)
from data_ingestion.metadata import (  # noqa: E402  (as above)
    CORPUS_ROOT,
    MANIFEST_PATH,
    load_manifest,
)

# The normalized corpus (D30). Git-ignored, so its tests skip when it is absent. How many
# documents it holds is read from its manifest (``MANIFEST_PATH``), never written here;
# both come from ``CORPUS_ROOT``, so the count and the docs always describe one corpus.
CORPUS_DIR = CORPUS_ROOT / "docs"

# A heading line: up to three spaces of indent, then one to six '#' markers.
HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?:\s|$)")
# A fence line: any indent, then a run of at least three backticks or tildes.
FENCE_RE = re.compile(r"^\s*(?:`{3,}|~{3,})")
# Leading whitespace, which is what indentation means for a line of code.
INDENT_RE = re.compile(r"^[ \t]*")


# A stand-in for one corpus file: headings at three levels, a Python block whose
# comment must not be mistaken for a heading, a block whose own leading spaces are
# content, an indented list, and blank lines between all of it.
SAMPLE_DOC = """\
# Sample Page { #sample-page }

An intro paragraph with a `code span` in it.

## A Python block { #a-python-block }

```python
# not a heading: this is a comment inside a fence
def handler():
    if True:
        return {"ok": True}
```

A paragraph after the block, separated by a blank line.

### An indented list { #an-indented-list }

- first item
    - nested item
      continuation line

```text
    these leading spaces are part of the content

    and so is the blank line above
```
"""

SAMPLE_FILENAME = "sample__page.md"


@dataclass(frozen=True)
class MarkdownStructure:
    """The three things loading must not change, pulled out of a markdown document."""

    heading_lines: list[str]  # lines that open an ATX heading
    fence_lines: list[str]  # lines that open or close a code fence
    indentation: list[str]  # leading whitespace of every line


def read_structure(text: str) -> MarkdownStructure:
    """Return ``text``'s heading lines, fence lines, and per-line indentation.

    Deliberately stateless: it never tracks whether a line sits inside a fence. So a
    ``#`` comment in a Python block counts as a heading line, and indentation is
    recorded for prose as well as code. Both make the comparison stricter rather than
    looser, and neither can drift out of phase.

    That last part is the point. The corpus really does contain malformed fences:
    ``advanced__templates.md`` wraps a ````html```` block inside a ````jinja```` one
    and closes both with two bare ```` ``` ```` lines, three times over. A parser that
    tracks fence state goes out of phase inside each doubled region and misses the
    headings there (2 of 8 in that file, see F007) — silently turning this test's
    headline check into a no-op over exactly the region where something went wrong.
    """
    lines = text.splitlines()
    return MarkdownStructure(
        heading_lines=[line for line in lines if HEADING_RE.match(line)],
        fence_lines=[line for line in lines if FENCE_RE.match(line)],
        indentation=[INDENT_RE.match(line).group() for line in lines],
    )


def describe_structure_loss(source: str, loaded: str) -> list[str]:
    """Return one line per structural difference between ``source`` and ``loaded``.

    The source is stripped first, because trimming the document's outer edges is the
    one change loading is allowed to make (D24). Without it every corpus file would
    be reported as different, since they end in a newline and the loaded text does
    not, which ``splitlines()`` turns into a trailing indentation entry. That nothing
    *else* changes is checked by byte equality, in the tests below.
    """
    before = read_structure(source.strip())
    after = read_structure(loaded)
    return [
        describe_difference(label, was, now)
        for label, was, now in (
            ("headings", before.heading_lines, after.heading_lines),
            ("code fences", before.fence_lines, after.fence_lines),
            ("indentation", before.indentation, after.indentation),
        )
        if was != now
    ]


def describe_difference(label: str, was: list[str], now: list[str]) -> str:
    """Describe how two line lists differ, counts plus the first line that changed.

    The counts alone are not enough: a change that rewrites a heading without adding
    or removing one would otherwise be reported as ``headings 3 -> 3``, which reads
    like a false positive.
    """
    summary = f"{label} {len(was)} -> {len(now)}"
    for index, (old, new) in enumerate(zip(was, now)):  # noqa: B905  (the tail is reported by the counts)
        if old != new:
            return f"{summary}, first change at #{index + 1}: {old!r} -> {new!r}"
    return summary


@pytest.fixture
def sample_dir(tmp_path: Path) -> Path:
    """A one-file docs folder holding ``SAMPLE_DOC``."""
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / SAMPLE_FILENAME).write_text(SAMPLE_DOC, encoding="utf-8")
    return folder


# --------------------------------------------------------------------------- #
# The in-test sample: the same checks, without the git-ignored corpus.
# --------------------------------------------------------------------------- #


def test_sample_has_structure_worth_checking() -> None:
    """Guard against a sample so bland that the comparisons below prove nothing."""
    structure = read_structure(SAMPLE_DOC)
    # Three real headings, plus the '#' comment the stateless parser also counts.
    assert len(structure.heading_lines) == 4
    assert len(structure.fence_lines) == 4  # two blocks, opened and closed
    assert any(indent for indent in structure.indentation)  # some lines are indented


def test_sample_keeps_headings_fences_and_indentation(sample_dir: Path) -> None:
    document = load_documents(sample_dir)[0]
    assert describe_structure_loss(SAMPLE_DOC, document.content) == []


def test_sample_changes_only_at_its_outer_edges(sample_dir: Path) -> None:
    """Paragraph breaks, newlines, and indentation all survive; only the edges are trimmed."""
    document = load_documents(sample_dir)[0]
    assert document.content == SAMPLE_DOC.strip()
    assert "\n\n" in document.content  # blank lines between paragraphs


def test_sample_carries_its_file_name(sample_dir: Path) -> None:
    document = load_documents(sample_dir)[0]
    assert document.meta[FILENAME_META_KEY] == SAMPLE_FILENAME


def test_documents_come_back_in_file_name_order(sample_dir: Path) -> None:
    for name in ("advanced__zeta.md", "advanced__alpha.md"):
        (sample_dir / name).write_text("# Heading\n", encoding="utf-8")
    names = [document.meta[FILENAME_META_KEY] for document in load_documents(sample_dir)]
    assert names == sorted(names)


def test_missing_or_empty_folder_is_reported(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Docs folder not found"):
        find_doc_files(tmp_path / "absent")

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="No '\\*.md' files"):
        find_doc_files(empty)


def test_an_unreadable_file_is_reported_by_name(sample_dir: Path) -> None:
    """A file the converter skips must not become a silent hole in the corpus."""
    (sample_dir / "broken__encoding.md").write_bytes(b"# Heading\n\xff\xfe not utf-8\n")

    with pytest.raises(RuntimeError, match=r"1 of 2 file\(s\).*broken__encoding\.md"):
        load_documents(sample_dir)


# --------------------------------------------------------------------------- #
# The earlier loader's settings, pinned down as broken so they cannot come back unnoticed.
# --------------------------------------------------------------------------- #


def test_default_cleaner_settings_would_destroy_the_structure(sample_dir: Path) -> None:
    """``DocumentCleaner``'s defaults flatten indentation and drop blank lines (D24)."""
    from haystack.components.preprocessors import DocumentCleaner

    converted = build_converter().run(sources=sorted(sample_dir.glob("*.md")))["documents"]
    default_cleaner = DocumentCleaner(remove_empty_lines=True, remove_extra_whitespaces=True)
    damaged = default_cleaner.run(documents=converted)["documents"][0].content

    assert describe_structure_loss(SAMPLE_DOC, damaged) != []
    assert "\n\n" not in damaged  # paragraph breaks gone
    assert "    if True:" not in damaged  # code indentation collapsed


def test_markdown_converter_would_drop_headings_and_fences(sample_dir: Path) -> None:
    """``MarkdownToDocument`` renders markdown to prose, losing the markers (D14)."""
    from haystack.components.converters import MarkdownToDocument

    rendered = MarkdownToDocument().run(sources=sorted(sample_dir.glob("*.md")))["documents"][0]
    structure = read_structure(rendered.content)

    # Every real heading marker is gone; what survives is the '#' comment from inside
    # the Python block, now loose in the prose and indistinguishable from a heading.
    assert structure.heading_lines == ["# not a heading: this is a comment inside a fence"]
    assert structure.fence_lines == []


# --------------------------------------------------------------------------- #
# The real corpus: every document its manifest lists.
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def corpus_documents() -> dict[str, str]:
    """The whole normalized corpus, loaded once, keyed by file name."""
    if not CORPUS_DIR.is_dir():
        pytest.skip(f"normalized corpus not found at {CORPUS_DIR} (data/ is git-ignored)")
    return {
        document.meta[FILENAME_META_KEY]: document.content
        for document in load_documents(CORPUS_DIR)
    }


@pytest.fixture(scope="module")
def expected_corpus_size() -> int:
    """How many documents the corpus manifest lists.

    Normalization rewrites the manifest with the docs on every run, so the count follows
    the corpus from pull to pull. It never clears old docs, though: a doc the manifest
    has dropped but the folder still holds fails the count, which is the right call.
    A manifest that is present but malformed raises rather than skips.
    """
    if not MANIFEST_PATH.is_file():
        pytest.skip(f"corpus manifest not found at {MANIFEST_PATH} (data/ is git-ignored)")
    return len(load_manifest(MANIFEST_PATH).entries)


def test_corpus_loads_every_file(
    corpus_documents: dict[str, str], expected_corpus_size: int
) -> None:
    """As many documents as the manifest lists, named as the docs folder names them.

    The count is the check that does not lean on the loader: ``find_doc_files`` is the
    loader's own file discovery, so a file it misses drops out of both sides of the
    name comparison and only the manifest still expects it.
    """
    assert len(corpus_documents) == expected_corpus_size
    assert sorted(corpus_documents) == sorted(path.name for path in find_doc_files(CORPUS_DIR))


def test_corpus_has_structure_worth_checking(
    corpus_documents: dict[str, str], expected_corpus_size: int
) -> None:
    """Guard against the corpus comparison passing because there is nothing to lose.

    Per document, not summed over the corpus: a total would stay above zero even if
    the parser had stopped seeing all but one file.
    """
    structures = {name: read_structure(text) for name, text in corpus_documents.items()}

    without_headings = [name for name, s in structures.items() if not s.heading_lines]
    assert not without_headings, f"no heading found in: {', '.join(without_headings)}"

    with_fences = [name for name, s in structures.items() if s.fence_lines]
    assert len(with_fences) > expected_corpus_size // 2

    with_indentation = [name for name, s in structures.items() if any(s.indentation)]
    assert len(with_indentation) > expected_corpus_size // 2


def test_every_corpus_document_keeps_its_structure(corpus_documents: dict[str, str]) -> None:
    """Headings, code fences, and code indentation are identical before and after loading."""
    failures = []
    for path in find_doc_files(CORPUS_DIR):
        losses = describe_structure_loss(
            path.read_text(encoding="utf-8"), corpus_documents[path.name]
        )
        if losses:
            failures.append(f"{path.name}: {'; '.join(losses)}")

    assert not failures, (
        f"{len(failures)} of {len(corpus_documents)} documents lost structure:\n"
        + "\n".join(failures)
    )


def test_every_corpus_document_changes_only_at_its_outer_edges(
    corpus_documents: dict[str, str],
) -> None:
    """The cleaner trims the file's edges and rewrites nothing else (D24)."""
    failures = [
        path.name
        for path in find_doc_files(CORPUS_DIR)
        if corpus_documents[path.name] != path.read_text(encoding="utf-8").strip()
    ]
    assert not failures, f"{len(failures)} document(s) were rewritten: {', '.join(failures)}"


def test_cleaner_settings_stay_structure_preserving() -> None:
    """The three settings this module's promise rests on."""
    cleaner = build_cleaner()
    assert cleaner.remove_empty_lines is False
    assert cleaner.remove_extra_whitespaces is False
    assert cleaner.strip_whitespaces is True
