# Adaptive RAG for FastAPI Docs

A corrective/adaptive RAG (CRAG) system for answering questions about the
FastAPI documentation. It collects the docs from a pinned upstream commit,
normalizes them into self-contained documents, runs an LLM-assisted curation
pipeline to prepare candidate content for review, then chunks and indexes the
corpus for retrieval.

> Early development. Source collection, normalization, chunking, indexing, and
> hybrid retrieval work, and the CRAG pipeline answers questions from the command
> line, over HTTP, and on a web page. Evaluation is planned. See
> [ROADMAP.md](ROADMAP.md) for the full status.

## Pipeline

The system is a linear, re-runnable data pipeline. Each stage reads the previous
stage's artifacts and writes its own:

```
collect  →  normalize  →  curate  →  chunk  →  index  →  retrieve  →  evaluate
```

| Stage     | Status      |
| --------- | ----------- |
| collect   | Done        |
| normalize | Done        |
| curate    | Deferred    |
| chunk     | Done        |
| index     | In progress |
| retrieve  | Done        |
| evaluate  | Planned     |

Chunking compares seven strategies on the same corpus (`naive_small`,
`naive_medium`, `naive_large`, `sentence`, `recursive`, `semantic`,
`markdown_ast`). Indexing writes one Qdrant collection per strategy × embedder
pair.

## Tech stack

- Orchestration: [Haystack](https://haystack.deepset.ai/) 2.x
- Vector store: [Qdrant](https://qdrant.tech/)
- Embeddings: [Voyage AI](https://www.voyageai.com/) and BGE-large via
  [OpenRouter](https://openrouter.ai/), with a pinned local BGE tokenizer
- LLMs for curation: Google Gemini and OpenAI
- Validation: Pydantic for selected structured LLM outputs
- Tooling: [uv](https://docs.astral.sh/uv/) for packaging, Ruff for lint and
  format (run through pre-commit), pytest, Python 3.11+

## Project structure

```text
data_collection/    # Collect FastAPI docs + referenced code from a pinned commit
  data_normalization/ # Inline referenced code into self-contained documents
data_curation/      # LLM-assisted quality pipeline (stage runner + stages)
data_ingestion/     # Qdrant setup, chunking strategies, embedders, indexing
retrieval/          # Dense baseline RAG, and hybrid BM25 + dense search, reranked
crag/               # CRAG pipeline: grade chunks, pick a route, search the web, answer
scripts/            # Model downloads, chunk-size report, CI helpers
evaluation/         # Retrieval / answer evaluation           (planned)
backend/            # FastAPI service: POST /query, GET /health, the page at /
frontend/           # Static page for asking questions, served by the backend
experiments/        # Ad-hoc experiments
docs/               # Conventions and agent-workflow docs
tests/              # Kept local for now, while the testing approach settles
```

## Getting started

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
# 1. Install dependencies, including the dev tools (pytest, ruff)
uv sync --extra dev

# 2. Download the local models the chunkers need (BGE tokenizer, NLTK punkt)
uv run python scripts/download_models.py          # --check only reports

# 3. Configure secrets
cp .env.example .env
```

Fill in the keys for the stages you run: `GOOGLE_API_KEY` and `OPENAI_API_KEY`
for curation; `QDRANT_URL` and `QDRANT_API_KEY` for indexing; `VOYAGE_API_KEY`
and/or `OPENROUTER_API_KEY` for embeddings.

## Running the pipeline

Commands run from the project root. Steps marked **paid** call a paid API.

### 1. Collect

The default for future collection is a local checkout at the pinned FastAPI
commit. Prepare the checkout using the
[pinned snapshot instructions](data_collection/pinned_snapshot/README.md#prepare-a-checkout-and-collect),
then use one fresh run directory for separate raw and normalized candidates:

```bash
run=".scratch/data-collection-pipeline/targets/github-docs-source/plans/direct-snapshot/adoption-verification/manual-01"
uv run python -m data_collection.pinned_snapshot.collect \
  --snapshot-dir "$run/upstream" \
  --output-dir "$run/raw"
```

### 2. Normalize

```bash
uv run python -m data_collection.data_normalization.normalize_github_docs \
  --raw-dir "$run/raw" \
  --output-dir "$run/normalized"
```

This inlines referenced code into the separate normalized candidate. Supply both
path arguments; the output must be fresh, outside the input and active data trees.
The no-argument normalizer remains available for compatibility with the active
paths, but use explicit paths for candidate runs.

The active corpus has not been switched. Inspect the candidate and obtain separate
approval before manual promotion. Back up and replace the complete active raw and
normalized directories (`data/fastapi/{raw,normalized}/github-docs/v1/`) rather
than merging files. Regenerate saved chunks for the new manifest fingerprint
before indexing. These promotion steps are not performed by the commands above.

### 3. Curate (**paid**)

```bash
uv run python data_curation/stage0_run_pipeline.py --limit 5 --only 1   # small smoke run
```

### 4. Chunk

```bash
uv run python data_ingestion/stage2_chunk_corpus.py --strategy naive_small --curation-run <run_id>
uv run python data_ingestion/stage2_chunk_corpus.py --strategy all --curation-run <run_id>   # includes semantic (paid)
uv run python scripts/chunk_size_report.py      # shape and size report for the free strategies
```

### 5. Index (**paid**: embeds with Voyage or OpenRouter)

```bash
uv run python data_ingestion/stage1_setup_qdrant.py                       # attach to the Qdrant store (--recreate wipes it)
uv run python data_ingestion/stage3_index_chunks.py --chunks-run <run_id> --strategy all --embedder bge
uv run python data_ingestion/stage3_index_chunks.py --chunks-run <run_id> --strategy all --embedder voyage
```

Each collection is named `<prefix>__<strategy>__<embedder>`. Stage 3 refuses a
chunk run made from a different corpus build.

### 6. Ask (**paid**: Voyage, Gemini and Tavily)

From the command line, over HTTP or on a web page: see [crag/RUNNING.md](crag/RUNNING.md).

## Tests and checks

```bash
uv run pytest                          # tests never call a paid API
uvx pre-commit run --all-files         # ruff check + ruff format
```

## Documentation

- [ROADMAP.md](ROADMAP.md): status of each capability and what comes next.
- [data_ingestion/DECISIONS.md](data_ingestion/DECISIONS.md): ingestion design
  decisions, newest first.
- [docs/conventions.md](docs/conventions.md): code conventions and their
  rulings.
- [docs/simplification-conventions.md](docs/simplification-conventions.md):
  code-shape rules that keep the Python readable.
- [docs/agents/issue-tracker.md](docs/agents/issue-tracker.md): the local,
  agent-assisted planning and ticket workflow.
- [AGENTS.md](AGENTS.md) and [CLAUDE.md](CLAUDE.md): instructions for the
  coding agents that work in this repo.

## AI assistance and authorship

This project was developed with assistance from OpenAI Codex and Anthropic
Claude Code for brainstorming, planning, implementation, code review,
debugging, documentation, and analysis of generated artifacts. The test suite
was largely written with these tools; I agreed what each test covers and ran
the suite.

Third-party sources for the agent workflow (skills and agents it builds on) are
listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

I defined the project scope and architecture, approved implementation changes, ran the pipeline, verified its structural contracts, reviewed its generated outputs and limitations, and remain responsible for the final engineering decisions and published content.

## License

See [LICENSE](LICENSE).
