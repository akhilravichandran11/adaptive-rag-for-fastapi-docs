"""
A semantic answer cache on Upstash: a question close enough in meaning to one answered in the
last day gets that answer back. Upstash embeds the questions itself. The client always comes
from the caller (D4): passed in, or built by the factory the caller gives ``open_cache``.
"""

import hashlib
import json
import logging
from collections.abc import Callable
from typing import Any

import httpx

logger = logging.getLogger(__name__)

INDEX = "answers"
PREFIX = "cache:"
# Why 0.94 and 24 hours: backend/DECISIONS.md, 2026-11-22 (semantic answer cache).
THRESHOLD = 0.94
EXPIRY_SECONDS = 24 * 60 * 60


class SemanticCache:
    def __init__(self, client: Any) -> None:
        self.client = client

    def ensure_index(self) -> None:
        # A restart leaves an existing index alone.
        self.client.search.create_index(
            name=INDEX,
            schema={"question": "HYBRID"},
            data_type="HASH",
            prefixes=PREFIX,
            exists_ok=True,
        )

    def lookup(self, question: str) -> dict:
        results = self.client.search.index(INDEX).query(
            filter={"question": {"$ann": question}}, limit=1, select={}
        )
        if not results:
            return {"response": None, "score": None}
        [nearest] = results
        if nearest.score < THRESHOLD:
            return {"response": None, "score": nearest.score}
        stored = self.client.hget(nearest.key, "response")
        # None when the key expired a moment before the index dropped it.
        response = None if stored is None else json.loads(stored)
        return {"response": response, "score": nearest.score}

    def store(self, question: str, response: dict) -> None:
        key = PREFIX + hashlib.sha256(question.encode()).hexdigest()
        self.client.hset(key, values={"question": question, "response": json.dumps(response)})
        self.client.expire(key, EXPIRY_SECONDS)


def open_cache(
    url: str | None, token: str | None, client_factory: Callable[[str, str], Any]
) -> SemanticCache | None:
    if not url or not token:
        logger.info(
            "cache off: UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN are not both set"
        )
        return None
    cache = SemanticCache(client_factory(url, token))
    # The only cache error caught: a free database that vanished turns the cache off, not the app.
    try:
        cache.ensure_index()
    except httpx.TransportError as exc:
        logger.warning(
            "cache off: cannot reach the Upstash database (%s); make a new database or unset "
            "UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN",
            exc,
        )
        return None
    return cache
