"""Answer a question from retrieved chunks: built-in stub or OpenAI-style proxy."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import httpx

from app.retrieval import Hit

TIMEOUT_SECONDS = 30.0
SYSTEM_PROMPT = (
    "Answer the question using only the supplied context. "
    "If the context is not enough to answer, say so."
)


class ConfigError(Exception):
    """The environment settings are incomplete."""


class LLMError(Exception):
    """The LLM request failed. Messages never include keys or response bodies."""


@dataclass(frozen=True)
class LLM:
    mode: Literal["stub", "proxy"]
    # Takes the question and the retrieved chunks (best first, not empty).
    answer: Callable[[str, Sequence[Hit]], str]


def stub_answer(question: str, chunks: Sequence[Hit]) -> str:
    top = chunks[0]
    return f"[stub] Based on {top.file}: {top.text}"


def create_llm(
    env: Mapping[str, str], transport: httpx.BaseTransport | None = None
) -> LLM:
    """Pick proxy mode if LLM_PROXY_URL is non-empty, otherwise the stub."""
    base = env.get("LLM_PROXY_URL", "")
    if not base:
        return LLM("stub", stub_answer)
    model = env.get("LLM_PROXY_MODEL", "")
    if not model:
        raise ConfigError("LLM_PROXY_MODEL is required when LLM_PROXY_URL is set")
    key = env.get("LLM_PROXY_API_KEY", "")
    url = f"{base.rstrip('/')}/chat/completions"

    def proxy_answer(question: str, chunks: Sequence[Hit]) -> str:
        context = "\n\n".join(f"[{c.file}]\n{c.text}" for c in chunks)
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Context:\n\n{context}\n\nQuestion: {question}",
                },
            ],
        }
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        # "from None" below: the original errors can quote the key or the body.
        try:
            with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
                response = client.post(url, json=body, headers=headers)
            if not response.is_success:
                raise LLMError(f"LLM returned status {response.status_code}")
            content = response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, httpx.InvalidURL) as error:
            raise LLMError(f"LLM request failed: {type(error).__name__}") from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError("LLM request or response was invalid") from None
        if not isinstance(content, str) or not content:
            raise LLMError("LLM response had no text content")
        return content

    return LLM("proxy", proxy_answer)
