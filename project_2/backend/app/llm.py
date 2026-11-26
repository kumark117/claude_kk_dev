"""Turn a question and retrieved chunks into an answer: built-in stub or proxy."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import httpx

from app.retrieval import Hit

TIMEOUT_SECONDS = 30.0
SYSTEM_PROMPT = (
    "Answer the question using only the supplied context. "
    "If the context is not enough to answer it, say so."
)

Mode = Literal["stub", "proxy"]
Answer = Callable[[str, Sequence[Hit]], str]


class LLMError(Exception):
    """The LLM request failed. The message never holds secrets or response text."""


class LLMConfigError(Exception):
    """The LLM settings are incomplete."""


@dataclass(frozen=True)
class LLM:
    mode: Mode
    answer: Answer  # (question, chunks) -> answer text


def build_llm(
    env: Mapping[str, str], transport: httpx.BaseTransport | None = None
) -> LLM:
    """Choose the mode from the environment: proxy if LLM_PROXY_URL is set."""
    url = env.get("LLM_PROXY_URL", "")
    if not url:
        return LLM("stub", _stub_answer)
    model = env.get("LLM_PROXY_MODEL", "")
    if not model:
        raise LLMConfigError("LLM_PROXY_MODEL is required when LLM_PROXY_URL is set")
    endpoint = f"{url.removesuffix('/')}/chat/completions"
    key = env.get("LLM_PROXY_API_KEY", "")
    headers = {"Authorization": f"Bearer {key}"} if key else {}

    def answer(question: str, chunks: Sequence[Hit]) -> str:
        body = {"model": model, "messages": _messages(question, chunks)}
        # Errors are raised "from None": httpx messages can quote headers.
        try:
            with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
                response = client.post(endpoint, headers=headers, json=body)
        except (httpx.HTTPError, httpx.InvalidURL, UnicodeError):
            raise LLMError("could not reach the LLM endpoint") from None
        if not response.is_success:
            raise LLMError(f"the LLM endpoint returned status {response.status_code}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError("the LLM endpoint returned an unusable reply") from None
        if not isinstance(content, str) or not content:
            raise LLMError("the LLM endpoint returned no answer text")
        return content

    return LLM("proxy", answer)


def _stub_answer(question: str, chunks: Sequence[Hit]) -> str:
    top = chunks[0]
    return f"[stub] Based on {top.file}: {top.text}"


def _messages(question: str, chunks: Sequence[Hit]) -> list[dict[str, str]]:
    context = "\n\n".join(f"[{chunk.file}]\n{chunk.text}" for chunk in chunks)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n\n{context}\n\nQuestion: {question}"},
    ]
