"""
One structured Gemini call shared by every CRAG step: the primary model with retries, then
one fallback model with the same retries, then raise (D5).
"""

import logging

from google.genai import errors
from pydantic import BaseModel, ValidationError
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from observability.costs import USAGE_FIELDS

FALLBACK_MODEL = "gemini-flash-lite-latest"
MAX_ATTEMPTS = 3
RETRIED_ERRORS = (errors.ClientError, errors.ServerError, ValidationError)

logger = logging.getLogger(__name__)


def is_retryable(error: BaseException) -> bool:
    """Only a rate limit, a server error or a bad reply can pass on a retry; a 400 or a bad
    key is a bug in the request, and retrying it would only hide it."""
    if isinstance(error, errors.ClientError):
        return error.code == 429
    return isinstance(error, RETRIED_ERRORS)


class StructuredLLM:
    def __init__(
        self, client, *, primary: str, fallback: str = FALLBACK_MODEL, record=None
    ) -> None:
        self.client = client
        self.primary = primary
        self.fallback = fallback
        self.record = record

    def ask(self, prompt: str, schema: type[BaseModel]) -> tuple[BaseModel, str]:
        try:
            return self._generate(self.primary, prompt, schema)
        except RETRIED_ERRORS as error:
            if not is_retryable(error):
                raise
            logger.warning(
                "Gemini %s failed after %d attempts (%s: %s); falling back to %s",
                self.primary,
                MAX_ATTEMPTS,
                type(error).__name__,
                error,
                self.fallback,
            )
        return self._generate(self.fallback, prompt, schema)

    @retry(
        retry=retry_if_exception(is_retryable),
        stop=stop_after_attempt(MAX_ATTEMPTS),
        wait=wait_exponential(max=10),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _generate(self, model: str, prompt: str, schema: type[BaseModel]) -> tuple[BaseModel, str]:
        try:
            response = self.client.models.generate_content(
                model=model,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": schema,
                    "temperature": 0,
                },
            )
        except Exception:
            if self.record is not None:
                self.record(
                    provider="gemini",
                    operation="generation",
                    model=model,
                    outcome="error",
                    usage=None,
                )
            raise
        if self.record is not None:
            metadata = getattr(response, "usage_metadata", None)
            usage = {
                field: getattr(metadata, field)
                for field in USAGE_FIELDS["gemini"]
                if getattr(metadata, field, None) is not None
            }
            self.record(
                provider="gemini",
                operation="generation",
                model=response.model_version or model,
                outcome="response",
                usage=usage,
            )
        return schema.model_validate_json(response.text), response.model_version or model
