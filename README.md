# Adaptive RAG for FastAPI Docs

A corrective/adaptive RAG (CRAG) system for answering questions about the
**FastAPI documentation**. It collects the docs from a pinned upstream commit,
normalizes them into self-contained documents, and runs an LLM-assisted curation
pipeline to prepare for a candidate content for review and eventual retrieval.

> 🚧 **Early development.** Source collection, normalization, and curation
> are working. Chunking, indexing, retrieval, and evaluation are
> planned — see [ROADMAP.md](ROADMAP.md) for the full status.

## Pipeline

The system is a linear, re-runnable data pipeline. Each stage reads the previous
stage's artifacts and writes its own:

```
collect  →  normalize  →  curate  →  chunk  →  index  →  retrieve  →  evaluate
```

| Stage       | Status        |
| ----------- | ------------- |
| collect     | ✅ Done        |
| normalize   | ✅ Done        |
| curate      | 🔨 In progress |
| chunk       | 📋 Planned     |
| index       | 🔨 In progress |
| retrieve    | 📋 Planned     |
| evaluate    | 📋 Planned     |

See [ROADMAP.md](ROADMAP.md) for the per-stage breakdown.

## Tech stack

- **Orchestration:** [Haystack](https://haystack.deepset.ai/) 2.x
- **Vector store:** [Qdrant](https://qdrant.tech/)
- **LLMs / embeddings:** OpenAI and Google Gemini
- **Validation:** Pydantic for selected structured LLM outputs
- **Tooling:** [uv](https://docs.astral.sh/uv/) (packaging), Ruff (lint), Python 3.11+

## Project structure

```text
data_collection/    # Collect FastAPI docs + referenced code from a pinned commit
  data_normalization/ # Inline referenced code into self-contained documents
data_curation/      # LLM-assisted quality pipeline (stage runner + stages)
data_ingestion/     # Vector-store setup and indexing        (in progress)
evaluation/         # Retrieval / answer evaluation           (planned)
backend/            # API service                             (planned)
frontend/           # UI                                      (planned)
experiments/        # Ad-hoc experiments
docs/               # Project and agent-workflow docs
tests/              # Tests
```

## Getting started

Requires **Python 3.11+** and [uv](https://docs.astral.sh/uv/).

```bash
# 1. Install dependencies
uv sync

# 2. Configure secrets
cp .env.example .env   # then fill in your OPENAI_API_KEY
```
## Running the pipeline

### Data Collection

## To Do

- [ ] Add `data_normalization` stage steps

### Data Curation

```bash
uv run python data_curation/stage0_run_pipeline.py --limit 5 --only 1
```

## Documentation

- [ROADMAP.md](ROADMAP.md) — capability status and what's next.
- [docs/agents/issue-tracker.md](docs/agents/issue-tracker.md) — the local,
  agent-assisted planning and ticket workflow.

## AI assistance and authorship

This project was developed with assistance from OpenAI Codex for
brainstorming, code review, debugging, documentation, and analysis of generated
artifacts.

I defined the project scope and architecture, approved implementation changes, ran the pipeline, verified its structural contracts, reviewed its generated outputs and limitations, and remain responsible for the final engineering decisions and published content.
