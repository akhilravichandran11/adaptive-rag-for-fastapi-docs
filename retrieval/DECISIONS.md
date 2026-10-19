# Retrieval decisions

Why the query side of this project is built the way it is. Each entry records what
was decided, what else was considered, and what it costs. Entries are added as the
pipeline grows; the newest sit at the top.

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
