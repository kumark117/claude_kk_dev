"""Turn a question and retrieved chunks into an answer: stub or proxy."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import httpx

from app.retrieval import Match

TIMEOUT_SECONDS = 30.0
SYSTEM_PROMPT = (
    "Answer the question using only the supplied context. "
    "If the context is not enough to answer, say that it is not enough."
)


class LLMError(Exception):
    """The proxy request failed. The message never holds secrets or reply bodies."""


@dataclass(frozen=True)
class LLMConfig:
    url: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False)

    @property
    def mode(self) -> str:
        return "proxy" if self.url else "stub"


def load_config(env: Mapping[str, str]) -> LLMConfig:
    config = LLMConfig(
        url=env.get("LLM_PROXY_URL", ""),
        model=env.get("LLM_PROXY_MODEL", ""),
        api_key=env.get("LLM_PROXY_API_KEY", ""),
    )
    if config.mode == "proxy" and not config.model:
        raise ValueError("LLM_PROXY_MODEL is required when LLM_PROXY_URL is set")
    return config


def generate_answer(
    question: str,
    chunks: Sequence[Match],
    config: LLMConfig,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Answer from the chunks, which are ordered best first and never empty."""
    if config.mode == "stub":
        top = chunks[0]
        return f"[stub] Based on {top.file}: {top.text}"
    return _ask_proxy(question, chunks, config, transport)


def _ask_proxy(
    question: str,
    chunks: Sequence[Match],
    config: LLMConfig,
    transport: httpx.BaseTransport | None,
) -> str:
    context = "\n\n".join(f"File: {chunk.file}\n{chunk.text}" for chunk in chunks)
    body = {
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

    # "from None" below: the original exceptions can quote the key or the reply.
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, transport=transport) as client:
            response = client.post(url, json=body, headers=headers)
        if not response.is_success:
            raise LLMError(f"LLM proxy returned status {response.status_code}")
        content = response.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, httpx.InvalidURL) as error:
        raise LLMError(f"LLM request failed: {type(error).__name__}") from None
    except (ValueError, KeyError, IndexError, TypeError):
        # ValueError covers invalid JSON and a header that cannot be encoded.
        raise LLMError("LLM request or reply was invalid") from None
    if not isinstance(content, str) or not content:
        raise LLMError("LLM reply had no text content")
    return content
