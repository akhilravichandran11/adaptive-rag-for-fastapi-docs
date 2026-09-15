# LLM-assisted data curation for RAG

## Summary

This project explores the design of an LLM-assisted data-curation layer for a documentation RAG system. The four-stage pipeline preserves stable document identities, produces structured quality analysis, selects qualifying content, synthesizes knowledge domains, builds a deterministic taxonomy within defined boundaries, creates a proposed curriculum and content plan, and generates candidate learning documents.

A complete Stage 1–4 run was preserved and audited. The review confirmed the pipeline’s run-directory, artifact, schema, and transformation contracts while also exposing limitations common to probabilistic data pipelines: model-dependent scoring, cross-domain concept repetition, insufficient source-level provenance, semantic contract drift between stages, unenforced generation requirements, technically unreliable examples, and unsupported planning metrics.

Generated scores, taxonomies, gap assessments, roadmaps, and synthetic documents are therefore treated as structured candidates for human review rather than ground truth. The project separates generation, evidence, analysis, findings, plans, and executable tickets so that successful model output is never confused with validated data quality or publication readiness.

## Overview

This directory contains an LLM-assisted data-curation pipeline for a documentation RAG system. The pipeline analyzes normalized FastAPI documentation, selects qualifying source material, synthesizes knowledge domains, constructs learning taxonomies, plans a candidate dataset, and generates candidate learning documents.

The purpose of this layer is not to let an LLM declare content correct. Its purpose is to turn an unreviewed documentation corpus into structured, inspectable artifacts that can be evaluated before content enters retrieval and answer generation.

All generated scores, taxonomies, gap assessments, roadmaps, and learning documents are treated as candidates for human review rather than ground truth.

## Why a curation layer exists

A source document can be technically valid while still being a poor retrieval unit. It may be incomplete, overly narrow, duplicated elsewhere, dependent on surrounding context, or unsuitable for the intended audience.

This pipeline explores how a curation layer can make those concerns explicit by:

- preserving stable source-document identities;
- recording structured quality assessments;
- selecting documents according to visible criteria;
- organizing extracted concepts into knowledge domains;
- producing learning and synthesis plans;
- generating candidate documents from those plans;
- preserving intermediate artifacts for inspection;
- separating successful generation from validated data quality.

The pipeline deliberately combines probabilistic LLM operations with deterministic Python transformations. The LLM is used where semantic interpretation is required, while deterministic code handles artifact naming, run isolation, aggregation, and selected transformations.

## Pipeline at a glance

```text
Normalized FastAPI documentation
                |
                v
Stage 1: Document quality analysis
                |
                v
Stage 2: Knowledge-domain extraction and initial taxonomy
                |
                v
Stage 3: Final taxonomy and synthesis planning
                |
                v
Stage 4: Candidate learning-document generation
```

| Stage | Responsibility | Main outputs |
|---|---|---|
| 1 | Analyze normalized source documents and produce structured quality assessments | `content_analysis.json`, `analysis_report.json` |
| 2 | Select qualifying documents, extract knowledge domains, and assemble an initial taxonomy | `knowledge_domains.json`, `learning_taxonomy.json` |
| 3 | Generate the final taxonomy and synthesis plan, then derive an implementation roadmap | `learning_taxonomy_final.json`, `synthesis_plan.json`, `implementation_roadmap.json` |
| 4 | Generate candidate learning documents from the synthesis plan | `synthetic_dataset/`, `dataset_generation_log.json` |

## Run-directory and artifact contract

One pipeline execution uses one run directory:

```text
data_curation/outputs/<run-id>/
```

Every stage reads its required inputs from that directory and writes its outputs back into the same directory using fixed artifact names.

A completed run has the following general structure:

```text
outputs/<run-id>/
├── content_analysis.json
├── analysis_report.json
├── knowledge_domains.json
├── learning_taxonomy.json
├── learning_taxonomy_final.json
├── synthesis_plan.json
├── implementation_roadmap.json
├── dataset_generation_log.json
└── synthetic_dataset/
    ├── README.md
    └── <generated category directories and Markdown files>
```

The run ID belongs to the directory rather than the individual filenames. This keeps the stage interfaces stable while allowing multiple runs to be compared or reviewed independently.

Each stage has a designated completion sentinel:

| Stage | Completion sentinel |
|---|---|
| 1 | `content_analysis.json` |
| 2 | `learning_taxonomy.json` |
| 3 | `implementation_roadmap.json` |
| 4 | `dataset_generation_log.json` |

These sentinels are used by `--resume`. Their existence means that the corresponding stage produced its expected final artifact. It does not prove that the artifact is complete, semantically correct, or suitable for publication.

## Pipeline stages

### Stage 1: Document quality analysis

Stage 1 reads the normalized FastAPI documentation manifest and resolves each document through its recorded output filename. It submits document content for structured LLM analysis while retaining the source identity needed by later stages.

The analysis records quality scores, recommendations, detected problems, and processing failures. It also produces an aggregate report containing run metadata and summary statistics.

**Inputs**

- normalized documentation manifest;
- normalized source documents;
- OpenAI model configuration.

**Outputs**

- `content_analysis.json` — per-document analysis records;
- `analysis_report.json` — run metadata and aggregate statistics.

**Quality boundary**

The output structure is validated, but the quality scores and recommendations remain model judgments. Score distributions, classifications, and detected issues must be reviewed rather than treated as objective measurements.

### Stage 2: Knowledge-domain extraction and initial taxonomy

Stage 2 reads the Stage 1 analysis, selects documents that meet its qualification criteria, resolves their normalized source content, and uses an LLM to extract concepts, relationships, prerequisites, gaps, and synthesis opportunities.

The individual extractions are merged into a unified knowledge-domain representation. Once that representation exists, Python code deterministically assembles the initial learning taxonomy within the boundaries of the extracted data.

**Inputs**

- `content_analysis.json`;
- normalized source documents referenced by the selected records.

**Outputs**

- `knowledge_domains.json` — merged domains, concepts, relationships, gaps, and synthesis opportunities;
- `learning_taxonomy.json` — the initial taxonomy derived from the merged knowledge map.

**Quality boundary**

The aggregation is inspectable, but the underlying domain assignments and gap claims originate from probabilistic model output. Concept repetition, inconsistent labels, weak provenance, and unsupported corpus-level claims can survive structurally valid aggregation.

### Stage 3: Final taxonomy and synthesis planning

Stage 3 reads the knowledge-domain artifact and asks the LLM to produce a final learning taxonomy and a synthesis plan for candidate learning documents.

It then derives an implementation roadmap from those generated artifacts. The roadmap converts the proposed dataset structure into an ordered plan, but it does not independently establish that the proposed files, priorities, time estimates, or coverage claims are correct.

**Input**

- `knowledge_domains.json`.

**Outputs**

- `learning_taxonomy_final.json` — proposed learning progression and topic organization;
- `synthesis_plan.json` — proposed candidate-document structure and generation requirements;
- `implementation_roadmap.json` — deterministic roadmap derived from the taxonomy and synthesis plan.

**Quality boundary**

The artifacts are schema-valid planning candidates. Their topic coverage, gap claims, ordering, estimated effort, and proposed metrics require human review against the source corpus and the intended RAG use case.

### Stage 4: Candidate learning-document generation

Stage 4 reads the synthesis plan and generates the proposed Markdown files inside `synthetic_dataset/`. It can use relevant source-analysis context while producing each candidate document.

A generation log records the files that were successfully created. If any planned file fails, the stage reports the errors and does not write its completion sentinel.

**Inputs**

- `synthesis_plan.json`;
- `content_analysis.json`;
- normalized source material resolved through the analysis records.

**Outputs**

- `synthetic_dataset/` — generated category directories and Markdown documents;
- `synthetic_dataset/README.md` — generated description of the candidate dataset;
- `dataset_generation_log.json` — successful generation records and errors.

**Quality boundary**

Successful generation means that the requested files were created. It does not mean their explanations, code examples, framework behavior, security guidance, or production recommendations are technically correct.

The README generated inside `synthetic_dataset/` is itself a generated artifact. It should not be treated as authoritative evidence that the dataset meets its stated quality criteria.

## Running the pipeline

### Prerequisites

Install the project dependencies:

```bash
uv sync
```

Copy the environment template:

```bash
cp .env.example .env
```

Add the required OpenAI API key to `.env`:

```text
OPENAI_API_KEY=<your-key>
```

Optional model, retry, and timeout settings are documented in `.env.example`.

> The curation stages make paid OpenAI API calls. Review the selected stages and file limit before starting a run.

### Run the complete pipeline

The following command creates a fresh run ID and runs Stages 1 through 4:

```bash
uv run python data_curation/stage0_run_pipeline.py
```

The generated run directory is written under:

```text
data_curation/outputs/<run-id>/
```

### Run a limited Stage 1 smoke test

Use `--limit` with `--only 1` to analyze a small number of documents without continuing into the downstream stages:

```bash
uv run python data_curation/stage0_run_pipeline.py --limit 5 --only 1
```

The `--limit` option is passed only to Stage 1. It does not restrict the number of stages.

### Continue an existing run

After reviewing the Stage 1 output, continue the same run by supplying its run ID:

```bash
uv run python data_curation/stage0_run_pipeline.py \
  --run-id <run-id> \
  --only 2,3,4
```

Downstream stages expect the required upstream artifacts to already exist in that run directory.

### Resume a run

Use `--resume` to skip stages whose completion sentinels already exist:

```bash
uv run python data_curation/stage0_run_pipeline.py \
  --run-id <run-id> \
  --resume
```

Resume behavior is existence-based. It does not validate the contents of an existing sentinel before skipping the stage.

### Run selected stages

The `--only` option accepts comma-separated stage numbers:

```bash
uv run python data_curation/stage0_run_pipeline.py \
  --run-id <run-id> \
  --only 2,3
```

Selected stages still require their upstream artifacts. Running a later stage alone does not regenerate missing inputs automatically.

## Validation and review model

A complete Stage 1–4 run demonstrated that the stages can share one run directory, exchange the expected fixed-name artifacts, and complete the orchestration chain.

That structural success is only one layer of validation. Reviewing the generated artifacts exposed limitations common to probabilistic data pipelines, including:

- model-dependent quality scoring;
- clustering of scores around a narrow range;
- cross-domain concept repetition;
- insufficient source-level provenance;
- unsupported corpus-level gap claims;
- semantic contract drift between stages;
- planning requirements that are described but not enforced;
- unsupported effort estimates and improvement metrics;
- generated code examples that are structurally plausible but technically unreliable.

Generated scores, taxonomies, gap assessments, roadmaps, and Markdown documents are therefore treated as structured candidates for human review rather than ground truth.

The project keeps the following concerns separate:

```text
generation
    |
    v
preserved evidence
    |
    v
run analysis
    |
    v
triaged findings
    |
    v
approved plans and executable tickets
```

A generated artifact does not become trusted merely because it is valid JSON, satisfies a schema, or was produced without a runtime error. Publication and ingestion require additional semantic, technical, provenance, security, and retrieval-focused validation.

## What this project demonstrates

This data-curation layer demonstrates:

- orchestration of dependent LLM-assisted stages;
- stable run-directory and artifact contracts;
- structured-output validation for selected model responses;
- preservation of source-document identity;
- separation of probabilistic interpretation from deterministic transformations;
- explicit completion sentinels and resumable execution;
- inspection of generated evidence rather than relying only on successful execution;
- conversion of audit findings into separately approved engineering work.

It does not claim that the generated dataset is production-ready or that the current curation strategy improves retrieval quality by a measured amount. Those claims require a reviewed dataset, a fixed evaluation corpus, baseline retrieval results, and repeatable comparative evaluation.

## Related files

- [`stage0_run_pipeline.py`](stage0_run_pipeline.py) — pipeline orchestrator and CLI;
- [`pipeline.py`](pipeline.py) — shared run-directory and artifact-name contract;
- [`stage1_analyze_content_quality.py`](stage1_analyze_content_quality.py) — document-quality analysis;
- [`stage2_knowledge_domains.py`](stage2_knowledge_domains.py) — knowledge-domain extraction and initial taxonomy;
- [`stage3_learning_taxonomy_final.py`](stage3_learning_taxonomy_final.py) — final taxonomy and synthesis planning;
- [`stage4_synthetic_dataset.py`](stage4_synthetic_dataset.py) — candidate-document generation;
- [`../ROADMAP.md`](../ROADMAP.md) — overall project capability status;
- [`../docs/agents/issue-tracker.md`](../docs/agents/issue-tracker.md) — local findings, plans, and ticket workflow.
