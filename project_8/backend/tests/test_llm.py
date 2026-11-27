import json

import httpx
import pytest

from app.llm import ConfigError, LLMError, create_llm
from app.retrieval import Result

CHUNKS = [
    Result(file="moon.md", position=0, text="The moon is grey.", score=2.0),
    Result(file="sun.txt", position=1, text="The sun is hot.", score=1.0),
]
BASE_ENV = {"LLM_PROXY_URL": "http://llm.test/v1", "LLM_PROXY_MODEL": "test-model"}
SECRET = "dummy-secret-key"


def reply(content="canned answer"):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def recording_transport(response_factory=reply):
    requests = []

    def handler(request):
        requests.append(request)
        return response_factory()

    return httpx.MockTransport(handler), requests


def proxy(env=BASE_ENV, response_factory=reply):
    transport, requests = recording_transport(response_factory)
    return create_llm(env, transport=transport), requests


# Mode selection


@pytest.mark.parametrize("env", [{}, {"LLM_PROXY_URL": ""}])
def test_stub_mode_without_proxy_url(env):
    assert create_llm(env).mode == "stub"


def test_proxy_mode_with_proxy_url():
    assert create_llm(BASE_ENV).mode == "proxy"


@pytest.mark.parametrize("model", [None, ""])
def test_missing_model_fails_startup(model):
    env = {"LLM_PROXY_URL": "http://llm.test/v1"}
    if model is not None:
        env["LLM_PROXY_MODEL"] = model

    with pytest.raises(ConfigError, match="LLM_PROXY_MODEL"):
        create_llm(env)


# Stub


def test_stub_returns_exact_text_from_top_chunk_without_network():
    transport, requests = recording_transport()
    llm = create_llm({}, transport=transport)

    answer = llm.answer("What colour is the moon?", CHUNKS)

    assert answer == "[stub] Based on moon.md: The moon is grey."
    assert requests == []


# Proxy request


@pytest.mark.parametrize("base", ["http://llm.test/v1", "http://llm.test/v1/"])
def test_proxy_posts_to_chat_completions_under_the_base(base):
    llm, requests = proxy({**BASE_ENV, "LLM_PROXY_URL": base})

    assert llm.answer("q", CHUNKS) == "canned answer"

    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == "http://llm.test/v1/chat/completions"


def test_proxy_sends_bearer_key_and_json():
    llm, requests = proxy({**BASE_ENV, "LLM_PROXY_API_KEY": SECRET})

    llm.answer("q", CHUNKS)

    assert requests[0].headers["authorization"] == f"Bearer {SECRET}"
    assert requests[0].headers["content-type"] == "application/json"


@pytest.mark.parametrize("extra", [{}, {"LLM_PROXY_API_KEY": ""}])
def test_proxy_sends_no_auth_header_without_a_key(extra):
    llm, requests = proxy({**BASE_ENV, **extra})

    llm.answer("q", CHUNKS)

    assert "authorization" not in requests[0].headers


def test_proxy_sends_model_and_messages():
    llm, requests = proxy()

    llm.answer("What colour is the moon?", CHUNKS)

    body = json.loads(requests[0].content)
    assert set(body) == {"model", "messages"}
    assert body["model"] == "test-model"
    system, user = body["messages"]
    assert system["role"] == "system"
    assert "only" in system["content"].lower()
    assert "context" in system["content"].lower()
    assert "not enough" in system["content"].lower()
    assert user["role"] == "user"
    content = user["content"]
    positions = [
        content.index(part)
        for part in (
            "moon.md",
            "The moon is grey.",
            "sun.txt",
            "The sun is hot.",
            "What colour is the moon?",
        )
    ]
    assert positions == sorted(positions)


def test_proxy_uses_a_30_second_timeout():
    llm, requests = proxy()

    llm.answer("q", CHUNKS)

    assert requests[0].extensions["timeout"] == {
        "connect": 30,
        "read": 30,
        "write": 30,
        "pool": 30,
    }


# Proxy failures


def raising(error_type):
    def factory():
        raise error_type("boom")

    return factory


def json_reply(body):
    return lambda: httpx.Response(200, json=body)


FAILURES = {
    "connection error": raising(httpx.ConnectError),
    "timeout": raising(httpx.ReadTimeout),
    "status 301": lambda: httpx.Response(301, headers={"location": "http://x.test"}),
    "status 401": lambda: httpx.Response(401, text="upstream-body"),
    "status 500": lambda: httpx.Response(
        500, json={"choices": [{"message": {"content": "upstream-body"}}]}
    ),
    "invalid json": lambda: httpx.Response(200, text="upstream-body"),
    "invalid encoding": lambda: httpx.Response(200, content=b"\xff\xfe\xfa"),
    "not an object": json_reply(["upstream-body"]),
    "no choices": json_reply({"error": "upstream-body"}),
    "empty choices": json_reply({"choices": []}),
    "choices not a list": json_reply({"choices": "upstream-body"}),
    "no message": json_reply({"choices": [{}]}),
    "message not an object": json_reply({"choices": [{"message": "upstream-body"}]}),
    "no content": json_reply({"choices": [{"message": {}}]}),
    "null content": json_reply({"choices": [{"message": {"content": None}}]}),
    "non-string content": json_reply({"choices": [{"message": {"content": 5}}]}),
    "empty content": json_reply({"choices": [{"message": {"content": ""}}]}),
}


@pytest.mark.parametrize("factory", FAILURES.values(), ids=FAILURES.keys())
def test_proxy_failures_raise_llm_error_without_secrets(factory, caplog):
    llm, requests = proxy({**BASE_ENV, "LLM_PROXY_API_KEY": SECRET}, factory)

    with caplog.at_level("DEBUG"), pytest.raises(LLMError) as caught:
        llm.answer("q", CHUNKS)

    assert len(requests) == 1  # no retries
    for text in (str(caught.value), caplog.text):
        assert SECRET not in text
        assert "upstream-body" not in text


def test_unusable_proxy_url_raises_llm_error():
    llm = create_llm({**BASE_ENV, "LLM_PROXY_URL": "http://llm.test:notaport"})

    with pytest.raises(LLMError):
        llm.answer("q", CHUNKS)
