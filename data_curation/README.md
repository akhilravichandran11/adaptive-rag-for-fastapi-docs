# LLM-assisted data curation

This directory holds an LLM-assisted curation pipeline for the FastAPI
documentation. It analyzes the normalized documents, selects the ones that meet
its criteria, extracts knowledge domains, plans a learning taxonomy, and
generates candidate learning documents. Every output is a candidate for human
review, not a verified result.

> Status: deferred. The first metadata and quality pass was a learning exercise;
> a revised approach will follow once retrieval and evaluation are in place. Its
> first piece, a validated offline source inventory, is in `pipeline_v2/`. See
> [ROADMAP.md](../ROADMAP.md).

## Stages

| Stage | What it does | Main outputs |
|---|---|---|
| 1 | Scores each normalized document with structured LLM analysis | `content_analysis.json`, `analysis_report.json` |
| 2 | Selects qualifying documents, extracts knowledge domains, and builds an initial taxonomy | `knowledge_domains.json`, `learning_taxonomy.json` |
| 3 | Produces the final taxonomy and a synthesis plan, then derives a roadmap from them | `learning_taxonomy_final.json`, `synthesis_plan.json`, `implementation_roadmap.json` |
| 4 | Generates candidate learning documents from the synthesis plan | `synthetic_dataset/`, `dataset_generation_log.json` |

LLM calls do the semantic work (scoring, extraction, planning, generation).
Plain Python handles run isolation, artifact names, aggregation, and the
taxonomy and roadmap steps that can be computed from earlier outputs.

## Run directory

Each run writes every stage's output into one directory,
`data_curation/outputs/<run-id>/`, using fixed file names. The run ID lives in
the directory name, so runs can be compared side by side.

Each stage has a completion file: `content_analysis.json` for stage 1,
`learning_taxonomy.json` for stage 2, `implementation_roadmap.json` for stage 3,
and `dataset_generation_log.json` for stage 4. `--resume` skips a stage whose
completion file exists; it does not check the file's contents.

## Running it

The stages call the OpenAI API, which costs money. Set `OPENAI_API_KEY` in
`.env` (see `.env.example` for optional model, retry, and timeout settings).

```bash
# Analyze five documents with stage 1 only (a cheap smoke test)
uv run python data_curation/stage0_run_pipeline.py --limit 5 --only 1

# Run stages 1 to 4 in a fresh run directory
uv run python data_curation/stage0_run_pipeline.py

# Continue an existing run with later stages
uv run python data_curation/stage0_run_pipeline.py --run-id <run-id> --only 2,3,4

# Skip stages that already finished
uv run python data_curation/stage0_run_pipeline.py --run-id <run-id> --resume
```

`--limit` applies to stage 1 only. Later stages expect the earlier stages'
outputs to exist in the same run directory.
