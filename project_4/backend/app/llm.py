"""Answer a question from retrieved chunks, with a stub or an OpenAI-style proxy."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

import httpx

from app.retrieval import Hit

TIMEOUT_SECONDS = 30.0
SYSTEM_PROMPT = (
    "Answer the question using only the supplied context. "
    "If the context is not enough to answer, say so."
)


class LLMError(Exception):
    """The proxy request failed. Messages never hold the key or the response body."""


@dataclass(frozen=True)
class LLMSettings:
    url: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False)

    @property
    def mode(self) -> Literal["stub", "proxy"]:
        return "proxy" if self.url else "stub"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "LLMSettings":
        settings = cls(
            url=env.get("LLM_PROXY_URL", "").strip(),
            model=env.get("LLM_PROXY_MODEL", "").strip(),
            api_key=env.get("LLM_PROXY_API_KEY", "").strip(),
        )
        if settings.url and not settings.model:
            raise ValueError("LLM_PROXY_MODEL is required when LLM_PROXY_URL is set")
        return settings


def generate_answer(
    question: str,
    chunks: list[Hit],
    settings: LLMSettings,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Return the answer text. chunks must be non-empty and ordered best first."""
    if settings.mode == "stub":
        top = chunks[0]
        return f"[stub] Based on {top.file}: {top.text}"

    context = "\n\n".join(f"[{chunk.file}]\n{chunk.text}" for chunk in chunks)
    body = {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Context:\n\n{context}\n\nQuestion: {question}"},
        ],
    }
    headers = {}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"
    url = f"{settings.url.removesuffix('/')}/chat/completions"

    # "from None" keeps the original error, which may quote the key or the
    # response, out of any traceback.
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, transport=transport) as client:
            response = client.post(url, json=body, headers=headers)
    except (httpx.HTTPError, httpx.InvalidURL, UnicodeError) as error:
        raise LLMError(f"request failed: {type(error).__name__}") from None
    if not response.is_success:
        raise LLMError(f"upstream returned status {response.status_code}")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError, RecursionError):
        raise LLMError("upstream reply was not in the expected format") from None
    if not isinstance(content, str) or not content.strip():
        raise LLMError("upstream reply had no text content")
    return content
