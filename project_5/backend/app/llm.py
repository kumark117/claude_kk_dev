"""Answer a question from retrieved chunks, with a stub or an LLM proxy."""

from collections.abc import Mapping
from dataclasses import dataclass, field

import httpx

from app.retrieval import Chunk

TIMEOUT_SECONDS = 30
SYSTEM_PROMPT = (
    "Answer the question using only the supplied context. "
    "If the context is not enough to answer, say so."
)


class LLMError(Exception):
    """The proxy request failed. Messages never hold the key or response body."""


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
        raise ValueError("LLM_PROXY_MODEL is required when LLM_PROXY_URL is set")
    return config


def generate_answer(
    question: str,
    chunks: list[Chunk],
    config: LLMConfig,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Return the answer text. `chunks` is non-empty, best match first."""
    if config.mode == "stub":
        top = chunks[0]
        return f"[stub] Based on {top.file}: {top.text}"
    return _ask_proxy(question, chunks, config, transport)


def _ask_proxy(
    question: str,
    chunks: list[Chunk],
    config: LLMConfig,
    transport: httpx.BaseTransport | None,
) -> str:
    headers = {}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    context = "\n\n".join(f"[{chunk.file}]\n{chunk.text}" for chunk in chunks)
    body = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Context:\n\n{context}\n\nQuestion: {question}",
            },
        ],
    }
    # `from None` throughout: the original errors can quote the key or the body.
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
            response = client.post(
                f"{config.url.removesuffix('/')}/chat/completions",
                headers=headers,
                json=body,
            )
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
        raise LLMError(f"Request failed: {type(error).__name__}") from None
    if not response.is_success:
        raise LLMError(f"Upstream returned status {response.status_code}")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise LLMError("Upstream response was not understood") from None
    if not isinstance(content, str) or not content:
        raise LLMError("Upstream response had no answer text")
    return content
