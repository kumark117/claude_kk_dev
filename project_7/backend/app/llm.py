"""The language model: a built-in stub, or an OpenAI-compatible proxy."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

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
    """The proxy request failed. Never carries the API key or the response body."""


@dataclass(frozen=True)
class LLM:
    mode: str  # "stub" or "proxy"
    answer: Callable[[str, Sequence[Chunk]], str]


def build_llm(env: Mapping[str, str], transport: httpx.BaseTransport | None = None) -> LLM:
    """Choose the mode: proxy if LLM_PROXY_URL is non-empty, otherwise stub."""
    url = env.get("LLM_PROXY_URL", "")
    if not url:
        return LLM(mode="stub", answer=_stub_answer)

    model = env.get("LLM_PROXY_MODEL", "")
    if not model:
        raise ConfigError("LLM_PROXY_MODEL is required when LLM_PROXY_URL is set")
    key = env.get("LLM_PROXY_API_KEY", "")
    endpoint = f"{url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {key}"} if key else {}

    def answer(question: str, chunks: Sequence[Chunk]) -> str:
        body = {"model": model, "messages": _messages(question, chunks)}
        # "from None" keeps the original exception, which may quote the key or
        # the upstream body, out of any traceback.
        try:
            with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
                response = client.post(endpoint, headers=headers, json=body)
        except (httpx.HTTPError, httpx.InvalidURL, UnicodeError):
            raise LLMError("The request to the LLM proxy failed") from None
        if not response.is_success:
            raise LLMError(f"The LLM proxy returned status {response.status_code}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError("The LLM proxy returned an unusable response") from None
        if not isinstance(content, str) or not content:
            raise LLMError("The LLM proxy returned no answer text")
        return content

    return LLM(mode="proxy", answer=answer)


def _stub_answer(question: str, chunks: Sequence[Chunk]) -> str:
    top = chunks[0]
    return f"[stub] Based on {top.file}: {top.text}"


def _messages(question: str, chunks: Sequence[Chunk]) -> list[dict[str, str]]:
    context = "\n\n".join(f"[File: {c.file}]\n{c.text}" for c in chunks)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n\n{context}\n\nQuestion: {question}"},
    ]
