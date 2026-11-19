# Roadmap

Adaptive RAG for the FastAPI documentation is a corrective/adaptive RAG (CRAG)
application. This roadmap tracks it as a sequence of pipeline capabilities, from
raw source collection through retrieval and evaluation.

Statuses: Done, In progress, Deferred, Planned.

## Overview

The system is a linear data pipeline. Each stage reads the previous stage's
artifacts and writes its own, so each stage can be re-run and checked on its
own:

```
collect  →  normalize  →  curate  →  chunk  →  index  →  retrieve  →  evaluate
```

Raw and generated artifacts (`data/`, `outputs/`) are kept out of version control;
the code that produces them is the deliverable.

## 1. Source collection: Done

Reproducible, provenance-tracked collection of the FastAPI documentation.

- Documentation collector pinned to a specific upstream commit, writing a
  source-first layout (`data/fastapi/raw/<source>/<version>/`) with a
  per-source `manifest.json` (source, collection stats, and per-file routing).
- Referenced-code collector that parses the docs' code-include macros, resolves
  every referenced source file at the pinned commit, and fetches them with
  back-links recorded in a `code_manifest.json`.
- Deterministic file identities so re-collection is idempotent.
- A collector that reads a local pinned checkout (no network, bytes verified
  against Git blobs) is the default for future collection. The active corpus
  still comes from the collectors above; replacing it requires separate approval.

## 2. Normalization: Done

- Inliner that resolves the docs' code-include directives, honoring line-range
  slices, and emits self-contained normalized documents with source provenance
  comments preserved.

## 3. Curation: Stage 1 metadata retained; v2 planned

An LLM-assisted quality pass over the normalized corpus, structured as
independently runnable stages that share a per-run output directory.

Stage 1's existing analysis remains an ingestion dependency at
`data_curation/outputs/20260925_032632/content_analysis.json`. A revised
curation approach is being planned; the guide-generation workflow is retired.

All four stages remain executable from the existing orchestrator. Stages 2 to 4
now live under `data_curation/legacy/`:

- Stage 1, content quality analysis: scores every document across quality
  dimensions using structured (schema-validated) model outputs, with bounded
  concurrency, fail-fast setup validation, per-run metadata, and a
  quality-tiered report. Includes prompt-injection quarantine so untrusted
  content is treated as data and flagged out of the curated set.
- Stage 2 (retired), knowledge-domain extraction: selects qualifying documents, extracts
  concepts and their relationships, and builds an initial taxonomy.
- Stage 3 (retired), final taxonomy and synthesis plan: produces the learning taxonomy
  and a plan for candidate documents, selecting sources by run-local ID, then
  derives an implementation roadmap from them.
- Stage 4 (retired), candidate document generation: writes the planned documents,
  grounded in the sources each plan entry declares.
- A validated offline source inventory, the first piece of the revised
  approach, is built (`data_curation/pipeline_v2/`); further analysis and
  ingestion wiring are not implemented.

## 4. Pipeline orchestration: Done

- Shared run-directory contract: one run id per invocation, each stage reading
  and writing fixed artifact names under `outputs/<run_id>/`.
- Orchestrator with fail-fast execution, single-stage (`--only`) and resume
  (`--resume`) modes, and a document limit for small paid Stage 1 runs. The
  Stage 1-only command is unchanged; Stages 2 to 4 dispatch to `data_curation/legacy/`
  for the retired workflow. Existing outputs stay under `data_curation/outputs/`.

## 5. Chunking: Done

- Seven strategies compared on the same corpus: three fixed-size word sweeps
  (`naive_small`, `naive_medium`, `naive_large`), `sentence`, `recursive`,
  `semantic` (embedding-based, paid), and `markdown_ast` (heading-aware
  Markdown with Python AST splitting for code).
- Every chunk carries the same metadata fields whatever strategy produced it,
  including its source path and position in the parent document.
- Chunk runs are saved with the corpus build they came from, so indexing can
  refuse a chunk run made from a different corpus.
- A chunk-size report checks every strategy's chunks against shared shape rules
  (sizes are checked and reported, not enforced).

## 6. Indexing (Qdrant): Done

- Qdrant collection setup and configuration (done).
- Embedders (done): Voyage AI and BGE-large via OpenRouter, with a pinned local
  BGE tokenizer to measure truncation.
- Index saved chunk runs into one collection per strategy × embedder pair, with
  payload indexes and fixed point IDs, after checking the corpus build and chunk
  counts (done).
- Live end-to-end runs across all 14 pairs completed on 2026-10-15, including
  the successful retry of Voyage's `markdown_ast` collection.
- Choosing the pairs that go forward to retrieval (in progress).

## 7. Retrieval and adaptive RAG: Done

- Retrieval over the indexed corpus.
- Corrective/adaptive RAG control flow (relevance grading, query rewriting,
  fallback strategies).

## 8. Evaluation: Done (v1)

Evaluation v1 is done: two runs and one comparison. The LLM answer judge and a
chunk viewer are planned for December.

- Question sets (done): 10 handwritten and 40 synthetic questions, each naming
  the pages that answer it (`evaluation/questions/`).
- Retrieval scoring (done): recall@1, @3, @5 and @10 and MRR at page level,
  computed from saved results files with no API call
  (`evaluation/retrieval_metrics.py`).
- Dense vs hybrid retrieval (done): two runs on `markdown_ast` × Voyage,
  compared in
  [`evaluation/results/dense-vs-hybrid.md`](evaluation/results/dense-vs-hybrid.md).
- LLM answer judge and chunk viewer (planned, December).

## Engineering practices

- LLM outputs are parsed into Pydantic schemas.
- API-bound stages run with bounded concurrency.
- Document content is delimited and treated as untrusted data, and suspicious
  content is quarantined from curated output, to defend against prompt
  injection.
- Provenance is recorded through the pinned upstream commit, per-source
  manifests, and per-run metadata.
- Implementation work is tracked as tracer-bullet tickets with explicit
  dependencies (see [`docs/agents/issue-tracker.md`](docs/agents/issue-tracker.md)).
