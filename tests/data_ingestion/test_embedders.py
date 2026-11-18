"""
Tests for ``data_ingestion.embedders``: the registry, the component factories, and
the truncation numbers the chunking study reports.

Everything here runs offline and for free. An autouse fixture refuses any outgoing
socket connection, so a component that quietly called Voyage or OpenRouter, or a
tokenizer load that fell back to downloading from Hugging Face, fails the test
instead of costing money. BGE runs on OpenRouter, but its token counts come from its
tokenizer in the project cache; the tests that count need it and skip when it is
absent:

    uv run python scripts/download_models.py     # from the project root
    uv run pytest tests/data_ingestion/test_embedders.py
"""

import hashlib
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))  # same convention as scripts/download_models.py

from data_ingestion import openrouter_embeddings  # noqa: E402  (needs the path set above)
from data_ingestion.embedders import (  # noqa: E402
    EMBEDDERS,
    VOYAGE_TOKENS_PER_WORD,
    EmbedderSpec,
    TruncationStats,
    bge_tokenizer,
    build_document_embedder,
    build_query_embedder,
    cached_tokenizer_path,
    count_tokens,
    get_spec,
    truncation_report,
    truncation_stats,
)

# A filesystem probe, deliberately not a call to bge_tokenizer(): this runs during
# collection, before the no_network fixture below exists, and loading the tokenizer
# here would memoize it outside the guard — leaving the load path untested.
needs_bge = pytest.mark.skipif(
    cached_tokenizer_path("bge") is None,
    reason="BGE's tokenizer is not in the project model cache; run scripts/download_models.py",
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every outgoing connection raise, so no test can reach an API or a CDN."""

    def refuse(*args: object, **kwargs: object):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


# --- the registry -----------------------------------------------------------


def test_registry_holds_exactly_the_two_study_embedders():
    assert set(EMBEDDERS) == {"bge", "voyage"}


@pytest.mark.parametrize("name", sorted(EMBEDDERS))
def test_each_spec_knows_its_own_registry_name(name: str):
    assert EMBEDDERS[name].name == name
    assert get_spec(name) is EMBEDDERS[name]


def test_every_registry_entry_runs_on_a_backend_this_module_builds():
    assert {spec.backend for spec in EMBEDDERS.values()} == {"openrouter", "voyage"}


def test_bge_spec_matches_the_recorded_decision():
    spec = get_spec("bge")
    assert spec.model == "BAAI/bge-large-en-v1.5"
    assert spec.dimension == 1024
    assert spec.max_tokens == 512
    assert spec.backend == "openrouter"
    assert spec.api_model == "baai/bge-large-en-v1.5"
    assert spec.exact_tokens is True
    assert spec.tokenizer is not None
    assert (spec.tokenizer.repo, spec.tokenizer.filename, spec.tokenizer.revision) == (
        "qdrant/bge-large-en-v1.5-onnx",
        "tokenizer.json",
        "dc76b2c078fc38f0d243233d0ab0b51de925557e",
    )
    assert spec.tokenizer.sha256 == (
        "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66"
    )


def test_voyage_spec_matches_the_recorded_decision():
    spec = get_spec("voyage")
    assert spec.model == "voyage-4-lite"
    assert spec.dimension == 2048
    assert spec.max_tokens == 32_000
    assert spec.backend == "voyage"
    assert spec.exact_tokens is False


@pytest.mark.parametrize(
    "call",
    [get_spec, build_document_embedder, build_query_embedder, lambda n: truncation_stats(n, [])],
    ids=["get_spec", "build_document_embedder", "build_query_embedder", "truncation_stats"],
)
def test_an_unknown_embedder_name_is_rejected_by_name(call):
    with pytest.raises(ValueError) as caught:
        call("bge-large")
    message = str(caught.value)
    assert "bge-large" in message
    assert "bge" in message and "voyage" in message


# --- the component factories ------------------------------------------------


def test_voyage_components_build_with_no_api_key_and_no_call(monkeypatch: pytest.MonkeyPatch):
    """Building must not resolve the key or contact Voyage; only running would."""
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    for embedder in (build_document_embedder("voyage"), build_query_embedder("voyage")):
        assert embedder.model == "voyage-4-lite"
        assert embedder.output_dimension == 2048
        # Voyage's own default: it cuts anything past 32k rather than erroring.
        assert embedder.truncate is True


def test_voyage_embeds_chunks_as_documents_and_questions_as_queries(
    monkeypatch: pytest.MonkeyPatch,
):
    """D39: indexing and search use the pair Voyage pairs; setting it calls nothing."""
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    assert build_document_embedder("voyage").input_type == "document"
    assert build_query_embedder("voyage").input_type == "query"


def test_bge_components_are_openrouter_ones_built_with_no_key_and_no_call(
    monkeypatch: pytest.MonkeyPatch,
):
    """Building must not create the client, read the key, or contact OpenRouter."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(openrouter_embeddings, "_client", None)

    document_embedder = build_document_embedder("bge")
    text_embedder = build_query_embedder("bge")

    assert isinstance(document_embedder, openrouter_embeddings.OpenRouterDocumentEmbedder)
    assert isinstance(text_embedder, openrouter_embeddings.OpenRouterTextEmbedder)
    for embedder in (document_embedder, text_embedder):
        assert embedder.model == "baai/bge-large-en-v1.5"
        assert embedder.dimension == 1024
    assert openrouter_embeddings._client is None


def test_bge_questions_carry_the_model_card_instruction_and_documents_do_not(
    monkeypatch: pytest.MonkeyPatch,
):
    """D44: the collections were indexed without it, so only the query side changes."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    assert build_query_embedder("bge").prefix == (
        "Represent this sentence for searching relevant passages: "
    )
    assert not hasattr(build_document_embedder("bge"), "prefix")


def test_voyage_questions_get_no_prefix(monkeypatch: pytest.MonkeyPatch):
    """Voyage's query instruction comes from input_type="query" (D39), not from text."""
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    assert build_query_embedder("voyage").prefix == ""


@pytest.mark.parametrize(
    "build", [build_document_embedder, build_query_embedder], ids=["document", "text"]
)
def test_an_unhandled_backend_is_refused_rather_than_built_as_voyage(
    build, monkeypatch: pytest.MonkeyPatch
):
    """A new registry entry must not fall through into a paid Voyage call."""
    spec = EmbedderSpec(
        name="future",
        model="some/other-model",
        dimension=768,
        max_tokens=8192,
        backend="sentence-transformers",
        exact_tokens=True,
    )
    monkeypatch.setitem(EMBEDDERS, "future", spec)

    with pytest.raises(ValueError, match="sentence-transformers"):
        build("future")
    with pytest.raises(ValueError, match="sentence-transformers"):
        count_tokens("future", ["a chunk"])
    with pytest.raises(ValueError, match="sentence-transformers"):
        count_tokens("future", [])
    with pytest.raises(ValueError, match="sentence-transformers"):
        cached_tokenizer_path("future")


def test_an_openrouter_entry_without_a_tokenizer_source_says_so(monkeypatch: pytest.MonkeyPatch):
    spec = EmbedderSpec(
        name="hosted",
        model="some/hosted-model",
        dimension=768,
        max_tokens=512,
        backend="openrouter",
        exact_tokens=True,
        tokenizer=None,
    )
    monkeypatch.setitem(EMBEDDERS, "hosted", spec)

    with pytest.raises(ValueError, match="names no tokenizer source"):
        cached_tokenizer_path("hosted")
    with pytest.raises(ValueError, match="names no tokenizer source"):
        count_tokens("hosted", ["a chunk"])


def test_the_removed_fastembed_backend_is_refused_everywhere(monkeypatch: pytest.MonkeyPatch):
    """IDX-14 deleted the local path; an entry naming it must fail loudly, not run locally."""
    spec = EmbedderSpec(
        name="local",
        model="BAAI/bge-large-en-v1.5",
        dimension=1024,
        max_tokens=512,
        backend="fastembed",
        exact_tokens=True,
    )
    monkeypatch.setitem(EMBEDDERS, "local", spec)

    for call in (
        build_document_embedder,
        build_query_embedder,
        cached_tokenizer_path,
        lambda n: count_tokens(n, ["a chunk"]),
    ):
        with pytest.raises(ValueError, match="supported backends: openrouter, voyage"):
            call("local")


# --- token counting ---------------------------------------------------------


@needs_bge
def test_the_bge_tokenizer_loads_from_disk_with_the_network_blocked():
    """The no_network fixture is active here, so a load that reached a CDN would fail."""
    assert bge_tokenizer().encode("hello world").tokens[0] == "[CLS]"


@needs_bge
def test_a_moved_cache_folder_is_not_served_from_the_previous_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Memoizing the tokenizer must not outlive the cache folder it was read from."""
    assert bge_tokenizer() is not None  # warm the cache for the real folder
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path))

    assert cached_tokenizer_path("bge") is None
    with pytest.raises(FileNotFoundError, match="download_models.py"):
        bge_tokenizer()


@needs_bge
def test_a_caller_configuring_its_tokenizer_cannot_change_the_reported_counts():
    """bge_tokenizer() hands out a copy; truncating it must not truncate the study's counts."""
    mine = bge_tokenizer()
    mine.enable_truncation(512)
    mine.enable_padding(length=1000)
    assert len(mine.encode("word " * 600).ids) == 1000

    assert count_tokens("bge", ["word " * 600]) == [602]
    assert truncation_stats("bge", ["word " * 600]).tokens_lost == 90


@needs_bge
def test_bge_s_tokenizer_is_the_pinned_snapshot_file():
    path = cached_tokenizer_path("bge")
    source = get_spec("bge").tokenizer

    assert path is not None and source is not None
    assert path.name == "tokenizer.json"
    assert path.parent.name == source.revision
    assert hashlib.sha256(path.read_bytes()).hexdigest() == source.sha256


@needs_bge
def test_bge_counts_need_only_the_tokenizer_file_and_not_fastembed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A cache holding nothing but tokenizer.json gives the same numbers as before IDX-12."""
    source = get_spec("bge").tokenizer
    snapshot = (
        tmp_path / f"models--{source.repo.replace('/', '--')}" / "snapshots" / source.revision
    )
    snapshot.mkdir(parents=True)
    shutil.copyfile(cached_tokenizer_path("bge"), snapshot / source.filename)
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path))
    monkeypatch.setitem(sys.modules, "fastembed", None)  # importing it now fails

    assert cached_tokenizer_path("bge") == snapshot / source.filename
    assert bge_tokenizer().encode("hello world").tokens[0] == "[CLS]"
    assert count_tokens("bge", ["hello world", "word " * 600]) == [4, 602]
    stats = truncation_stats("bge", ["word " * 509, "word " * 520])
    assert (stats.chunks_over_limit, stats.tokens_lost, stats.largest_chunk_tokens) == (1, 10, 522)


@needs_bge
def test_building_and_counting_never_imports_fastembed():
    """The local path is gone (IDX-14); a fresh interpreter proves nothing loads it."""
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); from data_ingestion import embedders as e; "
        "e.build_document_embedder('bge'); e.build_query_embedder('bge'); "
        "e.count_tokens('bge', ['hi']); e.build_document_embedder('voyage'); "
        "print('fastembed' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(PROJECT_ROOT)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "False"


def test_an_api_embedder_has_no_local_tokenizer_to_find():
    assert cached_tokenizer_path("voyage") is None


@needs_bge
def test_bge_counts_include_the_special_tokens_the_model_pays_for():
    # BGE reads [CLS] ... [SEP], and both count against the 512.
    assert count_tokens("bge", ["hello world"]) == [4]


@needs_bge
def test_bge_counts_are_per_text_not_padded_to_the_longest():
    assert count_tokens("bge", ["hello world", "hello"]) == [4, 3]


def test_voyage_counts_are_the_documented_estimate():
    text = "one two three four five"  # 5 words
    assert count_tokens("voyage", [text]) == [7]  # ceil(5 * 1.3)
    assert VOYAGE_TOKENS_PER_WORD == 1.3


def test_counting_no_texts_returns_no_counts():
    assert count_tokens("voyage", []) == []


# --- truncation stats -------------------------------------------------------


@needs_bge
def test_bge_stats_separate_the_chunk_just_over_the_limit_from_the_one_just_under():
    # "word" is a single BGE token, so N words plus [CLS] and [SEP] is N + 2 tokens.
    under = "word " * 509  # 511 tokens: fits
    over = "word " * 520  # 522 tokens: 10 tokens past the limit
    assert count_tokens("bge", [under, over]) == [511, 522]

    stats = truncation_stats("bge", [under, over])

    assert stats.embedder == "bge"
    assert stats.model == "BAAI/bge-large-en-v1.5"
    assert stats.max_tokens == 512
    assert stats.exact is True
    assert stats.chunk_count == 2
    assert stats.chunks_over_limit == 1
    assert stats.percent_over_limit == 50.0
    assert stats.tokens_lost == 10  # only the overflow, not the whole chunk
    assert stats.largest_chunk_tokens == 522


@needs_bge
def test_bge_stats_report_nothing_lost_when_every_chunk_fits():
    stats = truncation_stats("bge", ["word " * 100, "word " * 200])

    assert stats.chunks_over_limit == 0
    assert stats.percent_over_limit == 0.0
    assert stats.tokens_lost == 0
    assert stats.largest_chunk_tokens == 202


def test_voyage_stats_are_labelled_as_an_estimate_and_clear_normal_chunks():
    stats = truncation_stats("voyage", ["word " * 520])

    assert stats.exact is False
    assert stats.max_tokens == 32_000
    assert stats.chunks_over_limit == 0
    assert stats.largest_chunk_tokens == 676  # ceil(520 * 1.3)


def test_voyage_stats_do_report_a_chunk_past_its_own_limit():
    stats = truncation_stats("voyage", ["word " * 30_000])  # 39,000 estimated tokens

    assert stats.chunks_over_limit == 1
    assert stats.tokens_lost == 39_000 - 32_000
    assert stats.largest_chunk_tokens == 39_000


def test_stats_on_no_chunks_do_not_divide_by_zero():
    stats = truncation_stats("voyage", [])

    assert stats.chunk_count == 0
    assert stats.chunks_over_limit == 0
    assert stats.percent_over_limit == 0.0
    assert stats.largest_chunk_tokens == 0


def test_a_report_covers_only_the_embedders_it_was_asked_for():
    report = truncation_report(["a chunk"], names=["voyage"])

    assert list(report) == ["voyage"]
    assert report["voyage"].chunk_count == 1


def test_asking_for_no_embedders_reports_on_none_of_them():
    # Not the same as asking for all of them: a caller that filtered its list down
    # to nothing must not get the (slow, local) BGE pass it just excluded.
    assert truncation_report(["a chunk"], names=[]) == {}


@needs_bge
def test_a_report_measures_the_same_chunks_against_every_embedder():
    chunks = ["word " * 520, "word " * 10]
    report = truncation_report(chunk for chunk in chunks)  # a generator, read once

    assert set(report) == {"bge", "voyage"}
    assert all(stats.chunk_count == 2 for stats in report.values())
    assert report["bge"].chunks_over_limit == 1
    assert report["voyage"].chunks_over_limit == 0


# --- how the numbers are presented ------------------------------------------


def _stats(**overrides) -> TruncationStats:
    values = {
        "embedder": "bge",
        "model": "BAAI/bge-large-en-v1.5",
        "max_tokens": 512,
        "exact": True,
        "chunk_count": 4,
        "chunks_over_limit": 3,
        "tokens_lost": 120,
        "largest_chunk_tokens": 600,
    }
    return TruncationStats(**{**values, **overrides})


def test_a_summary_says_the_counts_are_exact():
    summary = _stats().summary()

    assert "exact token counts" in summary
    assert "3/4 chunks over the limit (75.0%)" in summary
    assert "120 tokens dropped" in summary
    assert "largest chunk 600 tokens" in summary


def test_a_summary_never_passes_an_estimate_off_as_exact():
    stats = _stats(embedder="voyage", model="voyage-4-lite", max_tokens=32_000, exact=False)
    assert "estimated token counts" in stats.summary()


def test_as_dict_carries_every_number_the_report_needs():
    assert _stats().as_dict() == {
        "embedder": "bge",
        "model": "BAAI/bge-large-en-v1.5",
        "max_tokens": 512,
        "exact": True,
        "chunk_count": 4,
        "chunks_over_limit": 3,
        "percent_over_limit": 75.0,
        "tokens_lost": 120,
        "largest_chunk_tokens": 600,
    }
