"""The built-in stub and the OpenAI-compatible proxy."""

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

Mode = Literal["stub", "proxy"]


class ConfigError(Exception):
    """The environment settings are incomplete."""


class LLMError(Exception):
    """The LLM request failed. The message never holds secrets or reply bodies."""


@dataclass(frozen=True)
class LLM:
    mode: Mode
    # Takes the question and the retrieved chunks (best first, at least one).
    answer: Callable[[str, Sequence[Chunk]], str]


def create_llm(
    env: Mapping[str, str], transport: httpx.BaseTransport | None = None
) -> LLM:
    """Choose stub or proxy mode from the environment."""
    base = env.get("LLM_PROXY_URL", "")
    if not base:
        return LLM("stub", _stub_answer)
    model = env.get("LLM_PROXY_MODEL", "")
    if not model:
        raise ConfigError("LLM_PROXY_MODEL is required when LLM_PROXY_URL is set")
    url = f"{base.removesuffix('/')}/chat/completions"
    key = env.get("LLM_PROXY_API_KEY", "")
    headers = {"Authorization": f"Bearer {key}"} if key else {}

    def answer(question: str, chunks: Sequence[Chunk]) -> str:
        body = {"model": model, "messages": _messages(question, chunks)}
        return _request_completion(url, headers, body, transport)

    return LLM("proxy", answer)


def _stub_answer(question: str, chunks: Sequence[Chunk]) -> str:
    top = chunks[0]
    return f"[stub] Based on {top.file}: {top.text}"


def _messages(question: str, chunks: Sequence[Chunk]) -> list[dict[str, str]]:
    context = "\n\n".join(f"[{chunk.file}]\n{chunk.text}" for chunk in chunks)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n\n{context}\n\nQuestion: {question}"},
    ]


def _request_completion(
    url: str,
    headers: dict[str, str],
    body: dict,
    transport: httpx.BaseTransport | None,
) -> str:
    # Errors are raised "from None" so that no traceback can show the key or body.
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, transport=transport) as client:
            response = client.post(url, headers=headers, json=body)
        if not response.is_success:
            raise LLMError(f"LLM returned status {response.status_code}")
        content = response.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, httpx.InvalidURL) as error:
        raise LLMError(f"LLM request failed: {type(error).__name__}") from None
    except (ValueError, KeyError, IndexError, TypeError, RecursionError):
        raise LLMError("LLM request or reply was invalid") from None
    if not isinstance(content, str) or not content:
        raise LLMError("LLM reply had no content")
    return content
