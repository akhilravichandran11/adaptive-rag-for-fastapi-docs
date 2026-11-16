"""
Generate 40 questions from the FastAPI pages over 500 tokens with RAGAS's test-set generator,
and name each question's gold pages through the knowledge graph RAGAS built. The run makes
PAID OpenAI calls; see ``--help``. Why: evaluation/DECISIONS.md (entry written after the run).
"""

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

import ragas
from dotenv import load_dotenv
from langchain_core.documents import Document
from openai import OpenAI
from ragas.embeddings import OpenAIEmbeddings
from ragas.llms import llm_factory
from ragas.testset import TestsetGenerator
from ragas.testset.graph import KnowledgeGraph, NodeType
from ragas.testset.synthesizers.testset_schema import Testset
from ragas.utils import num_tokens_from_string

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))  # lets the script run by path from any directory

from data_ingestion.pipeline import generate_run_id  # noqa: E402  (needs the path set above)

logger = logging.getLogger(__name__)

MANIFEST_PATH = PROJECT_ROOT / "data/fastapi/normalized/github-docs/v1/manifest.json"
OUTPUTS_DIR = PROJECT_ROOT / "evaluation" / "outputs"
OUTPUT_FILE = "ragas_testset.json"
GRAPH_FILE = "knowledge_graph.json"

EMBEDDING_MODEL = "text-embedding-3-small"
# Fixed here, with no --size: the set is compared with the 40 synthetic questions.
TESTSET_SIZE = 40
# RAGAS's default transforms take headlines only from pages over this many tokens, and its
# splitter then raises on any page without headlines, so shorter pages are left out.
SHORT_PAGE_TOKENS = 500
# RAGAS starts each context of a multi-hop sample with "<1-hop>", "<2-hop>", ...
HOP_LABEL = re.compile(r"^<\d+-hop>\s*")


def load_documents() -> list[Document]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    docs_dir = MANIFEST_PATH.parent / "docs"
    return [
        Document(
            page_content=(docs_dir / entry["output_filename"]).read_text(encoding="utf-8"),
            metadata={"source_path": entry["source_path"]},
        )
        for entry in manifest["files"]
    ]


def lookup_key(text: str) -> str:
    return " ".join(HOP_LABEL.sub("", text).split())


def to_questions(
    samples: list[dict], knowledge_graph: KnowledgeGraph
) -> tuple[list[dict], list[dict]]:
    """Return the questions with their gold pages, and the samples that could not be matched.

    A sample with a context the graph does not hold is unmatched; its pages are never guessed.
    """
    source_of_text = {}
    for node in knowledge_graph.nodes:
        if node.type == NodeType.DOCUMENT:
            key = lookup_key(node.properties["page_content"])
            source_of_text[key] = node.properties["document_metadata"]["source_path"]
    # A chunk node carries no metadata: its page is the document node it was split from.
    for relationship in knowledge_graph.relationships:
        if relationship.type == "child":
            key = lookup_key(relationship.target.properties["page_content"])
            page = relationship.source.properties["document_metadata"]["source_path"]
            source_of_text[key] = page

    questions, unmatched = [], []
    for sample in samples:
        # RAGAS can return a sample with no contexts, and to_list() drops a None field.
        contexts = sample.get("reference_contexts") or []
        sources = [source_of_text.get(lookup_key(context)) for context in contexts]
        if not sources or None in sources:
            unmatched.append({"question": sample["user_input"], "reference_contexts": contexts})
            continue
        questions.append(
            {
                "question_id": f"rag-{len(questions) + 1:02d}",
                "question": sample["user_input"],
                "reference_answer": sample.get("reference"),
                "gold_sources": list(dict.fromkeys(sources)),
                "origin": "ragas",
                "synthesizer_name": sample["synthesizer_name"],
                # RAGAS leaves these three out of a multi-hop sample.
                "persona_name": sample.get("persona_name"),
                "query_style": sample.get("query_style"),
                "query_length": sample.get("query_length"),
                "reference_contexts": contexts,
            }
        )
    return questions, unmatched


def generate_testset(pages: list[Document], model: str) -> tuple[Testset, TestsetGenerator]:
    try:
        # Counted as RAGAS's own filter counts, so both agree on which pages are long.
        documents = [
            page for page in pages if num_tokens_from_string(page.page_content) > SHORT_PAGE_TOKENS
        ]
        logger.info(
            "RAGAS test set: generating %d questions from %d pages with %s (paid); "
            "%d pages of %d tokens or fewer are left out",
            TESTSET_SIZE,
            len(documents),
            model,
            len(pages) - len(documents),
            SHORT_PAGE_TOKENS,
        )
        # Sync: RAGAS runs each step in a new event loop, where a connection an async client
        # kept alive fails. RAGAS's own retries do not cover an llm_factory model, so a
        # failed call has only this client's retries.
        client = OpenAI(max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "5")))
        generator = TestsetGenerator(
            llm=llm_factory(model, client=client),
            embedding_model=OpenAIEmbeddings(client=client, model=EMBEDDING_MODEL),
        )
        testset = generator.generate_with_langchain_docs(documents, testset_size=TESTSET_SIZE)
    except Exception:  # noqa: BLE001  (any failure of the paid run: exit 1, write nothing)
        logger.exception("RAGAS test set: generation failed; nothing written")
        raise SystemExit(1) from None
    return testset, generator


def write_run(
    questions: list[dict], unmatched: list[dict], model: str, knowledge_graph: KnowledgeGraph
) -> None:
    output = {
        "ragas_version": ragas.__version__,
        "model": model,
        "embedding_model": EMBEDDING_MODEL,
        "testset_size": TESTSET_SIZE,
        "questions": questions,
        "unmatched": unmatched,
    }
    run_dir = OUTPUTS_DIR / generate_run_id()
    run_dir.mkdir(parents=True)
    # Written first, so a failed write never leaves a folder holding only the graph.
    (run_dir / OUTPUT_FILE).write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    # Saved so the gold pages can be mapped again without paying for another run.
    knowledge_graph.save(run_dir / GRAPH_FILE)
    logger.info(
        "RAGAS test set: wrote %d questions and %d unmatched samples to %s",
        len(questions),
        len(unmatched),
        run_dir,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=f"Generate {TESTSET_SIZE} questions from the normalized FastAPI pages of "
        f"more than {SHORT_PAGE_TOKENS} tokens with RAGAS's test-set generator and write them, "
        "with RAGAS's knowledge graph, to a new folder under evaluation/outputs. The run makes "
        f"PAID OpenAI calls (the chat model in OPENAI_MODEL and {EMBEDDING_MODEL}; a failed "
        "call is retried up to OPENAI_MAX_RETRIES times) and takes no options.",
    )
    parser.parse_args(argv)

    # Configure once from the entry point, in the format the stages use.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    # httpx (under the OpenAI client) logs one INFO line per request.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Also carries RAGAS_DO_NOT_TRACK, which RAGAS reads at its first tracking call.
    load_dotenv(PROJECT_ROOT / ".env")

    try:
        pages = load_documents()  # every page is read before the first paid call
    except OSError as exc:  # a missing manifest or page
        logger.error("RAGAS test set: cannot read the corpus: %s", exc)
        raise SystemExit(1) from None

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    testset, generator = generate_testset(pages, model)
    questions, unmatched = to_questions(testset.to_list(), generator.knowledge_graph)
    write_run(questions, unmatched, model, generator.knowledge_graph)


if __name__ == "__main__":
    main()
