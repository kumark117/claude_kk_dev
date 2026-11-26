"""Answer a question from retrieved chunks: built-in stub or OpenAI-compatible proxy."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import httpx

from app.retrieval import Chunk

TIMEOUT_SECONDS = 30.0
SYSTEM_PROMPT = (
    "Answer the question using only the supplied context. "
    "If the context is not enough to answer, say so."
)


class ConfigError(Exception):
    """The LLM settings are incomplete."""


class LLMError(Exception):
    """The LLM request failed. Messages never hold keys or response bodies."""


Answer = Callable[[str, Sequence[Chunk]], str]


@dataclass(frozen=True)
class LLM:
    mode: Literal["stub", "proxy"]
    answer: Answer


def _stub_answer(question: str, chunks: Sequence[Chunk]) -> str:
    top = chunks[0]
    return f"[stub] Based on {top.file}: {top.text}"


def _user_message(question: str, chunks: Sequence[Chunk]) -> str:
    context = "\n\n".join(f"File: {c.file}\n{c.text}" for c in chunks)
    return f"Context:\n\n{context}\n\nQuestion: {question}"


def _proxy_answer(
    base_url: str, model: str, api_key: str, transport: httpx.BaseTransport | None
) -> Answer:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def answer(question: str, chunks: Sequence[Chunk]) -> str:
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _user_message(question, chunks)},
            ],
        }
        # "from None" keeps the original error, which may quote the key or the
        # response, out of tracebacks.
        try:
            with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
                response = client.post(url, headers=headers, json=body)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            raise LLMError(f"Request failed: {type(error).__name__}") from None
        if not response.is_success:
            raise LLMError(f"Upstream status {response.status_code}") from None
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, LookupError, TypeError, RecursionError):
            raise LLMError("Upstream reply was not a chat completion") from None
        if not isinstance(content, str) or not content:
            raise LLMError("Upstream reply had no text content") from None
        return content

    return answer


def create_llm(env: Mapping[str, str], transport: httpx.BaseTransport | None = None) -> LLM:
    """Choose the mode from the environment: proxy if LLM_PROXY_URL is set."""
    base_url = env.get("LLM_PROXY_URL", "")
    if not base_url:
        return LLM("stub", _stub_answer)
    model = env.get("LLM_PROXY_MODEL", "")
    if not model:
        raise ConfigError("LLM_PROXY_MODEL is required when LLM_PROXY_URL is set")
    api_key = env.get("LLM_PROXY_API_KEY", "")
    return LLM("proxy", _proxy_answer(base_url, model, api_key, transport))
