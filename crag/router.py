"""
Two structured calls on the light model (D14): label each question before anything is retrieved,
calling it fastapi when unsure (D13), and reply to small talk (D20).
"""

import logging
from typing import Any, Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# A wrong decline loses an answer the docs or the web had; a wrong accept costs one search (D13).
ROUTE_PROMPT = """You sort the messages sent to an assistant that answers questions about \
building, testing, deploying and securing APIs with FastAPI. Give the message below exactly one \
route:

- fastapi: a question a developer building an API with FastAPI could ask, even when it never \
names FastAPI. That covers HTTP requests and responses, Python, Pydantic, Starlette, async code, \
databases, authentication and security, testing, servers such as Uvicorn, deployment, \
monitoring, and the services an API calls, such as storage, email or notifications.
- chitchat: a greeting, thanks, a goodbye or other small talk with no technical question in it.
- out_of_scope: a question clearly unrelated to building, testing, deploying or securing an API \
with FastAPI, and to the tools and services such an API uses.

A greeting or thanks that also asks a question gets the route of its question, never chitchat. \
When you are unsure, choose fastapi.

Message: {query}"""

# This reply carries no sources, so it may state nothing a reader would need one for (D20).
CHITCHAT_PROMPT = """You reply to small talk on behalf of an assistant that answers questions \
about FastAPI. The message below is a greeting, thanks or other small talk. Reply in one or two \
friendly sentences, and end by inviting the user to ask a question about FastAPI. Never answer a \
technical question and never state a fact about FastAPI, even when the message asks for one: \
questions are answered elsewhere from cited sources, and this reply has none.

Message: {query}"""


class RouteReply(BaseModel):
    route: Literal["fastapi", "chitchat", "out_of_scope"]


class ChitchatReply(BaseModel):
    answer: str


class Router:
    def __init__(self, llm: Any) -> None:
        self.llm = llm

    def route(self, query: str) -> dict[str, str]:
        reply, model = self.llm.ask(ROUTE_PROMPT.format(query=query), RouteReply)
        logger.info("route: %s (%s)", reply.route, model)
        return {"route": reply.route, "model": model}

    def chitchat(self, query: str) -> dict[str, str]:
        reply, model = self.llm.ask(CHITCHAT_PROMPT.format(query=query), ChitchatReply)
        if not reply.answer.strip():
            raise ValueError(f"chitchat: empty answer from {model} for question {query!r}")
        logger.info("chitchat: replied (%s)", model)
        return {"answer": reply.answer, "model": model}
