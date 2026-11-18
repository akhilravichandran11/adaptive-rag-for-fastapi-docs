"""
Offline tests for the chunk shape rules, messy copies, and the chunk-size report.

Each shape rule is broken on purpose with chunks built here, then all nine built
strategies run over a set of bad documents built here and over messy copies of a
seeded handful of real corpus documents. Rules 3–5 (no text lost, source order,
no empty chunk) must hold for every strategy; rules 1–2 (declared maximum and
minimum) must hold too, except where a strategy is known not to keep its declared
size (today, or by design for ``markdown_ast`` and ``markdown_sections``) — those
cases are marked ``xfail(strict=False)`` with the reason, so the gap stays visible
and the test passes once a fix lands.

An autouse fixture refuses every outgoing socket connection (C006). ``semantic``
runs with its OpenRouter client replaced by a stand-in that returns a fixed
pseudo-random vector per text, so its boundaries here are arbitrary but its
length rules and text handling are real. Tests that split text need BGE's
tokenizer and NLTK's ``punkt_tab`` in the project model cache and skip without
them; the messy-copy tests on real documents also skip when the git-ignored
corpus is absent.

    uv run python scripts/download_models.py     # from the project root
    uv run pytest tests/data_ingestion/test_chunk_shape.py
"""

import hashlib
import json
import random
import socket
import sys
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import pytest
from haystack import Document

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data_ingestion import openrouter_embeddings  # noqa: E402
from data_ingestion.chunk_shape import (  # noqa: E402
    EMPTY,
    FREE_STRATEGIES,
    GLUE_DOCUMENTS,
    INJECT_UNBROKEN_RUN,
    LOST_TEXT,
    MESSY_CHANGES,
    OUT_OF_ORDER,
    OVER_MAX,
    REMOVE_PUNCTUATION,
    SHAPE_STRATEGIES,
    STRIP_BLANK_LINES,
    UNDER_MIN,
    build_report,
    check_chunks,
    make_messy_copies,
    write_report,
)
from data_ingestion.chunkers import ChunkSettings, build_chunks, get_chunker  # noqa: E402
from data_ingestion.embedders import cached_tokenizer_path  # noqa: E402
from data_ingestion.loading import load_documents  # noqa: E402
from data_ingestion.model_cache import register_nltk_data_dir  # noqa: E402
from scripts import chunk_size_report  # noqa: E402

CORPUS_DIR = PROJECT_ROOT / "data" / "fastapi" / "normalized" / "github-docs" / "v1" / "docs"

TOY = ChunkSettings(strategy="toy", size=10, overlap=0, unit="char")
# For chunks of whole lines, long enough that rule 1 stays out of the way.
WIDE = ChunkSettings(strategy="toy", size=100_000, overlap=0, unit="char")


def punkt_is_cached() -> bool:
    """Whether NLTK's sentence tokenizer is in the project model cache."""
    import nltk

    register_nltk_data_dir()
    try:
        nltk.data.find("tokenizers/punkt_tab")
    except LookupError:
        return False
    return True


# Filesystem probes, not model loads: this runs during collection, before the
# no_network fixture exists (see C006's note on collection time).
needs_models = pytest.mark.skipif(
    cached_tokenizer_path("bge") is None or not punkt_is_cached(),
    reason="needs BGE's tokenizer and NLTK punkt_tab in the model cache; "
    "run scripts/download_models.py",
)
needs_punkt = pytest.mark.skipif(
    not punkt_is_cached(), reason="needs NLTK punkt_tab in the model cache"
)
needs_corpus = pytest.mark.skipif(
    not CORPUS_DIR.is_dir(), reason=f"normalized corpus not found at {CORPUS_DIR} (git-ignored)"
)


def _refuse(*args: object, **kwargs: object):
    raise AssertionError("a test tried to open a network connection")


def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)


def stand_in_vector(text: str) -> list[float]:
    """A fixed pseudo-random 1024-dimension vector per text, standing in for BGE."""
    rng = random.Random(hashlib.sha256(text.encode()).digest())
    return [rng.gauss(0.0, 1.0) for _ in range(1024)]


class StandInEmbeddings:
    """Stands in for ``client.embeddings`` and answers with :func:`stand_in_vector`."""

    def create(self, *, model: str, input: list[str], **kwargs: object):
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=index, embedding=stand_in_vector(text))
                for index, text in enumerate(input)
            ]
        )


def _stub_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SimpleNamespace(embeddings=StandInEmbeddings())
    monkeypatch.setattr(openrouter_embeddings, "get_client", lambda: client)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every outgoing connection raise, so no test can reach an API or a CDN (C006)."""
    _block_network(monkeypatch)


@pytest.fixture(autouse=True)
def stub_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the OpenRouter client, so ``semantic`` makes no paid call (C006)."""
    _stub_openrouter(monkeypatch)


def toy_chunks(parent: Document, texts: list[str], **extra_meta: object) -> list[Document]:
    per_chunk = [dict(extra_meta)] * len(texts) if extra_meta else None
    return build_chunks(parent, texts, TOY, per_chunk_meta=per_chunk)


def doc(content: str | None, path: str = "docs/toy.md") -> Document:
    return Document(content=content, meta={"source_path": path})


def rules_of(breaks) -> list[str]:
    return [rule_break.rule for rule_break in breaks]


# --- each rule, broken on purpose ----------------------------------------------


def test_a_clean_document_breaks_no_rule() -> None:
    parent = doc("Alpha beta gamma delta.")
    chunks = toy_chunks(parent, ["Alpha beta", " gamma ", "delta."], min_length=3)
    assert check_chunks(parent, chunks, TOY) == []


def test_overlapping_chunks_that_cover_the_document_break_no_rule() -> None:
    parent = doc("one two three four five six")
    settings = ChunkSettings(strategy="toy", size=4, overlap=1, unit="word")
    chunks = build_chunks(parent, ["one two three ", "three four five ", "five six"], settings)
    assert check_chunks(parent, chunks, settings) == []


def test_rule_1_a_chunk_over_the_declared_maximum_is_reported() -> None:
    parent = doc("Alpha beta gamma delta.")
    chunks = toy_chunks(parent, ["Alpha beta gamma", " delta."])
    breaks = check_chunks(parent, chunks, TOY)
    assert rules_of(breaks) == [OVER_MAX]
    assert breaks[0].chunk_index == 0


def test_rule_1_counts_words_the_way_the_word_splitter_does() -> None:
    # A newline does not end a word for Haystack's word splitter: "b\nc" is one.
    settings = ChunkSettings(strategy="toy", size=3, overlap=0, unit="word")
    parent = doc("a b\nc d e")
    fits = build_chunks(parent, ["a b\nc d ", "e"], settings)
    assert check_chunks(parent, fits, settings) == []
    over = build_chunks(parent, ["a b\nc d e"], settings)
    assert rules_of(check_chunks(parent, over, settings)) == [OVER_MAX]


@needs_punkt
def test_rule_1_counts_sentences_with_the_sentence_tokenizer() -> None:
    settings = ChunkSettings(strategy="toy", size=2, overlap=0, unit="sentence")
    parent = doc("One is here. Two is here. Three is here.")
    over = build_chunks(parent, [parent.content], settings)
    assert rules_of(check_chunks(parent, over, settings)) == [OVER_MAX]
    fits = build_chunks(parent, ["One is here. Two is here. ", "Three is here."], settings)
    assert check_chunks(parent, fits, settings) == []


@needs_punkt
def test_rule_1_counts_sentences_as_the_sentence_strategy_does() -> None:
    # Six sentences to DocumentSplitter (which keeps whitespace when it splits);
    # a splitter built without that setting counts seven.
    text = 'See e.g. the docs. What? Yes. Hello world! a.b.c ... "Quoted."'
    settings = ChunkSettings(strategy="toy", size=6, overlap=0, unit="sentence")
    parent = doc(text)
    assert check_chunks(parent, build_chunks(parent, [text], settings), settings) == []


@needs_punkt
def test_rule_1_counts_a_chunks_sentences_on_its_parent() -> None:
    # The second chunk starts inside the parent's "..." and holds six of the
    # parent's sentences; read on its own, the leading ".." is one more.
    six = " ".join(f"Sentence number {i} is here." for i in range(6))
    text = "It went on and on... " + six
    cut = text.index("..") + 1
    settings = ChunkSettings(strategy="toy", size=6, overlap=0, unit="sentence")
    parent = doc(text)
    chunks = build_chunks(parent, [text[:cut], text[cut:]], settings)
    assert check_chunks(parent, chunks, settings) == []


def test_rule_2_a_chunk_under_the_declared_minimum_is_reported() -> None:
    parent = doc("Alpha beta gamma delta.")
    chunks = toy_chunks(parent, ["Alpha beta", " gamma ", "delta."], min_length=8)
    breaks = check_chunks(parent, chunks, TOY)
    assert rules_of(breaks) == [UNDER_MIN, UNDER_MIN]
    assert [b.chunk_index for b in breaks] == [1, 2]


def test_rule_2_does_not_apply_to_a_document_shorter_than_the_minimum() -> None:
    parent = doc("Tiny.")
    chunks = toy_chunks(parent, ["Tiny."], min_length=50)
    assert check_chunks(parent, chunks, TOY) == []


def test_rule_2_does_not_apply_without_a_declared_minimum() -> None:
    parent = doc("Alpha beta gamma delta.")
    chunks = toy_chunks(parent, ["Alpha beta", " gamma ", "delta."])
    assert check_chunks(parent, chunks, TOY) == []


def test_rule_3_text_no_chunk_covers_is_reported_as_lost() -> None:
    parent = doc("Alpha beta gamma delta.")
    chunks = toy_chunks(parent, ["Alpha beta", "delta."])
    breaks = check_chunks(parent, chunks, TOY)
    assert rules_of(breaks) == [LOST_TEXT]
    assert breaks[0].chunk_index is None
    assert "5 of 20" in breaks[0].detail


def test_rule_3_a_document_with_no_chunks_has_lost_its_text() -> None:
    assert rules_of(check_chunks(doc("Alpha beta."), [], TOY)) == [LOST_TEXT]


def test_rule_3_a_chunk_that_is_not_the_documents_text_is_reported() -> None:
    parent = doc("Alpha beta.")
    chunks = toy_chunks(parent, ["Alpha beta", "."]) + toy_chunks(parent, ["Omega"])
    breaks = check_chunks(parent, chunks, TOY)
    assert rules_of(breaks) == [LOST_TEXT]
    assert breaks[0].chunk_index == 2


def test_rule_3_ignores_whitespace() -> None:
    parent = doc("Alpha\n\n\nbeta  gamma.")
    chunks = toy_chunks(parent, ["Alpha beta", "gamma."])
    assert check_chunks(parent, chunks, TOY) == []


FOUR_LINES = "alpha one\nbeta two\ngamma three\ndelta four\n"  # 34 non-whitespace characters


def test_rule_3_does_not_report_whole_lines_a_chunk_adds_at_its_start_or_end() -> None:
    # R17: a chunker may repeat a line the document has only once (a re-opened
    # fence, a repeated class line) at the start or end of a piece.
    parent = doc(FOUR_LINES)
    chunks = build_chunks(
        parent,
        ["alpha one\nbeta two\nadded end\n", "added start\nadded again\ngamma three\ndelta four\n"],
        WIDE,
    )
    assert check_chunks(parent, chunks, WIDE) == []


def test_rule_3_reports_a_line_missing_from_a_chunks_middle() -> None:
    parent = doc(FOUR_LINES)
    chunks = build_chunks(parent, ["alpha one\nbeta two\n", "beta two\ndelta four\n"], WIDE)
    breaks = check_chunks(parent, chunks, WIDE)
    assert rules_of(breaks) == [LOST_TEXT]
    assert breaks[0].chunk_index is None
    assert "10 of 34" in breaks[0].detail  # "gammathree"


def test_rule_3_reports_a_dropped_chunk_between_chunks_that_add_lines() -> None:
    parent = doc(FOUR_LINES)
    chunks = build_chunks(
        parent, ["alpha one\nbeta two\nadded end\n", "added start\ndelta four\n"], WIDE
    )
    breaks = check_chunks(parent, chunks, WIDE)
    assert rules_of(breaks) == [LOST_TEXT]
    assert breaks[0].chunk_index is None
    assert "10 of 34" in breaks[0].detail


def test_rule_3_reports_a_chunk_of_many_lines_none_in_the_document() -> None:
    parent = doc("alpha one\nbeta two\n")
    invented = "".join(f"line {i}\n" for i in range(5_000))
    chunks = build_chunks(parent, ["alpha one\nbeta two\n", invented], WIDE)
    breaks = check_chunks(parent, chunks, WIDE)
    assert rules_of(breaks) == [LOST_TEXT]
    assert breaks[0].chunk_index == 1


def test_rule_3_does_not_search_a_chunk_of_more_than_1000_lines() -> None:
    # The line-run search is bounded: past 1,000 lines it is not tried, so a chunk
    # that adds even one line is reported, as before R17.
    body = [f"line {i}\n" for i in range(1_000)]
    parent = doc("".join(body))
    searched = build_chunks(parent, ["added\n" + "".join(body[:999]), body[999]], WIDE)
    assert check_chunks(parent, searched, WIDE) == []
    too_long = build_chunks(parent, ["added\n" + "".join(body)], WIDE)
    breaks = check_chunks(parent, too_long, WIDE)
    assert rules_of(breaks) == [LOST_TEXT, LOST_TEXT]
    assert breaks[0].chunk_index == 0


def test_rules_3_and_4_try_every_placement_of_repeated_text() -> None:
    # "bab" also occurs later inside the overlap; placing it there would make the
    # next chunk look out of order. The true starts are 0, 3 and 4.
    parent = doc("baabababaaa")
    chunks = toy_chunks(parent, ["baaba", "bab", "ababaaa"])
    assert check_chunks(parent, chunks, TOY) == []


def test_a_real_gap_in_densely_repeated_text_is_reported_without_hanging() -> None:
    settings = ChunkSettings(strategy="toy", size=1_000, overlap=0, unit="char")
    parent = doc("a" * 5_000)
    chunks = build_chunks(parent, ["a" * 1_000] * 3, settings)
    assert rules_of(check_chunks(parent, chunks, settings)) == [LOST_TEXT]


def test_rule_4_chunks_out_of_source_order_are_reported() -> None:
    parent = doc("Alpha beta gamma delta.")
    chunks = toy_chunks(parent, [" gamma ", "delta.", "Alpha beta"])
    breaks = check_chunks(parent, chunks, TOY)
    assert rules_of(breaks) == [OUT_OF_ORDER]
    assert breaks[0].chunk_index == 2


@pytest.mark.parametrize("blank", ["", "   \n\t "], ids=["empty", "whitespace"])
def test_rule_5_an_empty_or_whitespace_chunk_is_reported(blank: str) -> None:
    parent = doc("Alpha beta gamma.")
    chunks = toy_chunks(parent, ["Alpha beta", blank, " gamma."])
    breaks = check_chunks(parent, chunks, TOY)
    assert rules_of(breaks) == [EMPTY]
    assert breaks[0].chunk_index == 1


# --- every strategy over bad documents and messy copies ---------------------


def bad_documents() -> list[Document]:
    """Documents no strategy was tuned for, built without looking at the corpus."""
    code = "\n".join(f"    value_{i} = combine(value_{i - 1}, factor={i})" for i in range(400))
    sentences = " ".join(
        f"Section {i} explains how the framework reads a request and returns a response."
        for i in range(60)
    )
    table_rows = "\n".join(f"| name_{i} | {i * 7} | enabled |" for i in range(200))
    # A Python example that parses and runs past 2,000 characters, so markdown_ast
    # cuts it between its functions and between the class's methods (F021).
    functions = "\n\n".join(
        f'def step_{i}(value):\n    """Step {i} of the example."""\n    return value * {i} + {i * 3}\n'
        for i in range(30)
    )
    methods = "\n".join(
        f"    def handle_{i}(self, request):\n        return self.respond(request, status={200 + i})\n"
        for i in range(30)
    )
    python = f'{functions}\n\nclass Handler:\n    """Answers requests."""\n\n{methods}'
    contents = {
        "bad/huge-code-block.md": f"```python\n{code}\n```\n",
        "bad/one-paragraph-no-punctuation.md": " ".join(f"word{i % 37}" for i in range(3_000)),
        "bad/unbroken-run.md": f"Intro line.\n\n{('a1B2c3' * 900)[:5_000]}\n\nOutro line.",
        "bad/long-page-ending-in-emoji.md": f"# Title\n\n{sentences}\n\n{sentences}\n\n🎉",
        "bad/markdown-table.md": f"| name | size | state |\n|---|---|---|\n{table_rows}\n",
        "bad/empty.md": "",
        "bad/whitespace-only.md": "  \n\n\t  \n",
        "bad/single-word.md": "FastAPI",
        "bad/long-python-example.md": (
            f"# Long example\n\nThe example below runs long.\n\n```python\n{python}```\n\nAfter it.\n"
        ),
    }
    return [doc(content, path) for path, content in contents.items()]


def run_all_strategies(documents: list[Document]) -> dict[str, list[Document]]:
    """Chunk ``documents`` with all nine strategies, offline (stubbed OpenRouter, no network)."""
    with pytest.MonkeyPatch.context() as patch:
        _block_network(patch)
        _stub_openrouter(patch)
        return {name: get_chunker(name).run(documents)["documents"] for name in SHAPE_STRATEGIES}


def breaks_by_rule(documents, chunks_by_strategy, strategy: str, rule: str) -> list[str]:
    """Every break of ``rule`` by ``strategy``, as readable lines."""
    found = []
    for parent in documents:
        path = parent.meta["source_path"]
        chunks = [c for c in chunks_by_strategy[strategy] if c.meta["source_path"] == path]
        settings = get_chunker(strategy).settings
        for rule_break in check_chunks(parent, chunks, settings):
            if rule_break.rule == rule:
                found.append(f"{path} chunk {rule_break.chunk_index}: {rule_break.detail}")
    return found


# Strategies that do not keep their declared size (R6). ``semantic``'s
# 1,000-character maximum and 50-character minimum are targets Haystack does not
# enforce: it returns a piece it cannot split further, and does not merge the
# short pieces a re-split produces (F016; Haystack 3.2.0 merges only a short last piece).
# ``markdown_ast`` breaks its 2,000 characters by design (D26): it never cuts inside a
# paragraph, code block or Python function, so a chunk can run past them.
KNOWN_GAPS = {
    ("semantic", OVER_MAX): "semantic returns pieces it cannot split under 1,000 characters (F016)",
    ("semantic", UNDER_MIN): "semantic does not merge short pieces a re-split produces (F016)",
    ("markdown_ast", OVER_MAX): (
        "markdown_ast does not cut inside a paragraph, code block or Python function, "
        "so a chunk can run past 2,000 characters, by design (D26)"
    ),
    ("markdown_sections", OVER_MAX): (
        "markdown_sections does not cut a code block or a paragraph, so a code block over "
        "2,000 characters (with the paragraph that introduces it) or a paragraph over 2,000 "
        "is a chunk past the maximum, by design"
    ),
}


def strategy_rule_cases(rules: tuple[str, ...]):
    for strategy in SHAPE_STRATEGIES:
        for rule in rules:
            reason = KNOWN_GAPS.get((strategy, rule))
            marks = [pytest.mark.xfail(strict=False, reason=reason)] if reason else []
            yield pytest.param(strategy, rule, marks=marks, id=f"{strategy}-{rule}")


@pytest.fixture(scope="module")
def bad_chunks() -> tuple[list[Document], dict[str, list[Document]]]:
    documents = bad_documents()
    return documents, run_all_strategies(documents)


@pytest.fixture(scope="module")
def messy_corpus_chunks() -> tuple[list[Document], dict[str, list[Document]]]:
    if not CORPUS_DIR.is_dir():
        pytest.skip(f"normalized corpus not found at {CORPUS_DIR} (data/ is git-ignored)")
    documents = make_messy_copies(load_documents(CORPUS_DIR), count=2, seed=7)
    return documents, run_all_strategies(documents)


@needs_models
@pytest.mark.parametrize(
    ("strategy", "rule"), strategy_rule_cases((LOST_TEXT, OUT_OF_ORDER, EMPTY))
)
def test_rules_3_to_5_hold_on_bad_documents(bad_chunks, strategy: str, rule: str) -> None:
    documents, chunks = bad_chunks
    assert breaks_by_rule(documents, chunks, strategy, rule) == []


@needs_models
@pytest.mark.parametrize(("strategy", "rule"), strategy_rule_cases((OVER_MAX, UNDER_MIN)))
def test_rules_1_and_2_hold_on_bad_documents(bad_chunks, strategy: str, rule: str) -> None:
    documents, chunks = bad_chunks
    assert breaks_by_rule(documents, chunks, strategy, rule) == []


@needs_models
@needs_corpus
@pytest.mark.parametrize(
    ("strategy", "rule"), strategy_rule_cases((LOST_TEXT, OUT_OF_ORDER, EMPTY))
)
def test_rules_3_to_5_hold_on_messy_copies(messy_corpus_chunks, strategy: str, rule: str) -> None:
    documents, chunks = messy_corpus_chunks
    assert breaks_by_rule(documents, chunks, strategy, rule) == []


@needs_models
@needs_corpus
@pytest.mark.parametrize(("strategy", "rule"), strategy_rule_cases((OVER_MAX, UNDER_MIN)))
def test_rules_1_and_2_hold_on_messy_copies(messy_corpus_chunks, strategy: str, rule: str) -> None:
    documents, chunks = messy_corpus_chunks
    assert breaks_by_rule(documents, chunks, strategy, rule) == []


# --- messy copies -----------------------------------------------------------------


def sample_documents() -> list[Document]:
    return [
        doc(f"# Page {i}\n\nFirst paragraph, with commas.\n\n\nSecond one; and more!\n", f"p{i}.md")
        for i in range(6)
    ]


def as_tuples(documents: list[Document]) -> list[tuple[str, str]]:
    return [(d.content, json.dumps(d.meta, sort_keys=True)) for d in documents]


def test_messy_copies_are_deterministic_for_a_seed() -> None:
    first = make_messy_copies(sample_documents(), count=3, seed=11)
    again = make_messy_copies(list(reversed(sample_documents())), count=3, seed=11)
    other = make_messy_copies(sample_documents(), count=3, seed=12)
    assert as_tuples(first) == as_tuples(again)
    assert as_tuples(first) != as_tuples(other)


def test_messy_copies_apply_only_the_changes_asked_for() -> None:
    copies = make_messy_copies(sample_documents(), changes=[STRIP_BLANK_LINES], count=2, seed=1)
    assert len(copies) == 2
    for copy in copies:
        assert copy.meta["messy_change"] == STRIP_BLANK_LINES
        assert copy.meta["source_path"].endswith(f"#messy-{STRIP_BLANK_LINES}")
        assert "\n\n" not in copy.content
        assert "," in copy.content and ";" in copy.content  # punctuation untouched


def test_each_change_does_what_it_says() -> None:
    originals = {d.meta["source_path"]: d.content for d in sample_documents()}
    copies = make_messy_copies(sample_documents(), count=2, seed=3, run_length=300)
    by_change: dict[str, list[Document]] = {}
    for copy in copies:
        by_change.setdefault(copy.meta["messy_change"], []).append(copy)
    assert sorted(by_change) == sorted(MESSY_CHANGES)

    for copy in by_change[REMOVE_PUNCTUATION]:
        assert not any(unicodedata.category(c).startswith("P") for c in copy.content)
    for copy in by_change[INJECT_UNBROKEN_RUN]:
        original = originals[copy.meta["source_path"].split("#")[0]]
        assert len(copy.content) == len(original) + 300
        assert max(len(piece) for piece in copy.content.split()) >= 300
    (glued,) = by_change[GLUE_DOCUMENTS]
    assert glued.content == "\n".join(originals[path] for path in glued.meta["glued_from"])
    assert len(glued.meta["glued_from"]) == 2


def test_messy_copies_refuse_an_unknown_change() -> None:
    with pytest.raises(ValueError, match="shuffle"):
        make_messy_copies(sample_documents(), changes=["shuffle"])


# --- the report ---------------------------------------------------------------------


def hand_built_run() -> tuple[list[Document], list[Document]]:
    """Five documents whose toy chunks break each rule once, plus one low-prose chunk."""
    clean = doc("Alpha beta gamma.", "a.md")
    broken = doc("x" * 30 + " tail", "b.md")  # over max, an empty chunk, lost text
    digits = doc("12345 678.", "c.md")  # low-prose
    swapped = doc("one two", "d.md")  # out of order
    short = doc("aa bb cc", "e.md")  # under min
    chunks = (
        toy_chunks(clean, ["Alpha beta", " gamma."])
        + toy_chunks(broken, ["x" * 30, "   "])
        + toy_chunks(digits, ["12345 678."])
        + toy_chunks(swapped, ["two", "one"])
        + toy_chunks(short, ["aa bb", " cc"], min_length=4)
    )
    return [clean, broken, digits, swapped, short], chunks


@needs_models
def test_the_report_counts_match_a_hand_built_example(tmp_path: Path) -> None:
    documents, chunks = hand_built_run()
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"files": []}\n')
    report = build_report(documents, {"toy": chunks}, manifest_path=manifest)

    row = report["strategies"]["toy"]
    assert {key: row[key] for key in (
        "chunks", "over_max", "under_min", "empty", "documents_with_lost_text",
        "order_errors", "low_prose", "unbroken_run",
    )} == {
        "chunks": 9, "over_max": 1, "under_min": 1, "empty": 1, "documents_with_lost_text": 1,
        "order_errors": 1, "low_prose": 1, "unbroken_run": 0,
    }  # fmt: skip
    assert set(row["truncation"]) == {"bge", "voyage"}
    assert row["truncation"]["bge"]["exact"] is True
    assert row["truncation"]["voyage"]["exact"] is False
    assert report["manifest"]["sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert report["documents"] == 5

    detail = {(c["source_path"], c["chunk_index"]): c for c in report["chunks"]}
    assert len(detail) == 9
    assert detail[("b.md", 0)]["rules_broken"] == [OVER_MAX]
    assert detail[("b.md", 0)]["length"] == 30
    assert detail[("b.md", 1)]["rules_broken"] == [EMPTY]
    assert detail[("c.md", 0)]["low_prose"] is True
    assert detail[("d.md", 1)]["rules_broken"] == [OUT_OF_ORDER]
    assert detail[("e.md", 1)]["rules_broken"] == [UNDER_MIN]
    for row_detail in detail.values():
        assert {"strategy", "source_path", "chunk_index", "length", "rules_broken",
                "low_prose", "unbroken_run", "bge_tokens"} <= set(row_detail)  # fmt: skip
        assert isinstance(row_detail["bge_tokens"], int)
    assert report["document_breaks"] == [
        {"strategy": "toy", "source_path": "b.md", "rule": LOST_TEXT,
         "detail": "4 of 34 non-whitespace characters are in no chunk"},
    ]  # fmt: skip

    path = write_report(report, tmp_path / "reports")
    assert path.parent == tmp_path / "reports"
    assert json.loads(path.read_text()) == report


@needs_models
def test_the_report_honours_both_thresholds() -> None:
    documents, chunks = hand_built_run()
    report = build_report(documents, {"toy": chunks}, low_prose_share=0.05, unbroken_run=20)
    row = report["strategies"]["toy"]
    assert (row["low_prose"], row["unbroken_run"]) == (0, 1)
    assert report["thresholds"] == {"low_prose_share": 0.05, "unbroken_run": 20}


def test_the_report_refuses_a_chunk_whose_document_is_missing() -> None:
    documents, chunks = hand_built_run()
    with pytest.raises(ValueError, match="b.md"):
        build_report([d for d in documents if d.meta["source_path"] != "b.md"], {"toy": chunks})


def test_a_second_report_in_the_same_second_does_not_overwrite_the_first(tmp_path: Path) -> None:
    report = {"created_at": "2026-10-07T01:02:03+00:00", "strategies": {}}
    first = write_report(report, tmp_path)
    second = write_report(report, tmp_path)
    assert (first.name, second.name) == ("20261007T010203Z.json", "20261007T010203Z-1.json")


# --- the script, end to end ---------------------------------------------------------


@pytest.fixture
def tiny_corpus(tmp_path: Path) -> Path:
    docs = tmp_path / "corpus" / "docs"
    docs.mkdir(parents=True)
    for document in bad_documents()[:5]:
        name = document.meta["source_path"].split("/")[-1]
        (docs / name).write_text(document.content, encoding="utf-8")
    (docs.parent / "manifest.json").write_text('{"source": {"ref": "test"}}\n')
    return docs


@needs_models
def test_the_script_reports_the_free_strategies_and_leaves_semantic_out(
    tiny_corpus: Path, tmp_path: Path
) -> None:
    path = chunk_size_report.main(
        ["--docs-dir", str(tiny_corpus), "--out-dir", str(tmp_path / "out"),
         "--low-prose-share", "0.3", "--unbroken-run", "150"]
    )  # fmt: skip
    report = json.loads(path.read_text())
    assert list(report["strategies"]) == list(FREE_STRATEGIES)
    assert "markdown_ast" in report["strategies"]
    assert "semantic" not in report["strategies"]
    assert report["documents"] == 5
    manifest = tiny_corpus.parent / "manifest.json"
    assert report["manifest"]["sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert report["thresholds"] == {"low_prose_share": 0.3, "unbroken_run": 150}
    assert report["input"]["messy"] is None

    row = report["strategies"]["markdown_ast"]
    assert (row["unit"], row["declared_max"]) == ("char", 2_000)
    assert (row["documents_with_lost_text"], row["order_errors"], row["empty"]) == (0, 0, 0)
    assert any(c["strategy"] == "markdown_ast" for c in report["chunks"])


@needs_models
def test_the_script_reports_markdown_ast_alone_when_asked(
    tiny_corpus: Path, tmp_path: Path
) -> None:
    path = chunk_size_report.main(
        ["--docs-dir", str(tiny_corpus), "--out-dir", str(tmp_path / "out"),
         "--strategy", "markdown_ast"]
    )  # fmt: skip
    report = json.loads(path.read_text())
    assert list(report["strategies"]) == ["markdown_ast"]
    assert {c["strategy"] for c in report["chunks"]} == {"markdown_ast"}


@needs_models
def test_the_script_runs_semantic_only_when_asked(tiny_corpus: Path, tmp_path: Path) -> None:
    path = chunk_size_report.main(
        ["--docs-dir", str(tiny_corpus), "--out-dir", str(tmp_path / "out"),
         "--strategy", "naive_small", "--include-semantic"]
    )  # fmt: skip
    assert list(json.loads(path.read_text())["strategies"]) == ["naive_small", "semantic"]


@needs_models
def test_the_script_reports_on_messy_copies_when_asked(tiny_corpus: Path, tmp_path: Path) -> None:
    path = chunk_size_report.main(
        ["--docs-dir", str(tiny_corpus), "--out-dir", str(tmp_path / "out"), "--messy",
         "--messy-change", REMOVE_PUNCTUATION, "--messy-count", "2", "--messy-seed", "5",
         "--strategy", "recursive"]
    )  # fmt: skip
    report = json.loads(path.read_text())
    assert report["documents"] == 2
    assert report["input"]["messy"]["changes"] == [REMOVE_PUNCTUATION]
    assert all(c["source_path"].endswith("#messy-remove_punctuation") for c in report["chunks"])


@pytest.mark.parametrize(
    "flags",
    [["--low-prose-share", "0"], ["--unbroken-run", "0"], ["--messy-count", "0"]],
    ids=["low-prose-share", "unbroken-run", "messy-count"],
)
def test_the_script_refuses_a_bad_setting_before_chunking_anything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, flags: list[str]
) -> None:
    def no_chunking(name: str):
        raise AssertionError(f"{name} was built before the settings were checked")

    monkeypatch.setattr(chunk_size_report, "get_chunker", no_chunking)
    with pytest.raises(SystemExit) as exit_info:
        chunk_size_report.main(["--include-semantic", "--out-dir", str(tmp_path), *flags])
    assert exit_info.value.code == 2
