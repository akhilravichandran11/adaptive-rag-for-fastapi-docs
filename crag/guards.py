"""
Clean every web page before refinement or the generator sees it (D41): no page can open or
close an excerpt tag, and a page that tells the model to ignore its instructions is dropped.
Why a wrapper, and what it cannot catch: crag/DECISIONS.md, 2026-11-22 (document guard).
"""

import logging
import re
from typing import Any

from haystack import Document

logger = logging.getLogger(__name__)

# A match drops the whole page, so each phrase names instructions explicitly, and none is as
# broad as "you are now".
OVERRIDE_PHRASES = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "ignore prior instructions",
    "ignore all prior instructions",
    "disregard previous instructions",
    "disregard all previous instructions",
    "disregard the above instructions",
)

# A model can still read "</ excerpt>" as a tag, or one hidden by a common invisible character
# (zero-width, soft hyphen, direction mark), so these may sit in either gap. Only the whole
# word counts, so code such as "n <excerpt_len" is left alone.
GAP = r"[\s\u0000\u00ad\u034f\u180e\u200b-\u200f\u2060-\u2064\ufeff]*"
EXCERPT_TAG = re.compile(rf"<(?={GAP}/?{GAP}excerpt\b)", re.IGNORECASE)


class DocumentGuard:
    def __init__(self, web_search: Any) -> None:
        self.web_search = web_search

    def run(self, queries: list[str]) -> dict[str, list[Document]]:
        kept = []
        for doc in self.web_search.run(queries=queries)["documents"]:
            # The title is the page's own text and reaches refinement's prompt. Whitespace is
            # collapsed, so a phrase wrapped across two lines still matches.
            text = " ".join(f"{doc.meta.get('title', '')} {doc.content}".split()).lower()
            phrase = next((found for found in OVERRIDE_PHRASES if found in text), None)
            if phrase is not None:
                logger.warning(
                    "document guard: dropped %s, it contains %r", doc.meta["url"], phrase
                )
                continue
            # Only the "<" changes, so a person still reads the word and the model reads no tag.
            content = EXCERPT_TAG.sub("&lt;", doc.content)
            meta = dict(doc.meta)
            if "title" in meta:
                meta["title"] = EXCERPT_TAG.sub("&lt;", meta["title"])
            kept.append(Document(content=content, meta=meta, score=doc.score))
        return {"documents": kept}
