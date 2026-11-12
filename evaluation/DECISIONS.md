# Evaluation decisions

Why the evaluation side of this project is built the way it is. Each entry records
what was decided, what else was considered, and what it costs. Entries are added as
the harness grows; the newest sit at the top.

---

## 2026-11-09 — Questions name their answer pages by `source_path`; ten are written by hand and forty generated

**Decision.** An evaluation question is an `EvalQuestion` (`data_curation/schemas.py`)
with five required fields, `question_id`, `question`, `reference_answer`,
`gold_sources` and `origin` (`handwritten` or `synthetic`), and one optional field,
`paraphrase`. `gold_sources` lists the pages that answer the question by their
manifest `source_path`, such as `advanced/additional-responses.md`, and holds at least
one; a question lists two pages only where it takes two to answer it. The first set,
`evaluation/questions/handwritten.json`, holds ten questions written by hand, `hw-01`
to `hw-10`, whose gold pages between them reach all six buckets of the docs: tutorial,
advanced, reference, how-to, deployment and the top-level pages. A test checks that
every entry loads as an `EvalQuestion` with no unknown keys, that every gold page is in
the manifest, and that every bucket is covered.

**Why `source_path`.** Every indexed chunk carries its page's `source_path` in
`meta.source_path`, so retrieval is scored by comparing strings: a retrieved chunk is a
hit when its `source_path` is in the question's `gold_sources`. A page's normalized
filename (`advanced__additional-responses.md`) is its other name, but no chunk field
holds it, so scoring on it would need a lookup table.

**Why no `bucket` or `difficulty`.** Nothing reads either one: results are reported by
system and by question set. A stored bucket would be the first gold page's, an
arbitrary pick for a two-page question, so a check that needs one looks it up in the
manifest. A difficulty label had no honest source: the corpus analysis rated 112 of
the 124 pages `intermediate`.

**Why an optional `paraphrase`.** Where a question's author also wrote a second
wording of it, `paraphrase` holds that wording; every other entry leaves it `null`, and
generated questions never set it. Nothing reads it yet, but unlike a bucket it cannot
be looked up later: only the person who wrote the question can supply it. It is kept so
that a later run can ask both wordings and compare how retrieval does on each.

**Why a handwritten set.** Questions generated from a page tend to reuse its headings
and terms, which makes retrieval look easier than it is for a real user. Ten questions
phrased the way a developer asks them, with answers drafted from the docs and then
checked by hand, give a harder baseline beside the generated set, and they are the set
rated by hand to calibrate the answer judges.

**Why a generated set, and its limit.** The second set,
`evaluation/questions/synthetic.json`, holds forty questions, `syn-01` to
`syn-40`, written by `data_curation/stage5_synthetic_qa.py`. With a fixed seed it
draws forty pages in proportion to the bucket sizes (16, 11, 4, 4, 3 and 2 in the
order above), skips any page the corpus analysis flagged for possible prompt
injection, and takes for each page a question the corpus analysis already wrote
for it. One model call per page answers that question from that page alone. The
forty were read by hand before the copy, and a bad entry is corrected by hand,
never regenerated: syn-13's answer was rewritten without its opening hedge, and
syn-16, whose page does not say how to set environment variables, takes the
page's second question from the corpus analysis, with an answer written from the
page. The limit: the corpus analysis wrote these questions while
reading the page, so they tend to reuse its wording and may make retrieval look
easier than it is for a real user. That is why the two sets are reported apart,
never pooled. The echo also favours keyword search: about nine in ten of a
synthetic question's content words appear in its gold page, against about three
in four for the handwritten ten, so a gap between hybrid and dense retrieval on
this set is checked against the handwritten set. A second limit: the four
reference questions, syn-21 to syn-24, point at short pages (560 to 1,779
characters, against a median of 5,414), and other pages often answer those
questions more fully, so a miss on one of the four can mean the retriever found
a better page; those misses are read by hand. Listing that page as a second gold
source would not help: recall divides by the number of gold pages, so either
page alone would score half.

**Cost.** Ten questions are a small sample: one question moves a recall figure by ten
points, so the handwritten set shows direction, not small differences.
