"""Turn a question and retrieved chunks into an answer, by stub or proxy."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

import httpx

from app.retrieval import Chunk

TIMEOUT_SECONDS = 30.0
SYSTEM_PROMPT = (
    "Answer the question using only the supplied context. If the context is not "
    "enough to answer, say that it is not enough."
)

Mode = Literal["stub", "proxy"]


class LLMError(Exception):
    """The proxy did not return a usable answer."""


@dataclass(frozen=True)
class LLMSettings:
    proxy_url: str = ""
    api_key: str = field(default="", repr=False)
    model: str = ""

    @property
    def mode(self) -> Mode:
        return "proxy" if self.proxy_url else "stub"


def load_settings(env: Mapping[str, str]) -> LLMSettings:
    settings = LLMSettings(
        proxy_url=env.get("LLM_PROXY_URL", ""),
        api_key=env.get("LLM_PROXY_API_KEY", ""),
        model=env.get("LLM_PROXY_MODEL", ""),
    )
    if settings.mode == "proxy" and not settings.model:
        raise ValueError("LLM_PROXY_MODEL is required when LLM_PROXY_URL is set")
    return settings


def generate_answer(
    question: str,
    chunks: Sequence[Chunk],
    settings: LLMSettings,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Answer from chunks, which are ordered best first and must not be empty."""
    if settings.mode == "stub":
        top = chunks[0]
        return f"[stub] Based on {top.file}: {top.text}"
    return _ask_proxy(question, chunks, settings, transport)


def _ask_proxy(
    question: str,
    chunks: Sequence[Chunk],
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
    url = f"{settings.proxy_url.removesuffix('/')}/chat/completions"

    # "from None" everywhere: the original errors can quote the key or the reply.
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
            response = client.post(url, json=body, headers=headers)
    except (httpx.HTTPError, httpx.InvalidURL, ValueError):
        raise LLMError("The request to the LLM proxy failed") from None
    if not response.is_success:
        raise LLMError(f"The LLM proxy returned status {response.status_code}")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, LookupError, TypeError):
        raise LLMError("The LLM proxy reply was not in the expected form") from None
    if not isinstance(content, str) or not content:
        raise LLMError("The LLM proxy reply had no text content")
    return content
