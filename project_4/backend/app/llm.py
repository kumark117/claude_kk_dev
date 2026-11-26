"""Turn a question and retrieved chunks into an answer: stub or proxy."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import httpx

from app.retrieval import Chunk

TIMEOUT_SECONDS = 30.0
SYSTEM_PROMPT = (
    "Answer the question using only the supplied context. "
    "If the context is not enough to answer, say so."
)


class LLMError(Exception):
    """The LLM request failed. The message never holds secrets or reply bodies."""


class ConfigError(Exception):
    """The LLM settings are incomplete."""


@dataclass(frozen=True)
class LLMConfig:
    url: str = ""
    api_key: str = field(default="", repr=False)
    model: str = ""

    @property
    def mode(self) -> str:
        return "proxy" if self.url else "stub"


def load_config(env: Mapping[str, str]) -> LLMConfig:
    config = LLMConfig(
        url=env.get("LLM_PROXY_URL", ""),
        api_key=env.get("LLM_PROXY_API_KEY", ""),
        model=env.get("LLM_PROXY_MODEL", ""),
    )
    if config.mode == "proxy" and not config.model:
        raise ConfigError("LLM_PROXY_MODEL is required when LLM_PROXY_URL is set")
    return config


def generate_answer(
    question: str,
    chunks: Sequence[Chunk],
    config: LLMConfig,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Answer the question from the chunks, which must be non-empty and ranked."""
    if config.mode == "stub":
        top = chunks[0]
        return f"[stub] Based on {top.file}: {top.text}"
    return _ask_proxy(question, chunks, config, transport)


def _ask_proxy(
    question: str,
    chunks: Sequence[Chunk],
    config: LLMConfig,
    transport: httpx.BaseTransport | None,
) -> str:
    context = "\n\n".join(f"File: {c.file}\n{c.text}" for c in chunks)
    body = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{context}\n\nQuestion: {question}"},
        ],
    }
    headers = {}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    # "from None" below: the underlying errors can quote headers or reply bodies.
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
            response = client.post(
                f"{config.url.rstrip('/')}/chat/completions",
                headers=headers,
                json=body,
            )
        if not response.is_success:
            raise LLMError(f"LLM returned status {response.status_code}")
        content = response.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, httpx.InvalidURL) as error:
        raise LLMError(f"LLM request failed: {type(error).__name__}") from None
    except (ValueError, LookupError, TypeError):
        raise LLMError("LLM request or reply was not usable") from None
    if not isinstance(content, str) or not content:
        raise LLMError("LLM reply had no text content")
    return content
