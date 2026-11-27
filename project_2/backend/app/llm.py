"""Turn a question and retrieved chunks into an answer: stub or OpenAI proxy."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

import httpx

from app.retrieval import Result

TIMEOUT_SECONDS = 30.0
SYSTEM_PROMPT = (
    "Answer the question using only the supplied context. "
    "If the context is not enough to answer, say so."
)


class ConfigError(Exception):
    """The LLM settings are incomplete."""


class LLMError(Exception):
    """The LLM proxy request failed."""


@dataclass(frozen=True)
class LLMSettings:
    url: str = ""
    api_key: str = field(default="", repr=False)
    model: str = ""

    @property
    def mode(self) -> Literal["stub", "proxy"]:
        return "proxy" if self.url else "stub"


def load_settings(env: Mapping[str, str]) -> LLMSettings:
    settings = LLMSettings(
        url=env.get("LLM_PROXY_URL", ""),
        api_key=env.get("LLM_PROXY_API_KEY", ""),
        model=env.get("LLM_PROXY_MODEL", ""),
    )
    if settings.mode == "proxy" and not settings.model:
        raise ConfigError("LLM_PROXY_MODEL is required when LLM_PROXY_URL is set")
    return settings


def generate_answer(
    question: str,
    chunks: Sequence[Result],
    settings: LLMSettings,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Answer from the chunks, which must be non-empty and ordered best first."""
    if settings.mode == "stub":
        top = chunks[0]
        return f"[stub] Based on {top.file}: {top.text}"
    return _ask_proxy(question, chunks, settings, transport)


def _ask_proxy(
    question: str,
    chunks: Sequence[Result],
    settings: LLMSettings,
    transport: httpx.BaseTransport | None,
) -> str:
    context = "\n\n".join(f"[{chunk.file}]\n{chunk.text}" for chunk in chunks)
    body = {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Context:\n\n{context}\n\nQuestion: {question}",
            },
        ],
    }
    headers = {}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"

    # Errors are raised "from None": the originals can quote the key or the
    # upstream response, which must never reach logs.
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, transport=transport) as client:
            response = client.post(
                f"{settings.url.removesuffix('/')}/chat/completions",
                headers=headers,
                json=body,
            )
        if not response.is_success:
            raise LLMError(
                f"LLM proxy returned status {response.status_code}"
            ) from None
        content = response.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, httpx.InvalidURL) as error:
        raise LLMError(f"LLM request failed: {type(error).__name__}") from None
    except (ValueError, KeyError, IndexError, TypeError):
        raise LLMError("LLM request or response was invalid") from None

    if not isinstance(content, str) or not content:
        raise LLMError("LLM response had no text content") from None
    return content
