"""
Offline tests for ``evaluation/ragas_testset.py`` at its two agreed seams: ``to_questions``
on a small real RAGAS knowledge graph, and what ``main(argv)`` writes or refuses, with the
paid pieces faked where the module looks them up.

The module could call OpenAI, so the guard below refuses every network call (C006).
"""

import importlib
import importlib.util
import json
import logging
import os
import socket
import sys
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace

import dotenv
import openai
import pytest
from ragas.dataset_schema import SingleTurnSample
from ragas.embeddings import OpenAIEmbeddings

# Aliased because pytest tries to collect any name in this module that starts with "Test".
from ragas.testset import Testset as RagasTestset
from ragas.testset import TestsetGenerator as RagasGenerator
from ragas.testset import TestsetSample as RagasSample
from ragas.testset.graph import KnowledgeGraph, Node, NodeType, Relationship

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))  # lets pytest import the project from any directory

FIRST_STEPS = "tutorial/first-steps.md"
EVENTS = "advanced/events.md"
ROUTER = "reference/apirouter.md"

CREATE_CHUNK = "# First steps\n\nCreate the app  with FastAPI()."
RUN_CHUNK = "## Run it\n\nStart the server with fastapi dev."
LIFESPAN_CHUNK = "# Lifespan events\n\nPass a lifespan function to the app."
SHUTDOWN_CHUNK = "## Shutdown\n\nCode after the yield runs at shutdown."
# RAGAS's default splitter cuts a long page into chunks at its headlines.
CHUNKS = {FIRST_STEPS: [CREATE_CHUNK, RUN_CHUNK], EVENTS: [LIFESPAN_CHUNK, SHUTDOWN_CHUNK]}
PAGES = {
    FIRST_STEPS: f"{CREATE_CHUNK}\n\n{RUN_CHUNK}\n",
    EVENTS: f"{LIFESPAN_CHUNK}\n\n{SHUTDOWN_CHUNK}\n",
    # A page RAGAS leaves whole, so a context from it is the document node's own text.
    ROUTER: "# APIRouter\n\nGroup path operations in a router.\n",
}
UNKNOWN_CONTEXT = "## Deploy\n\nA section that no node of the graph holds."

SINGLE_HOP = "single_hop_specific_query_synthesizer"
MULTI_HOP = "multi_hop_abstract_query_synthesizer"

WHOLE_PAGE_SAMPLE = RagasSample(
    eval_sample=SingleTurnSample(
        user_input="What does APIRouter do?",
        reference="It groups path operations.",
        reference_contexts=[PAGES[ROUTER]],
        persona_name="New FastAPI user",
        query_style="WEB_SEARCH_LIKE",
        query_length="SHORT",
    ),
    synthesizer_name=SINGLE_HOP,
)
CHUNK_SAMPLE = RagasSample(
    eval_sample=SingleTurnSample(
        user_input="How do I start the server?",
        reference="Run fastapi dev.",
        reference_contexts=[RUN_CHUNK],
        persona_name="Backend developer",
        query_style="PERFECT_GRAMMAR",
        query_length="MEDIUM",
    ),
    synthesizer_name=SINGLE_HOP,
)
# RAGAS writes a multi-hop context as "<n-hop>\n\n" + the node's text, and sets no persona,
# style or length on the sample. The first and third hops are chunks of the same page, and
# the pages' first-seen order is not their alphabetical order.
MULTI_HOP_SAMPLE = RagasSample(
    eval_sample=SingleTurnSample(
        user_input="How do I create an app, run it, and clean up when it shuts down?",
        reference="Create it with FastAPI(), run fastapi dev, and put clean-up after the yield.",
        reference_contexts=[
            f"<1-hop>\n\n{CREATE_CHUNK}",
            f"<2-hop>\n\n{SHUTDOWN_CHUNK}",
            f"<3-hop>\n\n{RUN_CHUNK}",
        ],
    ),
    synthesizer_name=MULTI_HOP,
)
# CREATE_CHUNK's text with every run of whitespace changed.
WHITESPACE_SAMPLE = RagasSample(
    eval_sample=SingleTurnSample(
        user_input="How do I create the app?",
        reference="Call FastAPI().",
        reference_contexts=["  # First steps\nCreate   the app with\tFastAPI().\n"],
        persona_name="Backend developer",
        query_style="MISSPELLED",
        query_length="LONG",
    ),
    synthesizer_name=SINGLE_HOP,
)
UNKNOWN_CONTEXT_SAMPLE = RagasSample(
    eval_sample=SingleTurnSample(
        user_input="How do I run and deploy the app?",
        reference="Run fastapi dev, then deploy it.",
        reference_contexts=[f"<1-hop>\n\n{RUN_CHUNK}", f"<2-hop>\n\n{UNKNOWN_CONTEXT}"],
    ),
    synthesizer_name=MULTI_HOP,
)
# RAGAS's multi-hop abstract synthesizer can build a scenario with no nodes, so no contexts.
EMPTY_CONTEXT_SAMPLE = RagasSample(
    eval_sample=SingleTurnSample(
        user_input="How do routers and events work together?",
        reference="Through the app's lifespan.",
        reference_contexts=[],
    ),
    synthesizer_name=MULTI_HOP,
)
# to_list() leaves out a field that is None, so this sample has no "reference" key.
NO_ANSWER_SAMPLE = RagasSample(
    eval_sample=SingleTurnSample(
        user_input="What does APIRouter do?",
        reference_contexts=[PAGES[ROUTER]],
        persona_name="New FastAPI user",
        query_style="WEB_SEARCH_LIKE",
        query_length="SHORT",
    ),
    synthesizer_name=SINGLE_HOP,
)
# What the fake generator returns, in RAGAS's order.
GENERATED = [CHUNK_SAMPLE, UNKNOWN_CONTEXT_SAMPLE, MULTI_HOP_SAMPLE, WHOLE_PAGE_SAMPLE]

QUESTION_KEYS = [
    "question_id",
    "question",
    "reference_answer",
    "gold_sources",
    "origin",
    "synthesizer_name",
    "persona_name",
    "query_style",
    "query_length",
    "reference_contexts",
]


@pytest.fixture
def ragas_testset(monkeypatch):
    """Import the module with network access and OpenAI client construction forbidden."""

    def refuse(*args, **kwargs):
        raise AssertionError("offline test attempted API or network activity")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(openai.OpenAI, "__init__", refuse)
    monkeypatch.setattr(openai.AsyncOpenAI, "__init__", refuse)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)

    module = importlib.import_module("evaluation.ragas_testset")
    monkeypatch.setattr(module, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setattr(module, "TestsetGenerator", refuse)
    monkeypatch.setattr(module, "llm_factory", refuse)
    # RAGAS's token counter downloads its encoding file the first time it is used.
    monkeypatch.setattr(module, "num_tokens_from_string", refuse)
    # main configures the root logger with force=True, which would remove caplog's handler,
    # and quiets httpx for the rest of the session.
    monkeypatch.setattr(logging, "basicConfig", lambda *args, **kwargs: None)
    monkeypatch.setattr(logging.getLogger("httpx"), "setLevel", lambda level: None)
    return module


@pytest.fixture
def graph() -> KnowledgeGraph:
    """The three pages as RAGAS's default transforms leave them: two split, one whole."""
    pages = {
        source_path: Node(
            type=NodeType.DOCUMENT,
            properties={"page_content": text, "document_metadata": {"source_path": source_path}},
        )
        for source_path, text in PAGES.items()
    }
    chunks = {
        text: Node(type=NodeType.CHUNK, properties={"page_content": text})
        for texts in CHUNKS.values()
        for text in texts
    }
    relationships = [
        Relationship(type="child", source=pages[source_path], target=chunks[text])
        for source_path, texts in CHUNKS.items()
        for text in texts
    ]
    # RAGAS also links a page's chunks in order and chunks of different pages that share
    # entities. Neither kind of link says which page a chunk came from.
    relationships.append(
        Relationship(type="next", source=chunks[CREATE_CHUNK], target=chunks[RUN_CHUNK])
    )
    relationships.append(
        Relationship(
            type="entities_overlap", source=chunks[RUN_CHUNK], target=chunks[LIFESPAN_CHUNK]
        )
    )
    return KnowledgeGraph(nodes=[*pages.values(), *chunks.values()], relationships=relationships)


def test_a_context_that_is_a_whole_page_maps_to_that_page(ragas_testset, graph):
    samples = RagasTestset(samples=[WHOLE_PAGE_SAMPLE]).to_list()

    questions, unmatched = ragas_testset.to_questions(samples, graph)

    assert questions == [
        {
            "question_id": "rag-01",
            "question": "What does APIRouter do?",
            "reference_answer": "It groups path operations.",
            "gold_sources": [ROUTER],
            "origin": "ragas",
            "synthesizer_name": SINGLE_HOP,
            "persona_name": "New FastAPI user",
            "query_style": "WEB_SEARCH_LIKE",
            "query_length": "SHORT",
            "reference_contexts": [PAGES[ROUTER]],
        }
    ]
    assert unmatched == []


def test_a_chunk_maps_to_the_page_it_was_split_from(ragas_testset, graph):
    samples = RagasTestset(samples=[CHUNK_SAMPLE]).to_list()

    questions, unmatched = ragas_testset.to_questions(samples, graph)

    assert [question["gold_sources"] for question in questions] == [[FIRST_STEPS]]
    assert unmatched == []


def test_a_multi_hop_sample_lists_each_page_once_in_first_seen_order(ragas_testset, graph):
    samples = RagasTestset(samples=[MULTI_HOP_SAMPLE]).to_list()

    [question], unmatched = ragas_testset.to_questions(samples, graph)

    assert question["gold_sources"] == [FIRST_STEPS, EVENTS]
    # the contexts stay as RAGAS wrote them, hop labels included
    assert question["reference_contexts"] == MULTI_HOP_SAMPLE.eval_sample.reference_contexts
    assert question["synthesizer_name"] == MULTI_HOP
    assert (question["persona_name"], question["query_style"], question["query_length"]) == (
        None,
        None,
        None,
    )
    assert unmatched == []


def test_a_context_matches_its_node_whatever_the_whitespace(ragas_testset, graph):
    samples = RagasTestset(samples=[WHITESPACE_SAMPLE]).to_list()

    questions, unmatched = ragas_testset.to_questions(samples, graph)

    assert [question["gold_sources"] for question in questions] == [[FIRST_STEPS]]
    assert unmatched == []


def test_a_sample_with_one_unknown_context_is_unmatched_and_takes_no_id(ragas_testset, graph):
    samples = RagasTestset(
        samples=[CHUNK_SAMPLE, UNKNOWN_CONTEXT_SAMPLE, WHOLE_PAGE_SAMPLE]
    ).to_list()

    questions, unmatched = ragas_testset.to_questions(samples, graph)

    assert [(question["question_id"], question["question"]) for question in questions] == [
        ("rag-01", "How do I start the server?"),
        ("rag-02", "What does APIRouter do?"),
    ]
    # its first context is a known chunk, and that is not used to guess a page
    assert unmatched == [
        {
            "question": "How do I run and deploy the app?",
            "reference_contexts": [f"<1-hop>\n\n{RUN_CHUNK}", f"<2-hop>\n\n{UNKNOWN_CONTEXT}"],
        }
    ]


def test_a_sample_with_no_contexts_is_unmatched_and_takes_no_id(ragas_testset, graph):
    samples = RagasTestset(
        samples=[CHUNK_SAMPLE, EMPTY_CONTEXT_SAMPLE, WHOLE_PAGE_SAMPLE]
    ).to_list()

    questions, unmatched = ragas_testset.to_questions(samples, graph)

    assert [(question["question_id"], question["question"]) for question in questions] == [
        ("rag-01", "How do I start the server?"),
        ("rag-02", "What does APIRouter do?"),
    ]
    assert unmatched == [
        {"question": "How do routers and events work together?", "reference_contexts": []}
    ]


def test_a_sample_without_an_answer_keeps_none_as_its_answer(ragas_testset, graph):
    samples = RagasTestset(samples=[NO_ANSWER_SAMPLE]).to_list()

    [question], unmatched = ragas_testset.to_questions(samples, graph)

    assert question["reference_answer"] is None
    assert question["gold_sources"] == [ROUTER]
    assert unmatched == []


@pytest.fixture
def corpus(ragas_testset, tmp_path, monkeypatch) -> Path:
    """Put the three pages and their manifest on disk; returns the folder runs are written to."""
    root = tmp_path / "corpus"
    (root / "docs").mkdir(parents=True)
    files = []
    for source_path, text in PAGES.items():
        output_filename = source_path.replace("/", "__")
        (root / "docs" / output_filename).write_text(text, encoding="utf-8")
        files.append({"source_path": source_path, "output_filename": output_filename})
    (root / "manifest.json").write_text(json.dumps({"files": files}), encoding="utf-8")

    monkeypatch.setattr(ragas_testset, "MANIFEST_PATH", root / "manifest.json")
    monkeypatch.setattr(ragas_testset, "OUTPUTS_DIR", tmp_path / "outputs")
    return tmp_path / "outputs"


@pytest.fixture
def paid(ragas_testset, monkeypatch) -> SimpleNamespace:
    """Fake the paid pieces and the token counter where the module looks them up; returns
    what they were given.

    ``OpenAIEmbeddings`` is not faked: RAGAS's own constructor runs and connects to nothing.
    """
    seen = SimpleNamespace(
        clients=[],
        llms=[],
        runs=[],
        error=None,
        # Every page counts as a long one unless a test gives it another length.
        tokens=dict.fromkeys(PAGES.values(), 900),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def fake_num_tokens(string):
        """Like RAGAS's counter called as RAGAS's own filter calls it: one text in, its
        number of tokens out. A text that is not a page of the corpus has no count."""
        return seen.tokens[string]

    class FakeOpenAI:
        """Like ``openai.OpenAI``: keyword arguments only, its key from the environment
        or it raises, and two retries unless it is given another number."""

        def __init__(self, *, max_retries=2):
            if not os.environ.get("OPENAI_API_KEY"):
                raise openai.OpenAIError("Missing credentials. Please pass an `api_key`")
            self.max_retries = max_retries
            seen.clients.append(self)

    def fake_llm_factory(
        model, provider="openai", client=None, adapter="auto", cache=None, **kwargs
    ):
        """RAGAS's signature and its two argument checks, without wrapping the client."""
        if client is None:
            raise ValueError("llm_factory() requires a client instance.")
        if not model:
            raise ValueError("model parameter is required")
        llm = SimpleNamespace(model=model, provider=provider, client=client, kwargs=kwargs)
        seen.llms.append(llm)
        return llm

    class FakeGenerator(RagasGenerator):
        """RAGAS's own dataclass, so its constructor is the real one; only the paid method
        is replaced."""

        def generate_with_langchain_docs(self, documents, testset_size, **options):
            seen.runs.append(
                SimpleNamespace(
                    generator=self,
                    documents=list(documents),
                    testset_size=testset_size,
                    options=options,
                )
            )
            # As RAGAS does: a document node per page from its text and metadata, then chunk
            # nodes linked to their page by "child". The generator's graph is empty until
            # this run replaces it, and it is replaced before any sample is generated.
            graph = KnowledgeGraph(
                nodes=[
                    Node(
                        type=NodeType.DOCUMENT,
                        properties={
                            "page_content": document.page_content,
                            "document_metadata": document.metadata,
                        },
                    )
                    for document in documents
                ]
            )
            for page in list(graph.nodes):
                for text in CHUNKS.get(page.properties["document_metadata"]["source_path"], []):
                    chunk = Node(type=NodeType.CHUNK, properties={"page_content": text})
                    graph.nodes.append(chunk)
                    graph.relationships.append(
                        Relationship(type="child", source=page, target=chunk)
                    )
            self.knowledge_graph = graph
            if seen.error is not None:
                raise seen.error
            return RagasTestset(samples=GENERATED)

    monkeypatch.setattr(ragas_testset, "num_tokens_from_string", fake_num_tokens)
    monkeypatch.setattr(ragas_testset, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(ragas_testset, "llm_factory", fake_llm_factory)
    monkeypatch.setattr(ragas_testset, "TestsetGenerator", FakeGenerator)
    return seen


def test_main_writes_the_questions_and_the_graph_to_a_new_run_folder(
    ragas_testset, corpus, paid, monkeypatch
):
    monkeypatch.setenv("OPENAI_MODEL", "model-under-test")

    ragas_testset.main([])

    [run_dir] = corpus.iterdir()
    assert sorted(path.name for path in run_dir.iterdir()) == [
        "knowledge_graph.json",
        "ragas_testset.json",
    ]
    data = json.loads((run_dir / "ragas_testset.json").read_text(encoding="utf-8"))
    assert list(data) == [
        "ragas_version",
        "model",
        "embedding_model",
        "testset_size",
        "questions",
        "unmatched",
    ]
    assert (data["ragas_version"], data["model"]) == (version("ragas"), "model-under-test")
    assert (data["embedding_model"], data["testset_size"]) == ("text-embedding-3-small", 40)
    assert all(list(question) == QUESTION_KEYS for question in data["questions"])
    assert [
        (question["question_id"], question["gold_sources"]) for question in data["questions"]
    ] == [
        ("rag-01", [FIRST_STEPS]),
        ("rag-02", [FIRST_STEPS, EVENTS]),
        ("rag-03", [ROUTER]),
    ]
    assert {question["origin"] for question in data["questions"]} == {"ragas"}
    assert [entry["question"] for entry in data["unmatched"]] == [
        "How do I run and deploy the app?"
    ]


def test_main_gives_ragas_every_long_page_and_nothing_but_its_defaults(
    ragas_testset, corpus, paid, monkeypatch
):
    monkeypatch.setenv("OPENAI_MODEL", "model-under-test")
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "7")

    ragas_testset.main([])

    [client] = paid.clients
    [llm] = paid.llms
    [run] = paid.runs
    assert client.max_retries == 7
    assert (llm.model, llm.provider, llm.client, llm.kwargs) == (
        "model-under-test",
        "openai",
        client,
        {},
    )
    assert run.generator.llm is llm
    embedding_model = run.generator.embedding_model
    assert isinstance(embedding_model, OpenAIEmbeddings)
    assert (embedding_model.client, embedding_model.model) == (client, "text-embedding-3-small")
    assert (run.generator.persona_list, run.generator.llm_context) == (None, None)
    assert (run.testset_size, run.options) == (40, {})
    assert [(document.metadata, document.page_content) for document in run.documents] == [
        ({"source_path": source_path}, text) for source_path, text in PAGES.items()
    ]


def test_main_leaves_out_the_pages_of_500_tokens_or_fewer(ragas_testset, corpus, paid, caplog):
    caplog.set_level(logging.INFO)
    paid.tokens.update({PAGES[FIRST_STEPS]: 500, PAGES[EVENTS]: 501, PAGES[ROUTER]: 120})

    ragas_testset.main([])

    [run] = paid.runs
    assert [(document.metadata, document.page_content) for document in run.documents] == [
        ({"source_path": EVENTS}, PAGES[EVENTS])
    ]
    assert "from 1 pages" in caplog.text
    assert "2 pages of 500 tokens or fewer are left out" in caplog.text


def test_the_saved_graph_gives_the_same_gold_pages_again(ragas_testset, corpus, paid):
    ragas_testset.main([])

    [run_dir] = corpus.iterdir()
    data = json.loads((run_dir / "ragas_testset.json").read_text(encoding="utf-8"))
    saved_graph = KnowledgeGraph.load(run_dir / "knowledge_graph.json")
    samples = RagasTestset(samples=GENERATED).to_list()
    assert ragas_testset.to_questions(samples, saved_graph) == (
        data["questions"],
        data["unmatched"],
    )


def test_a_failed_write_of_the_questions_leaves_no_graph_behind(
    ragas_testset, corpus, paid, monkeypatch
):
    # a file name inside a folder that does not exist, so the write raises
    monkeypatch.setattr(ragas_testset, "OUTPUT_FILE", "missing/ragas_testset.json")

    with pytest.raises(FileNotFoundError):
        ragas_testset.main([])

    [run_dir] = corpus.iterdir()
    assert list(run_dir.iterdir()) == []


def test_main_uses_gpt_4o_mini_when_openai_model_is_unset(ragas_testset, corpus, paid, monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    ragas_testset.main([])

    [run_dir] = corpus.iterdir()
    data = json.loads((run_dir / "ragas_testset.json").read_text(encoding="utf-8"))
    assert (data["model"], paid.llms[0].model) == ("gpt-4o-mini", "gpt-4o-mini")


def test_main_gives_the_client_5_retries_when_openai_max_retries_is_unset(
    ragas_testset, corpus, paid, monkeypatch
):
    monkeypatch.delenv("OPENAI_MAX_RETRIES", raising=False)

    ragas_testset.main([])

    [client] = paid.clients
    assert client.max_retries == 5


def test_main_takes_openai_max_retries_from_a_value_only_dotenv_holds(
    ragas_testset, corpus, paid, monkeypatch
):
    monkeypatch.delenv("OPENAI_MAX_RETRIES", raising=False)
    # the real load_dotenv puts the variables of .env into the environment
    monkeypatch.setattr(
        ragas_testset, "load_dotenv", lambda path: monkeypatch.setenv("OPENAI_MAX_RETRIES", "3")
    )

    ragas_testset.main([])

    [client] = paid.clients
    assert client.max_retries == 3


def test_main_loads_dotenv_before_it_builds_a_client_and_sets_no_opt_out_itself(
    ragas_testset, corpus, paid, monkeypatch
):
    monkeypatch.delenv("RAGAS_DO_NOT_TRACK", raising=False)
    loads = []
    monkeypatch.setattr(
        ragas_testset, "load_dotenv", lambda path: loads.append((path, len(paid.clients)))
    )

    ragas_testset.main([])

    assert loads == [(PROJECT_ROOT / ".env", 0)]
    assert len(paid.clients) == 1
    assert "RAGAS_DO_NOT_TRACK" not in os.environ


@pytest.mark.parametrize("missing", ["manifest.json", "docs/reference__apirouter.md"])
def test_main_exits_1_naming_a_missing_input_before_any_paid_call(
    ragas_testset, corpus, paid, caplog, missing
):
    # the missing page is the last one in the manifest, so the pages before it are read first
    path = ragas_testset.MANIFEST_PATH.parent / missing
    path.unlink()

    with pytest.raises(SystemExit) as exit_info:
        ragas_testset.main([])

    assert exit_info.value.code == 1
    assert str(path) in caplog.text
    assert (paid.clients, paid.llms, paid.runs) == ([], [], [])
    assert not corpus.exists()


def test_main_exits_1_and_writes_nothing_when_generation_fails(ragas_testset, corpus, paid, caplog):
    paid.error = RuntimeError("still failing after the client's retries")

    with pytest.raises(SystemExit) as exit_info:
        ragas_testset.main([])

    assert exit_info.value.code == 1
    assert len(paid.runs) == 1
    assert "still failing after the client's retries" in caplog.text
    assert not corpus.exists()


def test_main_exits_1_and_writes_nothing_without_an_openai_key(
    ragas_testset, corpus, paid, monkeypatch, caplog
):
    monkeypatch.delenv("OPENAI_API_KEY")

    with pytest.raises(SystemExit) as exit_info:
        ragas_testset.main([])

    assert exit_info.value.code == 1
    assert "Missing credentials" in caplog.text
    assert (paid.llms, paid.runs) == ([], [])
    assert not corpus.exists()


def test_main_exits_1_and_writes_nothing_when_openai_max_retries_is_not_a_number(
    ragas_testset, corpus, paid, monkeypatch, caplog
):
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "five")

    with pytest.raises(SystemExit) as exit_info:
        ragas_testset.main([])

    assert exit_info.value.code == 1
    assert "invalid literal for int() with base 10: 'five'" in caplog.text
    assert (paid.clients, paid.llms, paid.runs) == ([], [], [])
    assert not corpus.exists()


def test_main_exits_1_and_writes_nothing_when_the_pages_cannot_be_counted(
    ragas_testset, corpus, paid, monkeypatch, caplog
):
    def no_encoding_file(string):
        raise OSError("cannot download the encoding file")

    monkeypatch.setattr(ragas_testset, "num_tokens_from_string", no_encoding_file)

    with pytest.raises(SystemExit) as exit_info:
        ragas_testset.main([])

    assert exit_info.value.code == 1
    assert "cannot download the encoding file" in caplog.text
    # every page was read, so this is not reported as a corpus that cannot be read
    assert "cannot read the corpus" not in caplog.text
    assert (paid.clients, paid.llms, paid.runs) == ([], [], [])
    assert not corpus.exists()


def test_help_names_the_paid_calls_and_runs_nothing(ragas_testset, corpus, paid, capsys):
    with pytest.raises(SystemExit) as exit_info:
        ragas_testset.main(["--help"])

    assert exit_info.value.code == 0
    # argparse wraps the description to the terminal's width
    assert "PAID OpenAI calls" in " ".join(capsys.readouterr().out.split())
    assert paid.clients == []
    assert not corpus.exists()


def test_main_takes_no_options(ragas_testset, corpus, paid):
    with pytest.raises(SystemExit) as exit_info:
        ragas_testset.main(["--size", "10"])

    assert exit_info.value.code == 2
    assert paid.clients == []
    assert not corpus.exists()


def test_importing_the_module_parses_no_arguments_and_opens_no_connection(
    ragas_testset, monkeypatch
):
    monkeypatch.setattr(sys, "argv", ["ragas_testset.py", "--no-such-flag"])
    monkeypatch.setattr(sys, "path", list(sys.path))  # the module adds the project root
    spec = importlib.util.spec_from_file_location(
        "ragas_testset_fresh", PROJECT_ROOT / "evaluation" / "ragas_testset.py"
    )
    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)  # parsing would exit 2; connecting would fail the test

    assert module.TESTSET_SIZE == 40
