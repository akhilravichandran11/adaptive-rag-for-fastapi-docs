"""
Search the web with Tavily and return each page as a Haystack Document with its url, for the
AMBIGUOUS and INCORRECT routes. The caller builds the client and passes it in (D4).
"""

import logging
from datetime import UTC, datetime
from typing import Any

from haystack import Document

logger = logging.getLogger(__name__)

SEARCH_DEPTH = "advanced"
MAX_RESULTS_PER_QUERY = 3


class WebSearch:
    def __init__(
        self,
        client: Any,
        *,
        search_depth: str = SEARCH_DEPTH,
        max_results: int = MAX_RESULTS_PER_QUERY,
        record=None,
    ) -> None:
        self.client = client
        self.search_depth = search_depth
        self.max_results = max_results
        self.record = record

    def run(self, queries: list[str]) -> dict[str, list[Document]]:
        documents = []
        seen_urls = set()
        for query in queries:
            started_at = datetime.now(UTC).isoformat() if self.record is not None else None
            try:
                response = self.client.search(
                    query=query,
                    search_depth=self.search_depth,
                    max_results=self.max_results,
                    include_raw_content="markdown",
                    **({"include_usage": True} if self.record is not None else {}),
                )
            except Exception:
                if self.record is not None:
                    ended_at = datetime.now(UTC).isoformat()
                    self.record(
                        provider="tavily",
                        operation="search",
                        model=None,
                        outcome="error",
                        usage=None,
                        started_at=started_at,
                        ended_at=ended_at,
                    )
                raise
            if self.record is not None:
                ended_at = datetime.now(UTC).isoformat()
                self.record(
                    provider="tavily",
                    operation="search",
                    model=None,
                    outcome="response",
                    usage=response.get("usage"),
                    started_at=started_at,
                    ended_at=ended_at,
                )
            results = response["results"]
            added = 0
            for result in results:
                url = result["url"]
                # raw_content is the full page and can be missing; content is Tavily's snippet.
                content = result.get("raw_content") or ""
                if not content.strip():
                    content = result.get("content") or ""
                if url in seen_urls or not content.strip():
                    continue
                seen_urls.add(url)
                documents.append(
                    Document(
                        content=content,
                        meta={"url": url, "title": result.get("title") or "", "web_query": query},
                        score=result.get("score"),
                    )
                )
                added += 1
            logger.info("web search %r: %d results, %d new", query, len(results), added)
        return {"documents": documents}
