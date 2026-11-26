"""Turn a question and retrieved chunks into an answer: stub or proxy."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import httpx

from app.retrieval import Hit

TIMEOUT_SECONDS = 30.0
SYSTEM_PROMPT = (
    "Answer the question using only the supplied context. "
    "If the context is not enough to answer, say so."
)


class LLMError(Exception):
    """The proxy did not produce an answer.

    Messages must never contain the API key or the upstream response body.
    """


@dataclass(frozen=True)
class LLMSettings:
    url: str = ""
    api_key: str = field(default="", repr=False)
    model: str = ""

    @property
    def mode(self) -> str:
        return "proxy" if self.url else "stub"


def load_settings(env: Mapping[str, str]) -> LLMSettings:
    settings = LLMSettings(
        url=env.get("LLM_PROXY_URL", ""),
        api_key=env.get("LLM_PROXY_API_KEY", ""),
        model=env.get("LLM_PROXY_MODEL", ""),
    )
    if settings.mode == "proxy" and not settings.model:
        raise ValueError("LLM_PROXY_MODEL is required when LLM_PROXY_URL is set")
    return settings


def generate_answer(
    settings: LLMSettings,
    question: str,
    chunks: Sequence[Hit],
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Answer from `chunks`, which are ordered best first and not empty."""
    if settings.mode == "stub":
        top = chunks[0]
        return f"[stub] Based on {top.file}: {top.text}"
    return _ask_proxy(settings, question, chunks, transport)


def _ask_proxy(
    settings: LLMSettings,
    question: str,
    chunks: Sequence[Hit],
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

    # `from None` below: the original errors can quote header values or the body.
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
            response = client.post(
                f"{settings.url.rstrip('/')}/chat/completions",
                json=body,
                headers=headers,
            )
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
        # ValueError covers values that cannot be encoded, such as a bad key.
        raise LLMError(f"Request failed: {type(error).__name__}") from None
    if not response.is_success:
        raise LLMError(f"Upstream returned status {response.status_code}")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise LLMError("Upstream reply was not a chat completion") from None
    if not isinstance(content, str) or not content:
        raise LLMError("Upstream reply had no text content")
    return content
