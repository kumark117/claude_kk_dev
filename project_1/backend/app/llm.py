"""Turn a question and retrieved chunks into an answer: stub or proxy."""

from collections.abc import Mapping
from dataclasses import dataclass, field

import httpx

from app.retrieval import Chunk

TIMEOUT_SECONDS = 30.0
SYSTEM_PROMPT = (
    "Answer the question using only the supplied context. "
    "If the context is not enough to answer, say so."
)


class ConfigError(Exception):
    """The environment does not describe a usable LLM setup."""


class LLMError(Exception):
    """The LLM request failed. The message never holds the key or reply body."""


@dataclass(frozen=True)
class LLMSettings:
    url: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False)

    @property
    def mode(self) -> str:
        return "proxy" if self.url else "stub"


def load_settings(env: Mapping[str, str]) -> LLMSettings:
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
    """Answer from ``chunks``, which must be non-empty and ordered best first."""
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
    headers = {}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"
    context = "\n\n".join(f"[file: {chunk.file}]\n{chunk.text}" for chunk in chunks)
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

    # Every error is raised "from None": the original exception may quote the
    # key or the reply, and must not appear in a traceback.
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
            response = client.post(
                f"{settings.url.removesuffix('/')}/chat/completions",
                headers=headers,
                json=body,
            )
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
        raise LLMError(f"request failed ({type(error).__name__})") from None

    if not response.is_success:
        raise LLMError(f"unexpected status {response.status_code}")

    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, LookupError, TypeError):
        raise LLMError("unusable response") from None

    if not isinstance(content, str) or not content:
        raise LLMError("response has no content")
    return content
