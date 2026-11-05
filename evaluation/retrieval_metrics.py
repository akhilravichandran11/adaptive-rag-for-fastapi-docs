"""File-level retrieval metrics (recall@k and reciprocal rank) for saved results, with no API calls."""

from typing import Any

KS = (1, 3, 5, 10)


def score_chunks(chunks: list[dict[str, Any]], gold_sources: list[str]) -> dict[str, float]:
    if not gold_sources:
        raise ValueError("score_chunks: the question has no gold_sources")
    gold = set(gold_sources)
    best_rank: dict[str, int] = {}
    for chunk in chunks:
        rank, meta = chunk["rank"], chunk["meta"]
        if not isinstance(rank, int) or rank < 1:
            raise ValueError(
                f"score_chunks: a chunk has rank {rank!r}; ranks are whole numbers from 1"
            )
        if meta.get("source") == "web":
            continue
        path = meta.get("source_path")
        if not path:
            raise ValueError(
                f"score_chunks: the chunk at rank {rank} has no meta.source_path "
                "and is not a web chunk (meta.source == 'web')"
            )
        if path in gold:
            best_rank[path] = min(rank, best_rank.get(path, rank))

    scores = {f"recall@{k}": sum(rank <= k for rank in best_rank.values()) / len(gold) for k in KS}
    scores["reciprocal_rank"] = 1 / min(best_rank.values()) if best_rank else 0.0
    return scores
