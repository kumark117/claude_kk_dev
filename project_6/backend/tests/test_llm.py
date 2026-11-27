import json
import traceback

import httpx
import pytest

from app.llm import (
    ConfigError,
    LLMError,
    LLMSettings,
    generate_answer,
    load_llm_settings,
)
from app.retrieval import Chunk

CHUNKS = [
    Chunk("kettle.md", 0, "The kettle boils water."),
    Chunk("teapot.txt", 2, "The teapot steeps tea."),
]
KEY = "dummy-key-123"


def proxy_settings(**overrides) -> LLMSettings:
    env = {
        "LLM_PROXY_URL": "http://127.0.0.1:9/v1",
        "LLM_PROXY_MODEL": "test-model",
        "LLM_PROXY_API_KEY": KEY,
    }
    env.update(overrides)
    return load_llm_settings(env)


def recording_transport(requests, reply="proxy answer"):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})

    return httpx.MockTransport(handler)


# Mode selection


@pytest.mark.parametrize("env", [{}, {"LLM_PROXY_URL": ""}])
def test_stub_mode_without_a_proxy_url(env):
    assert load_llm_settings(env).mode == "stub"


def test_proxy_mode_with_a_proxy_url():
    assert proxy_settings().mode == "proxy"


@pytest.mark.parametrize("model", [None, ""])
def test_proxy_mode_requires_a_model(model):
    env = {"LLM_PROXY_URL": "http://127.0.0.1:9/v1"}
    if model is not None:
        env["LLM_PROXY_MODEL"] = model

    with pytest.raises(ConfigError, match="LLM_PROXY_MODEL"):
        load_llm_settings(env)


def test_settings_repr_hides_the_key():
    assert KEY not in repr(proxy_settings())


# Stub


def test_stub_returns_the_exact_text_from_the_top_chunk():
    def no_network(request):
        raise AssertionError("stub mode must not use the network")

    answer = generate_answer(
        "q", CHUNKS, load_llm_settings({}), transport=httpx.MockTransport(no_network)
    )

    assert answer == "[stub] Based on kettle.md: The kettle boils water."


# Proxy request


@pytest.mark.parametrize("base", ["http://127.0.0.1:9/v1", "http://127.0.0.1:9/v1/"])
def test_proxy_posts_to_chat_completions_under_the_base(base):
    requests = []

    answer = generate_answer(
        "What boils water?",
        CHUNKS,
        proxy_settings(LLM_PROXY_URL=base),
        transport=recording_transport(requests),
    )

    assert answer == "proxy answer"
    (request,) = requests
    assert request.method == "POST"
    assert str(request.url) == "http://127.0.0.1:9/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["content-type"] == "application/json"


def test_proxy_body_has_model_and_messages():
    requests = []

    generate_answer(
        "What boils water?",
        CHUNKS,
        proxy_settings(),
        transport=recording_transport(requests),
    )

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
            "kettle.md",
            "The kettle boils water.",
            "teapot.txt",
            "The teapot steeps tea.",
            "What boils water?",
        )
    ]
    assert positions == sorted(positions)


@pytest.mark.parametrize("key", [None, ""])
def test_proxy_sends_no_auth_header_without_a_key(key):
    env = {"LLM_PROXY_URL": "http://127.0.0.1:9", "LLM_PROXY_MODEL": "m"}
    if key is not None:
        env["LLM_PROXY_API_KEY"] = key
    requests = []

    generate_answer(
        "q", CHUNKS, load_llm_settings(env), transport=recording_transport(requests)
    )

    assert "authorization" not in requests[0].headers


def test_proxy_uses_a_30_second_timeout():
    requests = []

    generate_answer(
        "q", CHUNKS, proxy_settings(), transport=recording_transport(requests)
    )

    assert set(requests[0].extensions["timeout"].values()) == {30.0}


# Proxy failures


def raising(error_type):
    def handler(request):
        raise error_type(f"boom {KEY}", request=request)

    return handler


def replying(status=200, **kwargs):
    def handler(request):
        return httpx.Response(status, **kwargs)

    return handler


def content(value):
    return {"choices": [{"message": {"content": value}}]}


FAILURES = {
    "connection error": raising(httpx.ConnectError),
    "timeout": raising(httpx.ReadTimeout),
    "status 401": replying(401, text="upstream secret body"),
    "status 500": replying(500, json=content("upstream secret body")),
    "redirect": replying(302, headers={"location": "http://127.0.0.1:9/x"}),
    "invalid json": replying(text="upstream secret body"),
    "not an object": replying(json=["upstream secret body"]),
    "string body": replying(json="upstream secret body"),
    "no choices": replying(json={}),
    "empty choices": replying(json={"choices": []}),
    "null choices": replying(json={"choices": None}),
    "choices object": replying(json={"choices": {}}),
    "no message": replying(json={"choices": [{}]}),
    "no content": replying(json={"choices": [{"message": {}}]}),
    "null content": replying(json=content(None)),
    "number content": replying(json=content(5)),
    "list content": replying(json=content(["a"])),
    "empty content": replying(json=content("")),
}


@pytest.mark.parametrize("handler", FAILURES.values(), ids=FAILURES.keys())
def test_proxy_failures_raise_the_llm_error(handler, caplog):
    calls = []

    def counting(request):
        calls.append(request)
        return handler(request)

    with caplog.at_level("DEBUG"), pytest.raises(LLMError) as caught:
        generate_answer(
            "q", CHUNKS, proxy_settings(), transport=httpx.MockTransport(counting)
        )

    assert len(calls) == 1  # no retries
    printed = "".join(traceback.format_exception(caught.value))
    for text in (printed, caplog.text):
        assert KEY not in text
        assert "upstream secret body" not in text


@pytest.mark.parametrize(
    "overrides",
    [
        {"LLM_PROXY_URL": "not a url"},
        {"LLM_PROXY_URL": "http://127.0.0.1:notaport"},
        {"LLM_PROXY_API_KEY": "bad\nkey"},
        {"LLM_PROXY_API_KEY": "ключ"},
    ],
)
def test_unusable_configuration_raises_the_llm_error(overrides):
    with pytest.raises(LLMError):
        generate_answer("q", CHUNKS, proxy_settings(**overrides))
