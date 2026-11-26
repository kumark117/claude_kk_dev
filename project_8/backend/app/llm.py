"""Answer generation: a built-in stub or an OpenAI-compatible proxy."""

from collections.abc import Mapping
from dataclasses import dataclass, field

import httpx

from app.retrieval import Result

TIMEOUT_SECONDS = 30.0
SYSTEM_PROMPT = (
    "Answer the question using only the supplied context. "
    "If the context is not enough to answer, say so."
)


class LLMError(Exception):
    """The proxy request failed. Messages never include the key or response body."""


class LLMConfigError(Exception):
    """The LLM settings are incomplete."""


@dataclass(frozen=True)
class LLMConfig:
    url: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False)

    @property
    def mode(self) -> str:
        return "proxy" if self.url else "stub"


def config_from_env(env: Mapping[str, str]) -> LLMConfig:
    config = LLMConfig(
        url=env.get("LLM_PROXY_URL", ""),
        model=env.get("LLM_PROXY_MODEL", ""),
        api_key=env.get("LLM_PROXY_API_KEY", ""),
    )
    if config.mode == "proxy" and not config.model:
        raise LLMConfigError("LLM_PROXY_MODEL is required when LLM_PROXY_URL is set")
    return config


def generate_answer(
    question: str,
    chunks: list[Result],
    config: LLMConfig,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Return the answer for a question, given at least one retrieved chunk."""
    if config.mode == "stub":
        top = chunks[0]
        return f"[stub] Based on {top.file}: {top.text}"
    return _ask_proxy(question, chunks, config, transport)


def _ask_proxy(question, chunks, config, transport) -> str:
    context = "\n\n".join(f"[{chunk.file}]\n{chunk.text}" for chunk in chunks)
    body = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Context:\n\n{context}\n\nQuestion: {question}"},
        ],
    }
    headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
    url = f"{config.url.rstrip('/')}/chat/completions"

    # "from None" keeps the key and upstream details out of any traceback.
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
            response = client.post(url, json=body, headers=headers)
        if not response.is_success:
            raise LLMError(f"LLM proxy returned status {response.status_code}")
        content = response.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, httpx.InvalidURL) as error:
        raise LLMError(f"LLM proxy request failed: {type(error).__name__}") from None
    except (ValueError, LookupError, TypeError):
        raise LLMError("LLM proxy returned an unusable response") from None
    if not isinstance(content, str) or not content:
        raise LLMError("LLM proxy returned no answer text")
    return content
