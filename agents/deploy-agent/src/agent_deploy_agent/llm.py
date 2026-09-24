"""LLM provider selection for Deploy Agent."""

from __future__ import annotations

import os

from langchain_anthropic import ChatAnthropic
from pydantic import SecretStr
from anthropic import (
    Anthropic,
    AsyncAnthropic,
    DefaultAsyncHttpxClient,
    DefaultHttpxClient,
)
from langchain_core.language_models import BaseChatModel

MODEL = "claude-sonnet-4-6"


def build_llm(temperature: float = 0) -> BaseChatModel:
    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        raise RuntimeError("LLM_API_KEY is missing; add it to .env or project secrets")

    # Inject secrets at send time so LangChain traces cannot serialize them.
    def inject_headers(request):
        request.headers.pop("authorization", None)
        request.headers.pop("x-api-key", None)
        request.headers["api-key"] = api_key
        request.headers["anthropic-version"] = "2023-06-01"

    async def inject_async_headers(request):
        inject_headers(request)

    http_client = DefaultHttpxClient(event_hooks={"request": [inject_headers]}, follow_redirects=False)
    http_async_client = DefaultAsyncHttpxClient(event_hooks={"request": [inject_async_headers]}, follow_redirects=False)
    llm = ChatAnthropic(model_name=MODEL, api_key=SecretStr("unused"), base_url="https://grove-gateway-prod.azure-api.net/grove-foundry-prod/anthropic")
    # ChatAnthropic has no public HTTP-client option; its cached SDK clients
    # keep transport credentials outside serializable model fields.
    llm._client = Anthropic(**llm._client_params, http_client=http_client)
    llm._async_client = AsyncAnthropic(**llm._client_params, http_client=http_async_client)
    return llm
