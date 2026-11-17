# Dense vs hybrid retrieval, markdown_ast × Voyage

One variable changes: how the top 10 chunks are retrieved. Both runs ask the same 50
questions (10 handwritten, 40 synthetic) of the same collection,
`fastapi_docs__markdown_ast__voyage` (index run `20261107_145200`, chunk run
`full_chunks_20261015`), with `voyage-4-lite` query embeddings.

- **Dense**, run [`20261117_103532`](20261117_103532/): Qdrant vector search alone, retrieve-only
  (no answers). Its systems are named `baseline__…`.
- **Hybrid**, run [`20261116_055637`](20261116_055637/): BM25 and vector search, 50 candidates
  each, fused by reciprocal rank fusion and reranked by `rerank-2.5`.

| system | set | n | recall@1 | recall@3 | recall@5 | recall@10 | MRR | no chunks | retrieval cost per question (upper bound) |
|---|---|---|---|---|---|---|---|---|---|
| baseline__markdown_ast__voyage | handwritten | 10 | 0.70 | 0.80 | 0.85 | 0.85 | 0.85 | 0 | ≤ $0.0000014 |
| baseline__markdown_ast__voyage | synthetic | 40 | 0.80 | 0.97 | 0.97 | 1.00 | 0.88 | 0 | ≤ $0.0000010 |
| hybrid__markdown_ast__voyage | handwritten | 10 | 0.50 | 1.00 | 1.00 | 1.00 | 0.80 | 0 | ≤ $0.0104 |
| hybrid__markdown_ast__voyage | synthetic | 40 | 0.85 | 0.97 | 0.97 | 1.00 | 0.90 | 0 | ≤ $0.0104 |

Latency isn't measured: the results lines record no timing, so the table has no latency column.

## How the cost column is worked out

Each line's `usage` holds only Gemini's answer tokens (it is null in the retrieve-only dense
run), and neither run saves Voyage's token counts. So the column is an upper bound, worked out
from the saved files at the list prices on Voyage's pricing page on 2026-11-17: `voyage-4-lite`
at $0.02 and `rerank-2.5` at $0.05 per million tokens. It counts one token per character,
which overcounts.

- **Query embedding (both systems):** the set's mean question length, from `question` in
  `results.jsonl` (69.3 characters handwritten, 51.9 synthetic), × $0.02 per million.
- **Rerank (hybrid only):** Voyage bills (query tokens × documents) + the tokens of every
  document. The fused list holds at most 100 chunks (`candidates: 50` from each retriever, in
  `run_meta.json`), and the 100 longest chunks in chunk run `full_chunks_20261015` (a local,
  git-ignored folder) total 201,997 characters. So a rerank costs at most
  (100 × question characters + 201,997) × $0.05 per million.
- **Handwritten hybrid, worked through:** (100 × 69.3 + 201,997) × $0.05 / 10⁶ + 69.3 × $0.02 / 10⁶
  = $0.01045 + $0.0000014 ≈ $0.0104.

## Commands

```bash
# Dense smoke run (2 questions, run 20261117_093603, not kept)
uv run python evaluation/generate_results.py --strategy markdown_ast --embedder voyage --retrieval dense --retrieve-only --limit 2
# Dense full run, then a resume that logged "0 pair(s) to ask"
uv run python evaluation/generate_results.py --strategy markdown_ast --embedder voyage --retrieval dense --retrieve-only
uv run python evaluation/generate_results.py --resume 20261117_103532
# Save it
cp evaluation/outputs/20261117_103532/results.jsonl evaluation/outputs/20261117_103532/run_meta.json evaluation/results/20261117_103532/
uv run python evaluation/retrieval_metrics.py evaluation/results/20261117_103532/results.jsonl > evaluation/results/20261117_103532/table.md
# The table above, before the cost column
uv run python evaluation/retrieval_metrics.py evaluation/results/20261116_055637/results.jsonl evaluation/results/20261117_103532/results.jsonl
# Characters in the 100 longest markdown_ast chunks
uv run python -c "from data_ingestion.chunk_snapshot import read_chunks; from data_ingestion.pipeline import resolve_run_dir; print(sum(sorted((len(c.content or '') for c in read_chunks(resolve_run_dir('full_chunks_20261015'), 'markdown_ast')), reverse=True)[:100]))"
```

The hybrid run's command, from the settings in its `run_meta.json`, was
`uv run python evaluation/generate_results.py --strategy markdown_ast --embedder voyage --retrieval hybrid`.

## What changed

Dropping BM25 and the reranker left the 40 synthetic questions almost where they were and moved
the 10 handwritten ones both ways. On the synthetic set, recall@3, @5 and @10 are unchanged
(0.97, 0.97, 1.00), and dense is slightly behind at the top: recall@1 is 0.80 against 0.85 and
MRR 0.88 against 0.90. On the handwritten set, dense scores better on the first hit (recall@1
0.70 against 0.50, MRR 0.85 against 0.80), because it puts the gold file first on hw-01 and hw-05,
where hybrid ranks it second. But it finds fewer of the gold files at all: recall@3 falls from
1.00 to 0.80 and recall@10 from 1.00 to 0.85. Dense never retrieves the gold file for hw-02
("How do I configure FastAPI?") and finds only one of hw-03's two. With 10 questions, one
question moves a handwritten score by 0.10, so these are differences of one or two questions.
On cost, the rerank is nearly all of hybrid's retrieval bill, at most about a cent per question.
Dense makes no rerank call, and its one embedding costs at most $0.0000014.
