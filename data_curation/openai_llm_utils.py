"""
Shared LLM client + chat helpers for the data-curation scripts.

Centralizes the OpenAI client and the chat call so every script uses one code
path (and one place to change model, provider base_url, or defaults later).
"""


import logging
import os
from typing import TypeVar

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
    omit,
)
from pydantic import BaseModel
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

DEFAULT_MODEL = "gpt-4o-mini"

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

class LLMError(RuntimeError):
    """Base for errors from the LLM helpers."""

class ModelRefusal(LLMError):
    """Model explicitly refused. Deterministic — do NOT retry."""

class EmptyStructuredResponse(LLMError):
    """HTTP 200 but no parsed content (not a refusal). Often a bad sample — safe to retry."""

# Reuse a single client across calls instead of building one per request.
_client: AsyncOpenAI | None = None
_client_no_retries: AsyncOpenAI | None = None


def configure_logging(level: int = logging.INFO, quiet_httpx: bool = True) -> None:
    """Configure root logging so app, tenacity, and OpenAI SDK logs are visible.

    Call ONCE from a script's entry point (e.g. top of main()), not at import time.
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,  # override any prior basicConfig so our format/level wins
    )
    # OpenAI SDK logs retries at INFO on the "openai" logger — make sure it emits.
    logging.getLogger("openai").setLevel(level)
    if quiet_httpx:
        # httpx logs one INFO line per HTTP request; keep it at WARNING unless debugging.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        


def resolve_model(model: str | None = None) -> str:
    """The model that will actually be used: explicit arg → OPENAI_MODEL env → default."""
    return model or os.getenv("OPENAI_MODEL", DEFAULT_MODEL)

def get_client() -> AsyncOpenAI:
    """Return a lazily-created, shared AsyncOpenAI client (reads OPENAI_API_KEY)."""
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "5")),
            timeout=float(os.getenv("OPENAI_TIMEOUT", "240.0")),  # fail a hung request fast so a retry can fire
            )
    return _client

def get_client_with_no_retries() -> AsyncOpenAI:
    """Return a lazily-created, shared AsyncOpenAI client (reads OPENAI_API_KEY) with no retries."""
    global _client_no_retries
    if _client_no_retries is None:
        _client_no_retries = AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            max_retries=0,
            timeout=float(os.getenv("OPENAI_TIMEOUT", "240.0")),  # fail a hung request fast so a retry can fire
            )
    return _client_no_retries

async def chat(
    prompt: str,
    *,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 3000,
) -> str:
    """
    Send one user-message chat request and return the raw text reply.

    `model` falls back to the OPENAI_MODEL env var, then gpt-4o-mini.
    """
    client = get_client()
    response = await client.chat.completions.create(
        model=resolve_model(model),
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    content = response.choices[0].message.content
    return content if content is not None else ""


async def chat_with_seed(
    prompt: str,
    *,
    seed: int,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 3000,
) -> str:
    """
    Like `chat`, but pins `seed` for best-effort reproducible text output.

    Same prompt + model + seed + temperature should (mostly) return the same
    text. `seed` has the most effect when `temperature > 0` (it fixes the
    sampling rolls); at `temperature = 0` the model is already near-greedy.
    
    Issue- Reproducibility is best-effort, not guaranteed (a model/infra change can still shift output).
    """
    client = get_client()
    response = await client.chat.completions.create(
        model=resolve_model(model),
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
        seed=seed,
    )
    content = response.choices[0].message.content
    return content if content is not None else ""

@retry(
    retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIConnectionError,InternalServerError, EmptyStructuredResponse)),
    stop=stop_after_attempt(int(os.getenv("OPENAI_MAX_RETRIES", "5"))),
    wait=wait_exponential(multiplier=1, max=10),
    before_sleep=before_sleep_log(logger, logging.WARNING), # logs every retry
    reraise=True,  # after the last attempt, raise EmptyStructuredResponse itself,
)  
async def chat_structured(
    prompt: str,
    schema: type[T],
    *,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 3000,
    seed: int | None = None
) -> T:
    """
    Send one user-message request and return a parsed `schema` instance.

    Uses OpenAI structured outputs, so the reply always matches `schema`
    Pass `seed` (with `temperature=0.0`) for best-effort reproducible output;
    reproducibility is best-effort, not guaranteed.
    """
    client = get_client_with_no_retries()
    completion = await client.chat.completions.parse(
        model=resolve_model(model),
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
        seed=seed if seed is not None else omit,   # omit when unset
        response_format=schema,
    )
    
    message = completion.choices[0].message

    if message.refusal:
        raise ModelRefusal(f"Model refused: {message.refusal}")
    if message.parsed is None:
        raise EmptyStructuredResponse("Structured parse returned no content")
    return message.parsed