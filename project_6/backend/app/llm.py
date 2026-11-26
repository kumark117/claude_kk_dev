"""Answer generation: a built-in stub, or an OpenAI-compatible endpoint."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

import httpx

from app.retrieval import Match

TIMEOUT_SECONDS = 30.0

SYSTEM_PROMPT = (
    "Answer the question using only the supplied context. "
    "If the context is not enough to answer, say so."
)


class ConfigError(Exception):
    """The LLM settings are incomplete."""


class LLMError(Exception):
    """The proxy request failed or returned an unusable reply."""


@dataclass(frozen=True)
class LLM:
    mode: Literal["stub", "proxy"]
    # Takes the question and the retrieved chunks (at least one).
    answer: Callable[[str, list[Match]], str]


def create_llm(
    env: Mapping[str, str], transport: httpx.BaseTransport | None = None
) -> LLM:
    """Choose proxy mode if LLM_PROXY_URL is non-empty, otherwise stub mode."""
    base = env.get("LLM_PROXY_URL", "")
    if not base:
        return LLM("stub", _stub_answer)

    model = env.get("LLM_PROXY_MODEL", "")
    if not model:
        raise ConfigError("LLM_PROXY_MODEL is required when LLM_PROXY_URL is set")
    url = f"{base.removesuffix('/')}/chat/completions"
    key = env.get("LLM_PROXY_API_KEY", "")
    headers = {"Authorization": f"Bearer {key}"} if key else {}

    def proxy_answer(question: str, chunks: list[Match]) -> str:
        body = {"model": model, "messages": _messages(question, chunks)}
        try:
            with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
                response = client.post(url, headers=headers, json=body)
            if not response.is_success:
                raise LLMError(f"LLM endpoint returned status {response.status_code}")
            content = response.json()["choices"][0]["message"]["content"]
        except (
            httpx.HTTPError,
            httpx.InvalidURL,
            ValueError,  # invalid JSON, or a key that cannot be sent as a header
            LookupError,
            TypeError,
        ) as error:
            # "from None": the original error may quote the key or the reply.
            raise LLMError(f"LLM request failed ({type(error).__name__})") from None
        if not isinstance(content, str) or not content:
            raise LLMError("LLM reply has no text content")
        return content

    return LLM("proxy", proxy_answer)


def _stub_answer(question: str, chunks: list[Match]) -> str:
    return f"[stub] Based on {chunks[0].file}: {chunks[0].text}"


def _messages(question: str, chunks: list[Match]) -> list[dict[str, str]]:
    context = "\n\n".join(f"[{chunk.file}]\n{chunk.text}" for chunk in chunks)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
    ]
