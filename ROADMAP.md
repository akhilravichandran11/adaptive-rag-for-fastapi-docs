# Roadmap

**Adaptive RAG for the FastAPI documentation** — a corrective/adaptive RAG (CRAG)
application built over the FastAPI docs. This roadmap tracks the project as a
sequence of pipeline capabilities, from raw source collection through retrieval
and evaluation.

Status legend: **Done** · **In progress** · **Planned**

---

## Overview

The system is organized as a linear data pipeline. Each stage reads the previous
stage's artifacts and writes its own, so stages can be run, re-run, and verified
independently:

```
collect  →  normalize  →  curate  →  chunk  →  index  →  retrieve  →  evaluate
```

Raw and generated artifacts (`data/`, `outputs/`) are kept out of version control;
the code that produces them is the deliverable.

---

## 1. Source collection — **Done**

Reproducible, provenance-tracked collection of the FastAPI documentation.

- Documentation collector pinned to a specific upstream commit, writing a
  source-first layout (`data/fastapi/raw/<source>/<version>/`) with a
  per-source `manifest.json` (source, collection stats, and per-file routing).
- Referenced-code collector that parses the docs' code-include macros, resolves
  every referenced source file at the pinned commit, and fetches them with
  back-links recorded in a `code_manifest.json`.
- Deterministic file identities so re-collection is idempotent.

## 2. Normalization — **Done**

- Inliner that resolves the docs' code-include directives, honoring line-range
  slices, and emits self-contained normalized documents with source provenance
  comments preserved.

## 3. Curation — **In progress**

An LLM-assisted quality pass over the normalized corpus, structured as
independently runnable stages that share a per-run output directory.

- **Stage 1 — Content quality analysis — Done.** Scores every document across
  quality dimensions using structured (schema-validated) model outputs, with
  bounded concurrency, fail-fast setup validation, per-run metadata, and a
  quality-tiered report. Includes prompt-injection quarantine so untrusted
  content is treated as data and flagged out of the curated set.
- **Stage 2 — Knowledge-domain extraction — In progress.** Migrate to the
  shared per-run artifact contract and structured outputs; resolve its input to
  the latest upstream artifact rather than a fixed name.
- **Stage 3 — Learning taxonomy — Planned.** Same contract and structured-output
  treatment.
- **Stage 5 — Synthetic dataset generation — Planned.** Generate evaluation
  question/answer pairs from curated content.

## 4. Pipeline orchestration — **In progress**

- Shared run-directory contract: one run id per invocation, each stage reading
  and writing fixed artifact names under `outputs/<run_id>/` — **Done.**
- Orchestrator with fail-fast execution, single-stage (`--only`) and resume
  (`--resume`) modes, and a document limit passthrough for cheap dry runs —
  **Done** (currently drives Stage 1).
- Wire Stages 2, 3, and 5 into the orchestrator once they adopt the run-directory
  contract; raise loudly on missing input instead of degrading silently —
  **Planned.**

## 5. Chunking — **Planned**

- Chunk the normalized/curated documents for retrieval, preserving source and
  section metadata and keeping full-file links for reference.

## 6. Indexing (Qdrant) — **In progress**

- Qdrant collection setup and configuration — **Done.**
- Index curated/chunked documents into a single collection, with `source` and
  routing information in the payload and deterministic point IDs for idempotent
  upserts — **Planned.**

## 7. Retrieval & adaptive RAG — **Planned**

- Retrieval over the indexed corpus.
- Corrective/adaptive RAG control flow (relevance grading, query rewriting,
  fallback strategies).

## 8. Evaluation — **Planned**

- Use the synthetic Q/A pairs as a retrieval/answer evaluation set.
- Track quality metrics across pipeline changes.

---

## Engineering practices

- **Structured LLM outputs** (Pydantic schemas) instead of free-text parsing.
- **Bounded concurrency** for API-bound stages.
- **Prompt-injection defense**: document content is delimited and treated as
  untrusted data; suspicious content is quarantined from curated output.
- **Provenance**: pinned upstream commit, per-source manifests, per-run metadata.
- **Agent-assisted workflow**: implementation work is tracked as tracer-bullet
  tickets with explicit dependencies (see
  [`docs/agents/issue-tracker.md`](docs/agents/issue-tracker.md)).
