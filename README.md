# Adaptive RAG for FastAPI Docs

A corrective RAG (CRAG) system that answers questions about the FastAPI
documentation. Each question goes through hybrid retrieval: BM25 and dense
search over Qdrant, fused and then reranked by Voyage. Gemini grades the chunks
that come back. When they miss the question, or cover only part of it, the
pipeline searches the web with Tavily, and Gemini writes the answer with
numbered sources. You can ask it from the command line, over HTTP
(`POST /query`), or on a web page served by the same FastAPI app. Its retrieval
is evaluated on 50 questions, dense against hybrid.

**Status:** evaluation v1 is done (two runs, one comparison). The LLM answer
judge and a chunk viewer are planned for December. See [ROADMAP.md](ROADMAP.md).

## Retrieval results

Both runs retrieve the top 10 chunks from
`fastapi_docs__markdown_ast__voyage`, the collection CRAG searches, for the same
50 questions (10 handwritten, 40 synthetic). Only the retrieval method changes.
Dense is Qdrant vector search alone, in systems named
`baseline__…`. Hybrid runs BM25 and vector search, takes 50 candidates from
each, fuses them by reciprocal rank fusion and reranks them with `rerank-2.5`.

| system | set | n | recall@1 | recall@3 | recall@5 | recall@10 | MRR | no chunks | retrieval cost per question (upper bound) |
|---|---|---|---|---|---|---|---|---|---|
| baseline__markdown_ast__voyage | handwritten | 10 | 0.70 | 0.80 | 0.85 | 0.85 | 0.85 | 0 | ≤ $0.0000014 |
| baseline__markdown_ast__voyage | synthetic | 40 | 0.80 | 0.97 | 0.97 | 1.00 | 0.88 | 0 | ≤ $0.0000010 |
| hybrid__markdown_ast__voyage | handwritten | 10 | 0.50 | 1.00 | 1.00 | 1.00 | 0.80 | 0 | ≤ $0.0104 |
| hybrid__markdown_ast__voyage | synthetic | 40 | 0.85 | 0.97 | 0.97 | 1.00 | 0.90 | 0 | ≤ $0.0104 |

With 10 handwritten questions, one question moves a handwritten score by 0.10.

The runs are dense [`20261117_103532`](evaluation/results/20261117_103532/) and
hybrid [`20261116_055637`](evaluation/results/20261116_055637/). The comparison,
with how the cost column is worked out and the commands, is in
[evaluation/results/dense-vs-hybrid.md](evaluation/results/dense-vs-hybrid.md).
The results record no timing, so the table has no latency column.

## Findings

**Hybrid finds every handwritten gold page by rank 3, and dense wins the first
slot.** On the 10 handwritten questions, hybrid's recall@3 is 1.00 against
dense's 0.80, and dense never retrieves the gold page for hw-02 ("How do I
configure FastAPI?"). Dense puts a gold page first more often: recall@1 is 0.70
against 0.50, because it ranks the gold page first on hw-01 and hw-05, where
hybrid ranks it second. On the 40 synthetic questions, recall@3, @5 and @10 are
the same for both (0.97, 0.97, 1.00). The rerank is nearly all of hybrid's
retrieval cost, at most about $0.01 per question, against at most $0.0000014
for dense's one query embedding. Source:
[dense-vs-hybrid.md](evaluation/results/dense-vs-hybrid.md#what-changed).

**Haystack's defaults lose text without an error.** The loader reads the docs as
plain text, because two Haystack defaults erase the structure the heading-based
chunkers split on. Its Markdown converter drops the `#` heading markers and the
code fences, and its `DocumentCleaner` removes blank lines and collapses runs of
whitespace, which flattens code indentation
([data_ingestion/loading.py](data_ingestion/loading.py)). The chunk-size
report's "no text lost" rule caught a second case: on Haystack 3.0.0, the
`recursive` splitter lost text in 4 of the 124 documents, which is why the
project requires Haystack 3.2.0
([data_ingestion/DECISIONS.md, 2026-10-08](data_ingestion/DECISIONS.md#2026-10-08--chunk-sizes-are-checked-and-reported-not-enforced)).

**79.5% of `naive_medium` chunks are longer than BGE-large can read.** The first
chunk-size report counted them with BGE's own tokenizer. 352 of the 443
`naive_medium` chunks go over BGE's 512-token input limit, and each loses an
average of 317 tokens when it is embedded, while Voyage reads the same chunks
whole. Each strategy keeps its chunks at the size it is meant to have instead
of capping them at 512 tokens. So
[`scripts/chunk_size_report.py`](scripts/chunk_size_report.py) measures, per
strategy, how many chunks go over each embedder's limit
([data_ingestion/DECISIONS.md, 2026-09-20](data_ingestion/DECISIONS.md#2026-09-20--bge-large-is-the-second-embedder-not-the-main-one)
and
[2026-10-08](data_ingestion/DECISIONS.md#2026-10-08--chunk-sizes-are-checked-and-reported-not-enforced)).

## Run it

These commands assume the setup in [Getting started](#getting-started), and
the chunk run `full_chunks_20261015` that stage 2 builds
([crag/RUNNING.md](crag/RUNNING.md#what-must-already-exist)). All three are
**paid**.

```bash
# Index the markdown_ast chunks into Qdrant (paid: Voyage)
uv run python data_ingestion/stage3_index_chunks.py --chunks-run full_chunks_20261015 --strategy markdown_ast --embedder voyage

# Ask one question from the command line (paid: Voyage and Gemini, plus Tavily on two of the routes)
uv run python crag/run.py --question "How do I add CORS middleware?"

# Run the eval: hybrid retrieval and Gemini answers for all 50 questions (paid: Voyage and Gemini)
uv run python evaluation/generate_results.py --strategy markdown_ast --embedder voyage --retrieval hybrid
```

The server, the web page and the other options are in
[crag/RUNNING.md](crag/RUNNING.md). Scoring a saved run is free; its commands
are in [dense-vs-hybrid.md](evaluation/results/dense-vs-hybrid.md#commands).

## Architecture

A question goes from `POST /query` through hybrid retrieval to Gemini's grading
of the chunks. The grade picks a route: answer from the chunks, search again
and on the web for the parts they miss, or answer from the web. The diagram is
in [docs/architecture.md](docs/architecture.md).

## Pipeline

The CRAG pipeline answers from a corpus that this project builds. It collects
the docs from a pinned upstream commit and normalizes them into self-contained
documents. It attaches saved Stage 1 quality metadata, then chunks and indexes
the corpus for retrieval.

The system is a linear, re-runnable data pipeline. Each stage reads the previous
stage's artifacts and writes its own:

```
collect  →  normalize  →  curate  →  chunk  →  index  →  retrieve  →  evaluate
```

| Stage     | Status      |
| --------- | ----------- |
| collect   | Done        |
| normalize | Done        |
| curate    | Stage 1 metadata retained; v2 planned |
| chunk     | Done        |
| index     | Done        |
| retrieve  | Done        |
| evaluate  | Done (v1)   |

Chunking compares nine strategies on the same corpus (`naive_small`,
`naive_medium`, `naive_large`, `sentence`, `recursive`, `semantic`,
`markdown_ast`, `markdown_sections`, `markdown_headers`). Indexing writes one
Qdrant collection per strategy × embedder pair.

## Tech stack

- Orchestration: [Haystack](https://haystack.deepset.ai/) 3.x
- Vector store: [Qdrant](https://qdrant.tech/)
- Embeddings: [Voyage AI](https://www.voyageai.com/) and BGE-large via
  [OpenRouter](https://openrouter.ai/), with a pinned local BGE tokenizer
- Reranking: Voyage `rerank-2.5`
- LLMs: Google Gemini is CRAG's answer model, and it also grades the chunks and
  writes the follow-up queries. Gemini and OpenAI run curation.
- Web search: [Tavily](https://www.tavily.com/), CRAG's fallback
- Validation: Pydantic for selected structured LLM outputs
- Tooling: [uv](https://docs.astral.sh/uv/) for packaging, Ruff for lint and
  format (run through pre-commit), pytest, Python 3.11+

## Project structure

```text
data_collection/    # Collect FastAPI docs + referenced code from a pinned commit
  data_normalization/ # Inline referenced code into self-contained documents
data_curation/      # Stage 1 metadata, Stage 5 evaluation questions, shared runner/modules
  legacy/          # Retired guide-generation stages 2–4
  pipeline_v2/     # Offline source inventory; further curation work is planned
data_ingestion/     # Qdrant setup, chunking strategies, embedders, indexing
retrieval/          # Dense baseline RAG, and hybrid BM25 + dense search, reranked
crag/               # CRAG pipeline: grade chunks, pick a route, search the web, answer
scripts/            # Model downloads, chunk-size report, CI helpers
evaluation/         # Question sets, results runs, retrieval scoring
backend/            # FastAPI service: POST /query, GET /health, the page at /
frontend/           # Static page for asking questions, served by the backend
experiments/        # Ad-hoc experiments
docs/               # Architecture diagram, conventions, agent-workflow docs
tests/              # The pytest suite, run by CI on every push
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
and/or `OPENROUTER_API_KEY` for embeddings; `GOOGLE_API_KEY`, `VOYAGE_API_KEY`
and `TAVILY_API_KEY` for asking (step 6). `.env.example` already names the Gemini
models.

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

Ingestion reads the explicitly selected run's Stage 1 analysis at
`data_curation/outputs/<run_id>/content_analysis.json`; the existing run
`20260925_032632` remains in place. Guide generation (Stages 2 to 4) is retired under
`data_curation/legacy/`. See [the curation README](data_curation/README.md).

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

The suite never calls a paid API, and the [Tests workflow](.github/workflows/tests.yml) runs it on every push and pull request.

## Documentation

- [ROADMAP.md](ROADMAP.md): status of each capability and what comes next.
- [Query architecture](docs/architecture.md) shows how a question moves from retrieval to answer.
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

IDs such as PRT-11, D34 or F008 in comments refer to private planning records;
the reasons behind them are in each package's `DECISIONS.md` and in
`docs/conventions.md`.

## AI assistance and authorship

This project was developed with assistance from OpenAI Codex and Anthropic
Claude Code for brainstorming, planning, implementation, code review,
debugging, documentation, and analysis of generated artifacts. The test suite
was written by these tools, test-first since October using an adapted version
of Matt Pocock's `tdd` skill. For each ticket I approved what its tests had to
check and which interface they test through, and I ran the suite; I did not
review the test code line by line.

Third-party sources for the agent workflow (skills and agents it builds on) are
listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

**Who built it.** I built this project. The coding agents named above drafted
most of the code from tickets I approved; I ran it, reviewed it and am
responsible for it. The corrective-RAG design follows the CRAG paper:
Yan et al., *Corrective Retrieval Augmented Generation*, 2024
([arXiv:2401.15884](https://arxiv.org/abs/2401.15884)).

## License

See [LICENSE](LICENSE).
