"""Answer generation: a built-in stub or an OpenAI-compatible proxy."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import httpx

from app.retrieval import Hit

TIMEOUT_SECONDS = 30.0
SYSTEM_PROMPT = (
    "Answer the question using only the supplied context. "
    "If the context is not enough to answer, say so."
)


class LLMError(Exception):
    """The LLM request failed. Messages never hold the key or upstream body."""


class LLMConfigError(Exception):
    """The LLM environment variables are incomplete."""


@dataclass(frozen=True)
class LLM:
    mode: str  # "stub" or "proxy"
    answer: Callable[[str, Sequence[Hit]], str]


def create_llm(
    env: Mapping[str, str], transport: httpx.BaseTransport | None = None
) -> LLM:
    """Choose the mode from the environment: proxy if LLM_PROXY_URL is set."""
    base = env.get("LLM_PROXY_URL", "")
    if not base:
        return LLM("stub", _stub_answer)

    model = env.get("LLM_PROXY_MODEL", "")
    if not model:
        raise LLMConfigError("LLM_PROXY_MODEL is required when LLM_PROXY_URL is set")
    url = base.rstrip("/") + "/chat/completions"
    key = env.get("LLM_PROXY_API_KEY", "")
    headers = {"Authorization": f"Bearer {key}"} if key else {}

    def proxy_answer(question: str, chunks: Sequence[Hit]) -> str:
        body = {"model": model, "messages": _messages(question, chunks)}
        # "from None" below: the original errors can carry the upstream body.
        try:
            with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
                response = client.post(url, headers=headers, json=body)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            raise LLMError(f"LLM request failed: {type(error).__name__}") from None
        if not response.is_success:
            raise LLMError(f"LLM returned status {response.status_code}") from None
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, LookupError, TypeError):
            raise LLMError("LLM returned an unreadable response") from None
        if not isinstance(content, str) or not content:
            raise LLMError("LLM returned no answer text") from None
        return content

    return LLM("proxy", proxy_answer)


def _stub_answer(question: str, chunks: Sequence[Hit]) -> str:
    top = chunks[0]
    return f"[stub] Based on {top.file}: {top.text}"


def _messages(question: str, chunks: Sequence[Hit]) -> list[dict[str, str]]:
    context = "\n\n".join(f"[{chunk.file}]\n{chunk.text}" for chunk in chunks)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n\n{context}\n\nQuestion: {question}"},
    ]
