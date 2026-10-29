# Running data_ingestion

Run every command from the project root. Each script prints its full options with
`--help`.

Before the first run:

```bash
uv sync --extra dev
cp .env.example .env                              # then fill in the keys
uv run python scripts/download_models.py          # BGE tokenizer + NLTK punkt into models/
```

The stages run in order: stage 2 writes chunks to disk, stage 3 reads them and writes
Qdrant collections. Stage 1 is only a connection check and setup for the store.

| Stage | Script | Cost | Needs in `.env` |
| --- | --- | --- | --- |
| 1 | `stage1_setup_qdrant.py` | free | `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION`, `EMBEDDING_DIMENSION` |
| 2 | `stage2_chunk_corpus.py` | free, except `semantic` / `all` | `OPENROUTER_API_KEY` only for `semantic` |
| 3 | `stage3_index_chunks.py` | **paid** | Qdrant keys, plus `OPENROUTER_API_KEY` (bge) or `VOYAGE_API_KEY` (voyage) |

## Stage 1: check the Qdrant connection

```bash
uv run python data_ingestion/stage1_setup_qdrant.py                                    # attach to QDRANT_COLLECTION, keep its data
uv run python data_ingestion/stage1_setup_qdrant.py --collection tmp_test              # attach to another collection
uv run python data_ingestion/stage1_setup_qdrant.py --collection tmp_test --recreate   # wipe and rebuild it
```

`--recreate` deletes the collection's data. Logs go to stderr.

## Stage 2: chunk the corpus

`--curation-run` names the data_curation run whose metadata the chunks carry
(the current one is `20260925_032632`).

```bash
# one strategy on the first 5 documents: a quick smoke run
uv run python data_ingestion/stage2_chunk_corpus.py --strategy naive_small --curation-run 20260925_032632 --limit 5

# one strategy on the whole corpus, with a run id you choose
uv run python data_ingestion/stage2_chunk_corpus.py --strategy markdown_ast --curation-run 20260925_032632 --run-id my_chunks_run

# every strategy, including semantic (PAID, about $0.008)
uv run python data_ingestion/stage2_chunk_corpus.py --strategy all --curation-run 20260925_032632
```

Strategies: `naive_small`, `naive_medium`, `naive_large`, `sentence`, `recursive`,
`semantic`, `markdown_ast`, or `all`.

Output: `data_ingestion/outputs/<run_id>/chunks/<strategy>.jsonl` and `run_meta.json`.
The run id defaults to the UTC time; a run id whose folder already exists is refused.

## Stage 3: embed and index the chunks (PAID)

`--chunks-run` is the stage 2 run id (a folder name under `data_ingestion/outputs/`,
for example `full_chunks_20261015`).

```bash
# one strategy into throwaway collections: check it works before a full run
uv run python data_ingestion/stage3_index_chunks.py --chunks-run full_chunks_20261015 --strategy naive_small --embedder bge --collection-prefix ix_verify_20261018

# every strategy in the chunk run, one embedder at a time
uv run python data_ingestion/stage3_index_chunks.py --chunks-run full_chunks_20261015 --strategy all --embedder bge
uv run python data_ingestion/stage3_index_chunks.py --chunks-run full_chunks_20261015 --strategy all --embedder voyage

# rebuild collections that already exist
uv run python data_ingestion/stage3_index_chunks.py --chunks-run full_chunks_20261015 --strategy all --embedder voyage --recreate
```

Collections are named `<prefix>__<strategy>__<embedder>`; the default prefix is
`fastapi_docs`. Without `--recreate` the run stops at the first collection that
already exists. Output: `data_ingestion/outputs/<run_id>/index_report.json` and
`run_meta.json`.

## Chunker demos

Each chunker can run on its own on a built-in sample document and log what it
produced. These are free, except `semantic`.

```bash
uv run python -m data_ingestion.chunkers.naive
uv run python -m data_ingestion.chunkers.sentence
uv run python -m data_ingestion.chunkers.recursive
uv run python -m data_ingestion.chunkers.markdown_ast
uv run python -m data_ingestion.chunkers.semantic      # PAID call to OpenRouter
```

## Related scripts outside this folder

```bash
uv run python scripts/chunk_size_report.py              # chunk shape and size report, free strategies only
uv run python scripts/download_models.py --check        # report whether the local models are present
```

`scripts/chunk_size_report.py --include-semantic` makes paid calls.

Next step after indexing: ask a collection a question with
[`retrieval/RUNNING.md`](../retrieval/RUNNING.md).
