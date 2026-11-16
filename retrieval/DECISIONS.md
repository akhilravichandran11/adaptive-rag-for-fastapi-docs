# Retrieval decisions

Why the query side of this project is built the way it is. Each entry records what
was decided, what else was considered, and what it costs. Entries are added as the
pipeline grows; the newest sit at the top.

---

## 2026-11-16 — Hybrid retrieval in baseline_rag: rerank scores, and index_run from the dense side

**Decision.** `build_rag_pipeline` in `retrieval/baseline_rag.py` takes `retrieval="dense"`
(the default, unchanged) or `"hybrid"`. Hybrid starts from `build_hybrid_pipeline` in
`retrieval/hybrid.py` and feeds its `ranker` into the same prompt and Gemini step, so a hybrid
answer differs from a dense one in retrieval only. `ask` returns the same record for both, plus
`index_run`. Both command lines take `--retrieval dense|hybrid`, and both refuse a hybrid run
without `VOYAGE_API_KEY` (exit 1) before any paid call, because the ranker reads its key only
when it first runs, after the paid query embedding. In `evaluation/generate_results.py`, hybrid
lines are named `hybrid__<strategy>__<embedder>`, and `run_meta.json`'s settings record
`retrieval` and `hybrid: {chunks_run, candidates, rerank_model}` (`null` under dense; a run
saved before this entry is read as dense).

**Under hybrid, `score` is the Voyage rerank score, not a cosine similarity.** The chunks are
the ranker's, in rerank order, ranked from 1. The scorer reads only `rank` and
`meta.source_path`, so recall and MRR are unaffected, but two results files' `score` values
are not comparable across retrievals.

**Why `index_run` comes from the dense branch.** The index report gate needs the indexing run
that built the collection. Chunks found only by BM25 come from the saved chunk file, whose
metadata has no `index_run` or `chunks_run`. Haystack's reciprocal-rank joiner keeps one copy of
a chunk both sides return, and it may be the BM25 copy, so the final chunks can all lack it in a
valid run. `ask` therefore reads `index_run` from the dense retriever's own output (`null` when it
returned nothing), and the gate stops on `null`. Under hybrid the gate also stops when the index
report's `chunks_run` differs from the chunk run BM25 searches, since fused lists from two
chunkings would mix.

**Where the gate takes `chunks_run`.** Under dense it still takes it from the first chunk's
`meta`, as before: every dense chunk is a Qdrant point that carries it. Under hybrid it takes it
from the index report entry, because which copy of a chunk the joiner keeps can change between
attempts, and a chunk's missing `chunks_run` would then read as a rebuilt collection on resume.

**Cost.** One more field in every record. A hybrid pipeline loads its chunk file into memory
when built. A resume refuses a run whose hybrid settings no longer match `hybrid.py`.

---

## 2026-10-26 — Hybrid retrieval: in-memory BM25 plus dense search, fused and reranked

**Decision.** `retrieval/hybrid.py` searches one indexed variant two ways. BM25 runs in
memory over the saved chunk file of stage 2's run `DEFAULT_CHUNKS_RUN`
(`full_chunks_20261015`). Dense search runs on the variant's Qdrant collection. Each
returns `CANDIDATES` (50) chunks. `DocumentJoiner` fuses the two lists by reciprocal
rank, and `VoyageRanker` (`RERANK_MODEL`, `"rerank-2.5"`) reranks them to `TOP_K` (10).
It is one small Haystack `Pipeline` of built-in components. CRAG uses it on the
`markdown_ast` Voyage collection through `build_hybrid_pipeline` and `retrieve`.

Each BM25 chunk gets the id stage 3 gave its Qdrant point,
`strategy:embedder:source_path:chunk_index` (D34, an id of the ingestion plan; see
`point_key` in `data_ingestion/stage3_index_chunks.py`). The key is built in
`hybrid.py`, not imported from the stage (C007 in
[`docs/conventions.md`](../docs/conventions.md)). A failed rerank raises; it is never
skipped. A missing chunk file or a missing collection raises `ValueError` before any
search.

**Why.** BM25 matches exact words, such as API names, that dense search can rank low.
Reciprocal rank fusion merges duplicates by document id. The saved chunk rows hold only
text and metadata, so without the shared key Haystack would give a BM25 chunk a content
hash that never matches its Qdrant copy. The collection is checked first because
Haystack's Qdrant store creates a missing collection when it first connects, so a
mistyped name would search a new, empty one. A skipped rerank would hand back the
un-reranked lists with nothing in the result to show it.

On the 2026-10-26 CRAG route check, 8 questions with known routes, dense retrieval
gave 6 of 8 routes as expected and hybrid gave 7 of 8. Hybrid found the HTTP Basic
auth page for a question that dense missed, and it changed no other route. On one
correct question it found 6 relevant chunks where dense found 3.

**The BM25 trade-offs.**

- The BM25 index is rebuilt from the chunk file at every start: seconds for the 1,137
  `markdown_ast` chunks.
- The chunk file sits in git-ignored `data_ingestion/outputs/`, so the backend needs it
  on disk wherever it runs.
- Move sparse vectors into Qdrant only if the corpus grows a lot.

**Alternatives considered.**

- **Sparse vectors in Qdrant, or a local sparse model.** Local models were slow on the
  laptop, and either needs a new hybrid collection and a paid re-index. In-memory BM25
  needs no new code path in `data_ingestion/`.
- **A hand-written reranker.** `VoyageRanker` ships with the installed Voyage package.

---

## 2026-10-17 — Every variant retrieves 10 chunks and answers from them only

**Decision.** `retrieval/baseline_rag.py` answers a question from one of the 14
indexed collections: it embeds the question with the collection's embedder, retrieves
10 chunks, and asks Gemini to answer from those chunks alone. The 10 is fixed in code
(`TOP_K`), not read from `.env`; a debugging run can still pass `--top-k`, and every
result records the `top_k` its retriever used. The answer model is whatever
`GEMINI_MODEL` names, at temperature 0. The prompt numbers the chunks, wraps each in
an `<excerpt>` tag with its source path, tells the model the excerpts are data and not
instructions, asks it to cite excerpts by number, and asks it to say the documentation
does not cover the question rather than answer from memory.

`GEMINI_MODEL` is set to `gemini-pro-latest`. That name is an alias, and Google moves
it to newer Pro models as they ship, so answers from different dates may come from
different models. Temperature 0 keeps answers as steady as one model allows; it does
not make them repeatable across dates.

**Example.** One question, "How do I add a custom response header?", asked of the two
`markdown_ast` collections on 2026-10-19. The file that answers it,
`advanced/response-headers.md`, came back at ranks 1, 2 and 4 of 10 from Voyage and
at ranks 3, 9 and 10 from BGE. Both answers cited excerpts by number, and every cited
chunk held the code the answer quoted. This is one question that shows the setup
working, not a measurement of either embedder.

**Why 10 chunks, fixed in code.** The 14 variants can only be compared if each
retrieves the same number of chunks, so a stray `.env` edit must not change it
silently. This is the same reason the embedding models are fixed in code.

**Why the answer model lives in `.env`.** All 14 variants are answered by the same
model within one run, so a change of model cannot favour one variant over another.
Temperature 0 makes a difference between variants come from their chunks, not from
sampling.

**Why a grounded prompt with numbered excerpts.** A model that answers only from the
excerpts, and says so when they fall short, makes a bad retrieval visible in its
answer; a model free to answer from memory would hide it. Treating the excerpts as
data means an instruction written inside a document is not followed. Numbers are
shorter to cite than paths, and each excerpt still shows its path.

**Alternatives considered.**

- **Read `top_k` from `RETRIEVAL_TOP_K`.** Left for the later adaptive pipeline; the
  comparison does not read it.
- **Pin a fixed model id.** Google renames and replaces Pro models often, so a pinned
  id would need chasing. Pinning is kept for a run whose results must be reproduced
  later.

**Known limitations**, accepted rather than coded around:

- **A chunk containing `</excerpt>` could close its tag early.** The text is not
  escaped. No chunk in the corpus contains it today.
- **No retries and no fallback model.** A failed Gemini call raises, and so does a
  reply that is not a complete answer; the caller records the error.
- **The answer model drifts.** Because `gemini-pro-latest` moves, answers and scores
  from different dates are not directly comparable unless the model behind the alias
  is known.
- **BGE's query instruction is assumed to reach the model unchanged.** See the
  2026-10-17 entry in [`data_ingestion/DECISIONS.md`](../data_ingestion/DECISIONS.md).
