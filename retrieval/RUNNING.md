# Running retrieval

Run every command from the project root. `--help` prints the full options:

```bash
uv run python retrieval/baseline_rag.py --help
uv run python retrieval/hybrid.py --help
```

`baseline_rag.py` asks one indexed collection a question. The collection must already
exist: build it with stage 3 in [`data_ingestion/RUNNING.md`](../data_ingestion/RUNNING.md).
The script reads collection `<prefix>__<strategy>__<embedder>`, with the default prefix
`fastapi_docs`.

Both steps are **paid**: embedding the question (OpenRouter for `bge`, Voyage for
`voyage`) and answering it (Gemini).

| Flag | Values | Needs in `.env` |
| --- | --- | --- |
| `--strategy` | `naive_small`, `naive_medium`, `naive_large`, `sentence`, `recursive`, `semantic`, `markdown_ast` | Qdrant keys |
| `--embedder` | `bge` or `voyage` | `OPENROUTER_API_KEY` or `VOYAGE_API_KEY` |
| answering (skipped by `--retrieve-only`) | | `GEMINI_MODEL` and `GOOGLE_API_KEY` |

## Retrieve chunks only (no Gemini)

```bash
uv run python retrieval/baseline_rag.py --strategy markdown_ast --embedder voyage --question "How do I declare a path parameter?" --retrieve-only
```

## Retrieve and answer

```bash
uv run python retrieval/baseline_rag.py --strategy naive_medium --embedder bge --question "How do I add CORS middleware?"
```

## Other options

```bash
# fetch 5 chunks instead of the default 10 (every variant is compared at 10)
uv run python retrieval/baseline_rag.py --strategy recursive --embedder voyage --question "What is a dependency?" --top-k 5

# query throwaway collections that stage 3 built with another prefix
uv run python retrieval/baseline_rag.py --strategy naive_small --embedder bge --question "What is a dependency?" --collection-prefix ix_verify_20261018 --retrieve-only
```

## Hybrid retrieval (`hybrid.py`)

`hybrid.py` searches one collection two ways and returns chunks only; nothing calls Gemini.
BM25 searches the strategy's chunk file from a stage 2 run, in memory, and dense search runs
on the Qdrant collection. The two lists of 50 are fused by reciprocal rank and reranked by
Voyage. CRAG runs this step first for every question.

Both steps are **paid**: embedding the question (Voyage, or OpenRouter for `bge`) and
reranking the chunks (always Voyage). Besides the Qdrant keys, `.env` needs
`VOYAGE_API_KEY` for every run and `OPENROUTER_API_KEY` for `--embedder bge`, which also
needs the BGE tokenizer from `scripts/download_models.py`.

| Flag | Default | What |
| --- | --- | --- |
| `--question` | required | the question to search for |
| `--strategy` | `markdown_ast` | one of the seven strategies in the table above |
| `--embedder` | `voyage` | `bge` or `voyage` |
| `--chunks-run` | `full_chunks_20261015` | the stage 2 run whose chunk file BM25 searches; it must be the run the collection was indexed from |
| `--collection-prefix` | `fastapi_docs` | reads collection `<prefix>__<strategy>__<embedder>` |
| `--top-k` | `10` | chunks kept after reranking |
| `--compare` | off | also log the BM25 and dense lists and the chunk ids found in both |

```bash
# the 10 reranked chunks from the markdown_ast Voyage collection
uv run python retrieval/hybrid.py --question "How do I add CORS middleware?"

# a BGE collection, logging the BM25 and dense lists before the reranked one
uv run python retrieval/hybrid.py --strategy naive_medium --embedder bge --question "What is a dependency?" --compare
```

Each chunk is logged with its rank, score, id and source path. A missing key, chunk file or
collection exits 1 before any paid call.

Next step: answer questions through the CRAG routes, from the command line or a web page,
with [`crag/RUNNING.md`](../crag/RUNNING.md).
