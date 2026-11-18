"""
Offline tests for the size-based chunking strategies.

They run on small markdown fixtures built here — headings, paragraphs, and a
code fence — and never touch the corpus, the network, or an API key. The
``sentence`` and ``recursive`` strategies need NLTK's ``punkt_tab`` tokenizer
from the project model cache; when it has not been downloaded those cases skip
instead of failing.

    uv run pytest tests/data_ingestion/test_chunkers.py    # from the project root
"""

import sys
from pathlib import Path

import pytest
from haystack import Document

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data_ingestion.chunkers import (  # noqa: E402  (needs the path set above)
    ChunkSettings,
    build_chunks,
    get_chunker,
    strategy_names,
)
from data_ingestion.model_cache import register_nltk_data_dir  # noqa: E402

# The sizes each strategy is defined by: name -> (size, overlap, unit).
EXPECTED_SETTINGS = {
    "naive_small": (200, 25, "word"),
    "naive_medium": (400, 50, "word"),
    "naive_large": (800, 100, "word"),
    "sentence": (6, 2, "sentence"),
    "recursive": (2_000, 200, "char"),
}

# Strategies that cut on sentence boundaries, and so need the cached tokenizer.
NLTK_STRATEGIES = frozenset({"sentence", "recursive"})

# Metadata a Haystack splitter writes for its own bookkeeping; chunks must not
# carry it, so that every strategy describes its chunks the same way.
SPLITTER_META_KEYS = ("source_id", "parent_id", "split_id", "split_idx_start", "_split_overlap")

PARENT_META = {
    "source_path": "docs/tutorial__body.md",
    "bucket": "tutorial",
    "url": "https://example.invalid/tutorial/body/",
    "overall_quality": 4.5,
}


def punkt_is_cached() -> bool:
    """Whether NLTK's sentence tokenizer is in the project model cache."""
    import nltk

    register_nltk_data_dir()
    try:
        nltk.data.find("tokenizers/punkt_tab")
    except LookupError:
        return False
    return True


def markdown_fixture(sections: int = 20) -> str:
    """A markdown document with headings, paragraphs, and code fences.

    Long enough (over 1,000 words) that even the 800-word strategy splits it
    into several chunks, and every section is worded differently so a chunk can
    be traced back to where it came from.
    """
    parts = [
        "# Request Body\n\n"
        "When you need to send data from a client to your API, you send it as a "
        "request body, and FastAPI builds it into the model you declared.\n"
    ]
    for number in range(sections):
        parts.append(
            f"## Parameter group {number}\n\n"
            f"Group {number} declares its parameters with type hints, so the values "
            f"arriving as strings are converted and validated before the handler "
            f"runs. A value that cannot be converted produces a response naming "
            f"the field that failed, and group {number} is no exception to that.\n\n"
            "```python\n"
            f"@app.post"
            f'("/groups/{number}/items/")\n'
            f"async def create_item_{number}(item: Item, priority: int = {number}):\n"
            f'    return {{"group": {number}, "priority": priority, "item": item}}\n'
            "```\n\n"
            f"The handler for group {number} returns the item it was given together "
            f"with the priority that was resolved for it.\n"
        )
    return "\n".join(parts)


@pytest.fixture
def parent() -> Document:
    """One fixture document carrying the metadata corpus loading would attach."""
    return Document(content=markdown_fixture(), meta=dict(PARENT_META))


@pytest.fixture
def chunker(request: pytest.FixtureRequest):
    """A chunker built by name, skipped when its tokenizer is not cached."""
    name = request.param
    if name in NLTK_STRATEGIES and not punkt_is_cached():
        pytest.skip(f"{name} needs NLTK punkt_tab; run scripts/download_models.py first")
    return get_chunker(name)


# The size-based strategies only; semantic (and later markdown_ast) has its own
# test file, since it needs a cached model and does not cut where these do.
SIZE_BASED = list(EXPECTED_SETTINGS)

all_strategies = pytest.mark.parametrize("chunker", SIZE_BASED, indirect=True)


def test_every_strategy_is_registered_in_study_order() -> None:
    # The size-based strategies come first, in the order the study reports them.
    assert strategy_names()[: len(SIZE_BASED)] == SIZE_BASED


@pytest.mark.parametrize(("name", "expected"), sorted(EXPECTED_SETTINGS.items()))
def test_each_strategy_owns_its_sizes(name: str, expected: tuple[int, int, str]) -> None:
    if name in NLTK_STRATEGIES and not punkt_is_cached():
        pytest.skip(f"{name} needs NLTK punkt_tab; run scripts/download_models.py first")
    settings = get_chunker(name).settings
    assert (settings.strategy, settings.size, settings.overlap, settings.unit) == (name, *expected)


def test_unknown_strategy_name_raises_a_clear_error() -> None:
    with pytest.raises(ValueError) as error:
        get_chunker("naive_smal")
    message = str(error.value)
    assert "naive_smal" in message
    assert "naive_small" in message


@all_strategies
def test_chunks_carry_the_shared_fields(chunker, parent: Document) -> None:
    chunks = chunker.run([parent])["documents"]
    assert chunks
    expected_size, expected_overlap, expected_unit = EXPECTED_SETTINGS[chunker.settings.strategy]

    for position, chunk in enumerate(chunks):
        assert chunk.meta["strategy"] == chunker.settings.strategy
        assert chunk.meta["chunk_index"] == position
        assert chunk.meta["chunk_count"] == len(chunks)
        assert chunk.meta["source_path"] == PARENT_META["source_path"]
        assert chunk.meta["chunk_size"] == expected_size
        assert chunk.meta["chunk_overlap"] == expected_overlap
        assert chunk.meta["chunk_unit"] == expected_unit


@all_strategies
def test_chunks_keep_the_parent_metadata(chunker, parent: Document) -> None:
    for chunk in chunker.run([parent])["documents"]:
        for key, value in PARENT_META.items():
            assert chunk.meta[key] == value
        for key in SPLITTER_META_KEYS:
            assert key not in chunk.meta


@all_strategies
def test_chunk_index_runs_without_gaps_per_source_document(chunker) -> None:
    first = Document(content=markdown_fixture(6), meta={"source_path": "docs/one.md"})
    second = Document(content=markdown_fixture(9), meta={"source_path": "docs/two.md"})

    chunks = chunker.run([first, second])["documents"]

    for source_path in ("docs/one.md", "docs/two.md"):
        of_source = [c for c in chunks if c.meta["source_path"] == source_path]
        assert of_source, f"no chunks for {source_path}"
        assert [c.meta["chunk_index"] for c in of_source] == list(range(len(of_source)))
        assert {c.meta["chunk_count"] for c in of_source} == {len(of_source)}


@all_strategies
def test_the_same_input_gives_the_same_chunks(chunker, parent: Document) -> None:
    first = chunker.run([parent])["documents"]
    # A second, independently built chunker: determinism must not depend on
    # reusing one instance's state.
    second = get_chunker(chunker.settings.strategy).run([parent])["documents"]

    assert [(c.id, c.content, c.meta) for c in first] == [(c.id, c.content, c.meta) for c in second]


@all_strategies
def test_no_source_text_is_lost(chunker, parent: Document) -> None:
    chunked_words: set[str] = set()
    for chunk in chunker.run([parent])["documents"]:
        chunked_words.update(chunk.content.split())

    missing = set(parent.content.split()) - chunked_words
    assert not missing, f"words missing from every chunk: {sorted(missing)[:5]}"


@all_strategies
def test_every_chunk_is_a_verbatim_stretch_of_its_parent(chunker, parent: Document) -> None:
    # A word-set check alone would pass on chunks stitched together out of order
    # or with text repeated inside them, which is exactly what a splitter that
    # applies its overlap twice produces.
    for chunk in chunker.run([parent])["documents"]:
        assert chunk.content in parent.content, (
            f"chunk {chunk.meta['chunk_index']} is not a contiguous stretch of its source"
        )


@all_strategies
def test_the_chunks_cover_the_parent_in_order(chunker, parent: Document) -> None:
    """Chunks advance through the source and together span all of its text.

    Trailing whitespace is allowed to go missing: Haystack's NLTK sentence
    splitter drops the document's final newline (see finding F004).
    """
    source = parent.content
    cursor = 0
    covered_to = 0
    for chunk in chunker.run([parent])["documents"]:
        start = source.find(chunk.content, cursor)
        assert start >= 0, f"chunk {chunk.meta['chunk_index']} goes backwards in the source"
        assert start <= covered_to, f"gap in the source before chunk {chunk.meta['chunk_index']}"
        cursor = start
        covered_to = max(covered_to, start + len(chunk.content))
    assert source[covered_to:].strip() == "", "source text past the last chunk"


@all_strategies
def test_the_code_fence_is_not_rewritten(chunker, parent: Document) -> None:
    chunks = chunker.run([parent])["documents"]
    fences = sum(chunk.content.count("```") for chunk in chunks)
    # Every fence marker in the source survives. Overlap can repeat one, but
    # only a bounded number of times: two neighbouring chunks at most.
    assert parent.content.count("```") <= fences <= 2 * parent.content.count("```")


@pytest.mark.parametrize("name", ["naive_small", "naive_medium", "naive_large"])
def test_word_strategies_cut_at_their_size(name: str) -> None:
    # One long line of words: every chunk but the last holds exactly ``size`` words.
    chunker = get_chunker(name)
    long_text = " ".join(f"w{i}" for i in range(2_000))
    chunks = chunker.run([Document(content=long_text, meta={"source_path": "docs/long.md"})])
    counts = [len(chunk.content.split()) for chunk in chunks["documents"]]
    assert len(counts) > 2
    assert counts[:-1] == [chunker.settings.size] * (len(counts) - 1)


@pytest.mark.parametrize("name", ["naive_small", "naive_medium", "naive_large"])
def test_word_strategies_stay_within_their_size(name: str, parent: Document) -> None:
    chunker = get_chunker(name)
    for chunk in chunker.run([parent])["documents"]:
        # A "word" here is what DocumentSplitter counts: a space-separated
        # field. All but the last chunk end on the space that follows their last
        # word, which shows up as one trailing empty field.
        fields = chunk.content.split(" ")
        words = [field for field in fields if field]
        assert len(words) <= chunker.settings.size
        assert len(fields) <= chunker.settings.size + 1


@pytest.mark.skipif(not punkt_is_cached(), reason="needs NLTK punkt_tab in the model cache")
def test_recursive_strategy_stays_within_its_character_size(parent: Document) -> None:
    chunker = get_chunker("recursive")
    for chunk in chunker.run([parent])["documents"]:
        assert len(chunk.content) <= chunker.settings.size


@all_strategies
@pytest.mark.parametrize("content", ["", None, "   \n\n  "], ids=["empty", "none", "whitespace"])
def test_a_document_without_content_produces_no_chunks(chunker, content: str | None) -> None:
    # Left to the splitters these three diverge: one raises on None, another
    # treats it as empty, and whitespace comes back as a chunk of nothing.
    blank = Document(content=content, meta={"source_path": "docs/blank.md"})
    assert chunker.run([blank])["documents"] == []


@all_strategies
def test_two_documents_sharing_a_source_path_are_refused(chunker) -> None:
    # Their chunks would land on the same deterministic point id at indexing
    # time, and the second would silently overwrite the first.
    same_path = [
        Document(content=markdown_fixture(2), meta={"source_path": "docs/same.md"}),
        Document(content=markdown_fixture(3), meta={"source_path": "docs/same.md"}),
    ]
    with pytest.raises(ValueError, match="docs/same.md"):
        chunker.run(same_path)


@all_strategies
def test_a_document_without_a_source_path_still_chunks(chunker, caplog) -> None:
    # Only indexing needs a path; a document built in memory is warned about,
    # not refused.
    loose = Document(content=markdown_fixture(2))
    with caplog.at_level("WARNING"):
        chunks = chunker.run([loose])["documents"]
    assert chunks
    assert all(chunk.meta["source_path"] is None for chunk in chunks)
    assert "source_path" in caplog.text


def test_source_path_falls_back_to_the_file_path() -> None:
    # Documents read straight off disk have file_path but not yet source_path.
    parent = Document(content="A short document.", meta={"file_path": "data/docs/short.md"})
    settings = ChunkSettings(strategy="test", size=1, overlap=0, unit="word")

    chunk = build_chunks(parent, ["A short document."], settings)[0]

    assert chunk.meta["source_path"] == "data/docs/short.md"


def test_a_strategy_may_add_its_own_field_to_each_chunk() -> None:
    # The seam the structure-aware strategies use: markdown_ast adds a heading path.
    parent = Document(content="unused", meta={"source_path": "docs/one.md"})
    settings = ChunkSettings(strategy="test", size=10, overlap=0, unit="word")

    chunks = build_chunks(
        parent,
        ["first", "second"],
        settings,
        per_chunk_meta=[{"heading_path": "A"}, {"heading_path": "A > B"}],
    )

    assert [c.meta["heading_path"] for c in chunks] == ["A", "A > B"]
    with pytest.raises(ValueError):
        build_chunks(parent, ["first", "second"], settings, per_chunk_meta=[{"heading_path": "A"}])
