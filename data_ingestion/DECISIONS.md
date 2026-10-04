# Ingestion decisions

Why the ingestion side of this project is built the way it is. Each entry records
what was decided, what else was considered, and what it costs. Entries are added as
the pipeline grows; the newest sit at the top.

---

## 2026-10-04 — The local BGE code path is removed

**Decision.** The fastembed branches that ran BGE-large on the laptop are deleted;
BGE is reached only through OpenRouter. Its tokenizer stays in `models/fastembed`
(the folder keeps its name so nothing on disk moves), and the `fastembed` and
`fastembed-haystack` packages stay as dependencies, because Qdrant's BM25 sparse
vectors (`Qdrant/bm25`) run through them once hybrid search is built.

**Why.** Unused code paths still have to be read, tested and kept in step; after
IDX-12 no registry entry used this one.

**Consequences.** Running BGE locally again would mean restoring the branches from
git history (`91f9ddd` is the last version with them) and re-downloading the 1.2 GB
model. The model file itself is deleted by hand.

---

## 2026-10-04 — BGE-large runs on OpenRouter, not on the laptop

**Decision.** BGE-large (`BAAI/bge-large-en-v1.5`, 1024 dimensions, 512-token input)
stays in the study for both of its jobs — the semantic chunker's boundary model and
the embedder behind the `*__bge` collections — but it now runs on OpenRouter
(`baai/bge-large-en-v1.5`, OpenAI-compatible embeddings endpoint) instead of locally
through fastembed. Only its tokenizer file stays on the laptop.

**Why.** Time. The first full-corpus semantic run took 106 minutes on a MacBook Air
with BGE-large on the CPU, with other applications closed to let it finish, and
embedding every strategy's chunks for the `*__bge` collections would take hours more
on the same machine. A study that is re-run whenever the corpus or a setting changes
cannot wait that long. On OpenRouter the same model costs $0.01 per million tokens,
and one pass over the corpus is about 240K tokens, so the whole study's BGE
embedding costs cents.

**Truncation is ours.** OpenRouter's documentation says input over the model's limit
is "truncated or rejected" without saying which. Every text is cut to 512 BGE tokens
(`[CLS]` and `[SEP]` included) with BGE's own tokenizer before it is sent, and the
number cut is logged, so the result does not depend on what OpenRouter does and a
long code block cannot fail a run. The truncation statistics are unchanged: they use
the same tokenizer file, pinned to the snapshot already cached; its sha256 is
checked when it is downloaded and by `scripts/download_models.py --check`.

**Alternatives considered.**

- **Keep running it locally.** Free and offline, but too slow to re-run.
- **A GPU job (Colab, Modal).** Fast, but a learning curve this project does not need.
- **A smaller BGE variant.** Faster locally, but a different vector size and a weaker
  comparison against Voyage.

**Consequences.** The BGE side of the pipeline is no longer offline or free: both
embedders are now paid APIs (OpenRouter, Voyage), so the "runs offline and free" lines
in the two BGE entries below no longer hold. Tests stay offline and never make a paid
call; they stub the client. Public FastAPI documentation is sent to a third party, and
a single hosting provider means an outage stops embedding until it is back. The
1.34 GB local model file is no longer read and can be deleted.

---

## 2026-09-20 — BGE-large is the second embedder, not the main one

**Decision.** The corpus is indexed twice: once with Voyage `voyage-4-lite`
(2048 dimensions, 32k-token input) as the primary embedder, and once with
`BAAI/bge-large-en-v1.5` (1024 dimensions, 512-token input), run locally through
fastembed. Every chunking strategy is indexed with both, so retrieval quality can be
compared per strategy **and** per embedder.

**Why keep a second embedder at all.** A chunking comparison with one embedder
cannot tell "this strategy is better" apart from "this strategy happens to suit this
embedder". Two embedders with very different input limits separate those. BGE also
runs offline and free, so the pipeline stays testable without an API key or spend.

**Why the large variant.** Its 1024 dimensions match the vector size the collections
already use, and it is the strongest of the BGE family that runs comfortably on a
laptop CPU. The cost is size: about 1.2 GB of ONNX weights.

**The 512-token limit is deliberate, and becomes a result.** Chunk sizes are kept at
the sizes each strategy is meant to demonstrate, rather than shrunk to fit BGE. With
those sizes, an estimated 69% of `naive_medium` chunks and 70% of `naive_large`
chunks are longer than BGE can read, so their tails are silently dropped when
embedded — usually by a few tokens for `naive_medium`, but up to half a chunk for
`naive_large`. `naive_small` and `recursive` stay comfortably inside the limit.
Voyage, with more than 60× the input budget, reads those same chunks whole.

That contrast is the point: "big chunks lose their tails on a small-context
embedder" is a finding worth reporting, and designing it away would hide it. The
indexing report records, per variant, how many chunks exceeded the limit and by how
much, so the effect is measured rather than assumed.

**Alternatives considered.**

- **Shrink every chunk to fit 512 tokens.** Makes all variants comparable on BGE,
  but changes what each strategy is, and hides the truncation effect.
- **Use only Voyage.** Cheaper to build and no downloads, but no second opinion, no
  offline path, and no truncation contrast.
- **A smaller BGE variant** (`bge-small`, 384 dimensions). Lighter and faster, but a
  different vector size and a weaker comparison against Voyage.

**Consequences.** ~1.2 GB downloaded once into the git-ignored `models/` folder; a
local CPU embedding pass that is slower than the API; and truncation statistics that
must be read alongside BGE's scores whenever the two embedders are compared.

---

## 2026-09-20 — BGE-large also finds the semantic chunker's boundaries

**Decision.** The semantic chunking strategy detects topic changes using BGE-large
locally, cutting where the similarity between neighbouring sentence groups drops
into the largest 5% of drops for that document.

**Why.** Semantic chunking needs an embedding model to *find the cut points*, which
is a separate job from the embedding that is later stored and searched. Choosing
BGE for it means no extra model to download, no API cost for a step that runs on
every re-chunk, and, most importantly, one set of chunks: the Voyage and BGE
variants of the semantic strategy contain exactly the same text, so the comparison
between them isolates the embedder.

**Alternatives considered.**

- **A small sentence-transformer model.** Faster, but another download and a weaker
  sense of where topics change.
- **Voyage.** Paid calls on every re-chunk, and it would not improve Voyage's own
  results, since boundaries are shared by both variants anyway.

**Consequences.** Re-chunking the corpus semantically embeds it an extra time
locally, which is the slowest offline step in the pipeline. The percentile, group
size, and length bounds are recorded in each chunk's metadata so a run can be
reproduced.

---

## 2026-09-20 — Sentence boundaries come from NLTK's Punkt, cached in the project

**Decision.** Sentence detection uses the Punkt tokenizer that the pipeline's
splitters rely on (NLTK's `punkt_tab` data), and that data is downloaded once, ahead
of time, into the git-ignored `models/nltk` folder.

**Which parts need it.** Two of the seven strategies: the sentence strategy, which
groups six sentences per chunk, and the semantic strategy, which splits a document
into sentences before comparing groups of them. The word-based, character-based, and
markdown-structure strategies do not.

**Why a real tokenizer.** The cheap alternative is to cut at every period followed by
a space. On API documentation that is wrong constantly: `app.get(...)`,
`response.headers`, version numbers like `3.11`, and file names such as `main.py`
all contain periods that end nothing. Punkt is trained to tell those from real
sentence ends, so sentence-based chunks stay whole sentences.

**Why cache it in the project.** Left alone, the splitter downloads the data itself,
on first use, into a folder in the user's home directory. That makes a "no network"
run fail in surprising ways, hides a download inside what looks like local work, and
leaves the pipeline's inputs outside the project. Fetching it up front with
`scripts/download_models.py` makes it explicit: one command fills `models/`,
`--check` reports what is present without downloading, and the pipeline only ever
reads from that folder.

There is a second, sharper reason. Recent NLTK versions refuse to read or write data
outside the folders on their search path, so the project cache must be registered
before use (`data_ingestion/model_cache.py::register_nltk_data_dir`). Without that
step the cached copy is ignored *and* rejected, and the library falls back to
downloading its own.

**Consequences.** One extra ~15 MB download alongside the embedding model, and one
rule for code that splits sentences: register the project's data folder first.
