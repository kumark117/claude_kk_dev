import json
import socket
import traceback

import httpx
import pytest

from app.llm import ConfigError, LLMError, create_llm
from app.retrieval import Match

CHUNKS = [
    Match("cats.md", 0, "Cats purr.", 2.0),
    Match("dogs.txt", 3, "Dogs bark.", 1.0),
]
PROXY_ENV = {
    "LLM_PROXY_URL": "http://llm.test/v1",
    "LLM_PROXY_MODEL": "test-model",
}


def reply(content="canned answer"):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def recording_transport(requests, response=None):
    def handler(request):
        requests.append(request)
        return response or reply()

    return httpx.MockTransport(handler)


def run_proxy(env, response=None):
    requests = []
    llm = create_llm(env, transport=recording_transport(requests, response))
    answer = llm.answer("What do cats do?", CHUNKS)
    return answer, requests


# Mode selection


@pytest.mark.parametrize("env", [{}, {"LLM_PROXY_URL": ""}])
def test_stub_mode_without_proxy_url(env):
    assert create_llm(env).mode == "stub"


def test_stub_mode_ignores_other_proxy_variables():
    env = {"LLM_PROXY_API_KEY": "dummy", "LLM_PROXY_MODEL": "m"}

    assert create_llm(env).mode == "stub"


def test_proxy_mode_with_proxy_url():
    assert create_llm(PROXY_ENV).mode == "proxy"


@pytest.mark.parametrize("extra", [{}, {"LLM_PROXY_MODEL": ""}])
def test_proxy_mode_requires_a_model(extra):
    with pytest.raises(ConfigError, match="LLM_PROXY_MODEL"):
        create_llm({"LLM_PROXY_URL": "http://llm.test/v1", **extra})


# Stub


def test_stub_returns_exact_text_from_top_chunk_without_network():
    def handler(request):
        raise AssertionError("stub must not use the network")

    llm = create_llm({}, transport=httpx.MockTransport(handler))

    assert llm.answer("q", CHUNKS) == "[stub] Based on cats.md: Cats purr."


# Proxy request


@pytest.mark.parametrize("base", ["http://llm.test/v1", "http://llm.test/v1/"])
def test_proxy_posts_to_chat_completions(base):
    answer, requests = run_proxy({**PROXY_ENV, "LLM_PROXY_URL": base})

    assert answer == "canned answer"
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == "http://llm.test/v1/chat/completions"
    assert requests[0].headers["content-type"] == "application/json"


def test_proxy_sends_bearer_key():
    _, requests = run_proxy({**PROXY_ENV, "LLM_PROXY_API_KEY": "dummy-key"})

    assert requests[0].headers["authorization"] == "Bearer dummy-key"


@pytest.mark.parametrize("extra", [{}, {"LLM_PROXY_API_KEY": ""}])
def test_proxy_sends_no_auth_header_without_key(extra):
    _, requests = run_proxy({**PROXY_ENV, **extra})

    assert "authorization" not in requests[0].headers


def test_proxy_sends_model_and_messages():
    _, requests = run_proxy(PROXY_ENV)

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
            "cats.md",
            "Cats purr.",
            "dogs.txt",
            "Dogs bark.",
            "What do cats do?",
        )
    ]
    assert positions == sorted(positions)


def test_proxy_uses_30_second_timeout():
    _, requests = run_proxy(PROXY_ENV)

    assert requests[0].extensions["timeout"] == {
        "connect": 30.0,
        "read": 30.0,
        "write": 30.0,
        "pool": 30.0,
    }


# Proxy failures


def failing_llm(handler, env=PROXY_ENV):
    return create_llm(env, transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
def test_transport_errors_raise_llm_error_without_retry(error):
    calls = []

    def handler(request):
        calls.append(request)
        raise error("boom", request=request)

    with pytest.raises(LLMError):
        failing_llm(handler).answer("q", CHUNKS)

    assert len(calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={"choices": [{"message": {"content": "x"}}]}),
        httpx.Response(401, text="denied"),
        httpx.Response(302, headers={"location": "http://llm.test/other"}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, content=b"\xff\xfe\xfa"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json="text"),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": {}}),
        httpx.Response(200, json={"choices": [{}]}),
        httpx.Response(200, json={"choices": [{"message": None}]}),
        httpx.Response(200, json={"choices": [{"message": {}}]}),
        reply(None),
        reply(42),
        reply(["x"]),
        reply(""),
    ],
)
def test_bad_responses_raise_llm_error(response):
    with pytest.raises(LLMError):
        failing_llm(lambda request: response).answer("q", CHUNKS)


@pytest.mark.parametrize(
    "env",
    [
        {**PROXY_ENV, "LLM_PROXY_URL": "not a url"},
        {**PROXY_ENV, "LLM_PROXY_URL": "http://llm.test:notaport/v1"},
        {**PROXY_ENV, "LLM_PROXY_API_KEY": "secret-ké"},
    ],
)
def test_unsendable_request_raises_llm_error(env):
    with pytest.raises(LLMError):
        create_llm(env).answer("q", CHUNKS)


@pytest.fixture
def listening_port():
    with socket.create_server(("127.0.0.1", 0)) as server:
        yield server.getsockname()[1]


@pytest.mark.parametrize(
    "key,response",
    [
        ("secret-key", httpx.Response(500, text="upstream-secret")),
        ("secret-key", httpx.Response(200, text="upstream-secret")),
        ("secret-key", httpx.Response(200, json={"secret": "upstream-secret"})),
        ("secret-ké", reply()),  # cannot be encoded as a header
        ("secret\nkey", None),  # rejected by the HTTP layer, which quotes it
    ],
)
def test_error_traceback_never_shows_key_or_upstream_body(
    key, response, listening_port
):
    env = {**PROXY_ENV, "LLM_PROXY_API_KEY": key}
    if response is None:
        # The real transport only checks headers once it has connected.
        env["LLM_PROXY_URL"] = f"http://127.0.0.1:{listening_port}/v1"
        llm = create_llm(env)
    else:
        llm = failing_llm(lambda request: response, env)

    with pytest.raises(LLMError) as caught:
        llm.answer("q", CHUNKS)

    shown = "".join(traceback.format_exception(caught.value))
    assert "upstream-secret" not in shown
    assert "secret-k" not in shown
    assert "secret\\nkey" not in shown
