"""Answer generation: a built-in stub, or an OpenAI-compatible proxy."""

from collections.abc import Mapping
from dataclasses import dataclass, field

import httpx

from app.retrieval import Match

TIMEOUT_SECONDS = 30.0
SYSTEM_PROMPT = (
    "Answer the question using only the supplied context. "
    "If the context is not enough to answer, say that it is not enough."
)


class LLMError(Exception):
    """The proxy request failed or returned an unusable reply."""


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


def config_from_env(env: Mapping[str, str]) -> LLMConfig:
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
    chunks: list[Match],
    config: LLMConfig,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Answer the question from the chunks, which are ordered best first."""
    if config.mode == "stub":
        top = chunks[0]
        return f"[stub] Based on {top.file}: {top.text}"
    return _ask_proxy(question, chunks, config, transport)


def _ask_proxy(
    question: str,
    chunks: list[Match],
    config: LLMConfig,
    transport: httpx.BaseTransport | None,
) -> str:
    context = "\n\n".join(f"[{chunk.file}]\n{chunk.text}" for chunk in chunks)
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Context:\n\n{context}\n\nQuestion: {question}"},
        ],
    }
    headers = {}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    url = f"{config.url.removesuffix('/')}/chat/completions"

    # Errors are raised "from None": the originals may quote the key or the reply.
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
            response = client.post(url, json=payload, headers=headers)
    except (httpx.HTTPError, httpx.InvalidURL, ValueError):
        raise LLMError("Could not complete the request to the LLM") from None

    if not response.is_success:
        raise LLMError(f"The LLM replied with status {response.status_code}")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise LLMError("The LLM reply could not be read") from None
    if not isinstance(content, str) or not content:
        raise LLMError("The LLM reply had no text content")
    return content
