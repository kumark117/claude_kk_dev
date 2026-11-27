"""Answer a question from retrieved chunks, with a stub or an LLM proxy."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import httpx

from app.retrieval import Result

TIMEOUT_SECONDS = 30
SYSTEM_PROMPT = (
    "Answer the question using only the supplied context. "
    "If the context is not enough to answer, say so."
)


class ConfigError(Exception):
    """The LLM settings are incomplete."""


class LLMError(Exception):
    """The LLM request failed. Messages never hold keys or response bodies."""


@dataclass(frozen=True)
class LLM:
    mode: str  # "stub" or "proxy"
    answer: Callable[[str, list[Result]], str]


def _stub_answer(question: str, chunks: list[Result]) -> str:
    top = chunks[0]
    return f"[stub] Based on {top.file}: {top.text}"


def _user_prompt(question: str, chunks: list[Result]) -> str:
    context = "\n\n".join(f"[{chunk.file}]\n{chunk.text}" for chunk in chunks)
    return f"Context:\n\n{context}\n\nQuestion: {question}"


def create_llm(
    env: Mapping[str, str], transport: httpx.BaseTransport | None = None
) -> LLM:
    base = env.get("LLM_PROXY_URL", "")
    if not base:
        return LLM("stub", _stub_answer)

    model = env.get("LLM_PROXY_MODEL", "")
    if not model:
        raise ConfigError("LLM_PROXY_MODEL is required when LLM_PROXY_URL is set")
    key = env.get("LLM_PROXY_API_KEY", "")
    url = f"{base.removesuffix('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {key}"} if key else {}

    def proxy_answer(question: str, chunks: list[Result]) -> str:
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _user_prompt(question, chunks)},
            ],
        }
        # "from None" keeps request and response details out of tracebacks.
        try:
            with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
                response = client.post(url, json=body, headers=headers)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            raise LLMError(f"request failed: {type(error).__name__}") from None
        if not response.is_success:
            raise LLMError(f"status {response.status_code}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, LookupError, TypeError):
            raise LLMError("unreadable response") from None
        if not isinstance(content, str) or not content:
            raise LLMError("missing answer text")
        return content

    return LLM("proxy", proxy_answer)
