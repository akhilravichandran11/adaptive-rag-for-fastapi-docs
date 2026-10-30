# Running crag

Run every command from the project root. The CRAG pipeline has two front doors over the
same code: the command line `crag/run.py`, and a FastAPI server that answers over HTTP and
serves a web page at `/`. Both search the `markdown_ast` Voyage collection. The result
names the route the question took (`correct`, `ambiguous`, `incorrect` or
`no_documents`); [`DECISIONS.md`](DECISIONS.md) explains each.

Every question is **paid** and takes about 30 to 40 seconds. Voyage embeds it and reranks
the chunks, and Gemini grades the chunks and writes the answer. On the `ambiguous` and
`incorrect` routes, Tavily also searches the web and Gemini extracts from the pages.

## Before the first run

```bash
uv sync --extra dev
cp .env.example .env          # then fill in the settings below
```

| Setting in `.env` | Used for |
| --- | --- |
| `QDRANT_URL`, `QDRANT_API_KEY` | dense search in Qdrant |
| `VOYAGE_API_KEY` | embedding the question and reranking the chunks |
| `GOOGLE_API_KEY` | every Gemini call |
| `GEMINI_MODEL` | grading, writing follow-up queries and answering (`gemini-pro-latest` in `.env.example`) |
| `GEMINI_LIGHT_MODEL` | re-grading chunks and extracting from web pages (`gemini-flash-latest`) |
| `TAVILY_API_KEY` | web search |
| `GEMINI_FALLBACK_MODEL`, `GEMINI_LIGHT_FALLBACK_MODEL` | optional: the model tried when the primary keeps failing; unset means `gemini-flash-lite-latest` |

A missing setting stops the run before any paid call, and the error names it: the
command line exits 1, and the server does not start.

## What must already exist

Both front doors read two things that data ingestion builds (see
[`data_ingestion/RUNNING.md`](../data_ingestion/RUNNING.md)):

- the Qdrant collection `fastapi_docs__markdown_ast__voyage`;
- the chunk file `data_ingestion/outputs/full_chunks_20261015/chunks/markdown_ast.jsonl`,
  which BM25 searches in memory. The run id is fixed in `retrieval/hybrid.py`
  (`DEFAULT_CHUNKS_RUN`), and the collection must be indexed from that same run.

To build both (stage 3 is **paid**):

```bash
uv run python data_ingestion/stage2_chunk_corpus.py --strategy markdown_ast --curation-run 20260925_032632 --run-id full_chunks_20261015
uv run python data_ingestion/stage3_index_chunks.py --chunks-run full_chunks_20261015 --strategy markdown_ast --embedder voyage
```

If either is missing, the command line exits 1 naming it, and the server does not start.
`--retriever dense` needs only the collection. Where both already exist, skip the build:
stage 2 refuses a run id whose folder exists, and stage 3 stops at an existing collection
unless given `--recreate`.

## Check retrieval alone (cheaper)

Hybrid retrieval is the first step of every question. On its own it makes only Voyage
calls, with no Gemini or Tavily, so it is a cheaper check that the collection and the
chunk file are in place:

```bash
uv run python retrieval/hybrid.py --question "How do I add CORS middleware?"             # the 10 reranked chunks
uv run python retrieval/hybrid.py --question "How do I add CORS middleware?" --compare   # also the BM25 and dense lists
```

## Command line

```bash
uv run python crag/run.py --help
```

Pass either `--question` or `--route-check`.

```bash
# answer one question on hybrid retrieval: BM25 + dense, fused and reranked (the default)
uv run python crag/run.py --question "How do I add CORS middleware?"

# the same question on dense retrieval only, to compare
uv run python crag/run.py --question "How do I add CORS middleware?" --retriever dense

# the 8 fixed questions, each route compared with the expected one: 8 paid runs, about 5 minutes
uv run python crag/run.py --route-check
```

The result is logged as JSON on stderr: the question, the route, the answer, its
numbered sources, the missing aspects, and one record per step that ran, with the model
version for each LLM step. `--route-check` logs each result, then one `match` or
`MISMATCH` line per question and a count such as `route check: 7 of 8 routes as
expected`. A mismatch is reported, not raised, because the routes are LLM judgments. A
blank `--question` exits 2 before anything runs.

## Server and web page

```bash
uv run uvicorn backend.app.main:app --port 8000
```

Startup loads `.env`, checks the settings, reads the chunk file and checks that the
collection exists; it makes no paid call. The server always uses hybrid retrieval. Open
<http://127.0.0.1:8000/>, type a question and press Ask: the page shows the route, the
answer, the numbered sources and any missing aspects.

| Path | What | Cost |
| --- | --- | --- |
| `GET /` | the web page | free; each question asked on it is paid |
| `POST /query` | takes `{"question": "..."}`, returns the command line's result as JSON | **paid** |
| `GET /health` | `{"status": "ok"}` | free |
| `GET /docs` | FastAPI's interactive API docs | free; trying `/query` there is paid |

```bash
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/query -H "Content-Type: application/json" -d '{"question": "How do I add CORS middleware?"}'
```

`/query` strips the question, which must then be 1 to 1,000 characters; anything else
returns 422 before any paid call. A failed step returns 500, with the traceback in the
server log. Keep the server on localhost: the page's mount serves every file in
`frontend/`, git-ignored ones included.
