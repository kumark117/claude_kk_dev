"""Answer generation: a built-in stub, or an OpenAI-compatible proxy."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

import httpx

from app.retrieval import Chunk

TIMEOUT_SECONDS = 30.0
SYSTEM_PROMPT = (
    "Answer the question using only the supplied context. "
    "If the context is not enough to answer, say that it is not enough."
)


class ConfigError(Exception):
    """The LLM settings are incomplete."""


class LLMError(Exception):
    """The proxy request failed. Messages never hold keys or response bodies."""


@dataclass(frozen=True)
class LLMSettings:
    url: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False)

    @property
    def mode(self) -> Literal["stub", "proxy"]:
        return "proxy" if self.url else "stub"


def load_llm_settings(env: Mapping[str, str]) -> LLMSettings:
    settings = LLMSettings(
        url=env.get("LLM_PROXY_URL", ""),
        model=env.get("LLM_PROXY_MODEL", ""),
        api_key=env.get("LLM_PROXY_API_KEY", ""),
    )
    if settings.mode == "proxy" and not settings.model:
        raise ConfigError("LLM_PROXY_MODEL is required when LLM_PROXY_URL is set")
    return settings


def generate_answer(
    question: str,
    chunks: list[Chunk],
    settings: LLMSettings,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Answer from the retrieved chunks, which must be non-empty, best first."""
    if settings.mode == "stub":
        top = chunks[0]
        return f"[stub] Based on {top.file}: {top.text}"
    return _ask_proxy(question, chunks, settings, transport)


def _ask_proxy(
    question: str,
    chunks: list[Chunk],
    settings: LLMSettings,
    transport: httpx.BaseTransport | None,
) -> str:
    context = "\n\n".join(f"[{chunk.file}]\n{chunk.text}" for chunk in chunks)
    body = {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
        ],
    }
    headers = {}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"
    url = f"{settings.url.rstrip('/')}/chat/completions"

    # "from None" keeps the original error, which may quote the key or the
    # response body, out of tracebacks.
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
            response = client.post(url, json=body, headers=headers)
        if not response.is_success:
            raise LLMError(f"LLM endpoint returned status {response.status_code}")
        content = response.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
        raise LLMError(f"LLM request failed: {type(error).__name__}") from None
    except (KeyError, IndexError, TypeError):
        raise LLMError("LLM response has no message content") from None
    if not isinstance(content, str) or not content:
        raise LLMError("LLM response has no message content")
    return content
