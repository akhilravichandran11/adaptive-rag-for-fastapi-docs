# Running retrieval

Run every command from the project root. `--help` prints the full options:

```bash
uv run python retrieval/baseline_rag.py --help
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
