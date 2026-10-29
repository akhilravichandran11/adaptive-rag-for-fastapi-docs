"""File-level retrieval metrics (recall@k and reciprocal rank) for saved results, with no API calls."""

from __future__ import annotations

from typing import Any

KS = (1, 3, 5, 10)


def score_chunks(chunks: list[dict[str, Any]], gold_sources: list[str]) -> dict[str, float]:
    if not gold_sources:
        raise ValueError("score_chunks: the question has no gold_sources")
    gold = set(gold_sources)
    best_rank: dict[str, int] = {}
    for chunk in chunks:
        meta = chunk["meta"]
        if meta.get("source") == "web":
            continue
        if "source_path" not in meta:
            raise ValueError(
                f"score_chunks: the chunk at rank {chunk['rank']} has no meta.source_path "
                "and is not a web chunk (meta.source == 'web')"
            )
        path = meta["source_path"]
        if path in gold:
            best_rank[path] = min(chunk["rank"], best_rank.get(path, chunk["rank"]))

    scores = {f"recall@{k}": sum(rank <= k for rank in best_rank.values()) / len(gold) for k in KS}
    scores["reciprocal_rank"] = 1 / min(best_rank.values()) if best_rank else 0.0
    return scores
