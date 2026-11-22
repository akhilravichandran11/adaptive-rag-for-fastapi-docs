# LLM-assisted data curation

Stage 1's saved analysis supplies the quality metadata used by
ingestion. It remains at `outputs/<run-id>/content_analysis.json`; ingestion
requires an explicit run ID, currently `20260925_032632`. Every existing output
stays in place, including the retired guide artifacts in that run.

Stages 2 to 4, which generated candidate learning guides, are retired under
`legacy/`. Their source and regression tests remain available. Stage 0 stays
here to preserve the Stage 1 command and historical workflows; Stage 5's
evaluation-question generator and the shared modules also stay here.

The revised curation approach is being planned. Its validated offline source
inventory already exists in `pipeline_v2/`; further analysis and ingestion
wiring are not implemented. See [ROADMAP.md](../ROADMAP.md).

## Stages

| Stage | What it does | Main outputs |
|---|---|---|
| 1 | Scores each normalized document with structured LLM analysis | `content_analysis.json`, `analysis_report.json` |
| 2 (retired: `legacy/stage2_knowledge_domains.py`) | Selects qualifying documents, extracts knowledge domains, and builds an initial taxonomy | `knowledge_domains.json`, `learning_taxonomy.json` |
| 3 (retired: `legacy/stage3_learning_taxonomy_final.py`) | Produces the final taxonomy and a synthesis plan, then derives a roadmap from them | `learning_taxonomy_final.json`, `synthesis_plan.json`, `implementation_roadmap.json` |
| 4 (retired: `legacy/stage4_synthetic_dataset.py`) | Generates candidate learning documents from the synthesis plan | `synthetic_dataset/`, `dataset_generation_log.json` |

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
```

These commands run the retired guide workflow and are kept for reference.
Generated guides are unverified candidates for human review.

```bash
# Run stages 1 to 4 in a fresh run directory
uv run python data_curation/stage0_run_pipeline.py

# Continue an existing run with later stages
uv run python data_curation/stage0_run_pipeline.py --run-id <run-id> --only 2,3,4

# Skip stages that already finished
uv run python data_curation/stage0_run_pipeline.py --run-id <run-id> --resume
```

`--limit` applies to stage 1 only. Later stages expect the earlier stages'
outputs to exist in the same run directory.
