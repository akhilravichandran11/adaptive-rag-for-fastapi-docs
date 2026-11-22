import logging
import os
from dataclasses import replace
from typing import Any

from haystack import Document, component
from haystack.utils import Secret
from voyageai import Client

logger = logging.getLogger(__name__)


@component
class UsageVoyageTextEmbedder:
    def __init__(self, *, model: str, output_dimension: int, record):
        self.model = model
        self.output_dimension = output_dimension
        self.record = record
        self.timeout = int(os.environ.get("VOYAGE_TIMEOUT", "30"))
        self.max_retries = int(os.environ.get("VOYAGE_MAX_RETRIES", "5"))
        self.client = None

    def warm_up(self):
        if self.client is None:
            self.client = Client(
                api_key=Secret.from_env_var("VOYAGE_API_KEY").resolve_value(),
                timeout=self.timeout,
                max_retries=self.max_retries,
            )

    @component.output_types(embedding=list[float], meta=dict[str, Any])
    def run(self, text: str) -> dict[str, Any]:
        if not isinstance(text, str):
            raise TypeError(f"Voyage query embedding requires a string; got {text!r}")
        self.warm_up()
        try:
            response = self.client.embed(
                texts=[text],
                model=self.model,
                input_type="query",
                truncation=True,
                output_dtype="float",
                output_dimension=self.output_dimension,
            )
        except Exception:
            self.record(
                provider="voyage",
                operation="embedding",
                model=self.model,
                outcome="error",
                usage=None,
            )
            raise
        self.record(
            provider="voyage",
            operation="embedding",
            model=self.model,
            outcome="response",
            usage={"total_tokens": response.total_tokens},
        )
        return {
            "embedding": response.embeddings[0],
            "meta": {"total_tokens": response.total_tokens},
        }


@component
class UsageVoyageRanker:
    def __init__(self, *, model: str, top_k: int, record):
        self.model = model
        self.top_k = top_k
        self.record = record
        self.timeout = int(os.environ.get("VOYAGE_TIMEOUT", "30"))
        self.max_retries = int(os.environ.get("VOYAGE_MAX_RETRIES", "5"))
        self.client = None

    def warm_up(self):
        if self.client is None:
            self.client = Client(
                api_key=Secret.from_env_var("VOYAGE_API_KEY").resolve_value(),
                timeout=self.timeout,
                max_retries=self.max_retries,
            )

    @component.output_types(documents=list[Document])
    def run(
        self, query: str, documents: list[Document], top_k: int | None = None
    ) -> dict[str, list[Document]]:
        top_k = top_k if top_k is not None else self.top_k
        if top_k is not None and top_k <= 0:
            raise ValueError(f"Voyage reranking requires top_k > 0; got {top_k}")
        input_docs = [doc.content or "" for doc in documents]
        if len(input_docs) > 1000:
            logger.warning("Voyage reranking truncates %d documents to 1000", len(input_docs))
            input_docs = input_docs[:1000]
        self.warm_up()
        try:
            response = self.client.rerank(
                model=self.model,
                query=query,
                documents=input_docs,
                top_k=top_k,
            )
        except Exception:
            self.record(
                provider="voyage",
                operation="rerank",
                model=self.model,
                outcome="error",
                usage=None,
            )
            raise
        self.record(
            provider="voyage",
            operation="rerank",
            model=self.model,
            outcome="response",
            usage={"total_tokens": response.total_tokens},
        )
        return {
            "documents": [
                replace(documents[result.index], score=result.relevance_score)
                for result in response.results
            ]
        }
