import json

import httpx
import pytest

from app.llm import LLMConfigError, LLMError, create_llm
from app.retrieval import Hit

HITS = [
    Hit(file="space.md", text="Rockets reach orbit.", score=2.0),
    Hit(file="tea.md", text="Tea is steeped.", score=1.0),
]
PROXY_ENV = {
    "LLM_PROXY_URL": "http://127.0.0.1:9/v1",
    "LLM_PROXY_MODEL": "test-model",
    "LLM_PROXY_API_KEY": "dummy-key",
}


def completion(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def recording_transport(requests, response=None):
    def handler(request):
        requests.append(request)
        return response or httpx.Response(200, json=completion("proxy says hi"))

    return httpx.MockTransport(handler)


def forbidden_transport():
    def handler(request):
        raise AssertionError("no request expected")

    return httpx.MockTransport(handler)


# Mode selection


@pytest.mark.parametrize("env", [{}, {"LLM_PROXY_URL": ""}])
def test_stub_mode_when_proxy_url_is_unset_or_empty(env):
    assert create_llm(env).mode == "stub"


def test_proxy_mode_when_proxy_url_is_set():
    assert create_llm(PROXY_ENV).mode == "proxy"


@pytest.mark.parametrize("model", [None, ""])
def test_missing_model_fails_startup_in_proxy_mode(model):
    env = {"LLM_PROXY_URL": "http://127.0.0.1:9/v1"}
    if model is not None:
        env["LLM_PROXY_MODEL"] = model

    with pytest.raises(LLMConfigError, match="LLM_PROXY_MODEL"):
        create_llm(env)


# Stub


def test_stub_returns_exact_text_from_the_top_chunk_without_network():
    llm = create_llm({}, transport=forbidden_transport())

    answer = llm.answer("How?", HITS)

    assert answer == "[stub] Based on space.md: Rockets reach orbit."


# Proxy


@pytest.mark.parametrize(
    "base", ["http://127.0.0.1:9/v1", "http://127.0.0.1:9/v1/"]
)
def test_proxy_posts_to_chat_completions_under_the_base(base):
    requests = []
    llm = create_llm(
        {**PROXY_ENV, "LLM_PROXY_URL": base},
        transport=recording_transport(requests),
    )

    assert llm.answer("How?", HITS) == "proxy says hi"

    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == "http://127.0.0.1:9/v1/chat/completions"


def test_proxy_sends_headers_model_and_messages():
    requests = []
    llm = create_llm(PROXY_ENV, transport=recording_transport(requests))

    llm.answer("How do rockets fly?", HITS)

    request = requests[0]
    assert request.headers["authorization"] == "Bearer dummy-key"
    assert request.headers["content-type"] == "application/json"
    body = json.loads(request.content)
    assert set(body) == {"model", "messages"}
    assert body["model"] == "test-model"
    system, user = body["messages"]
    assert system["role"] == "system"
    assert "only" in system["content"].lower()
    assert "context" in system["content"].lower()
    assert "enough" in system["content"].lower()
    assert user["role"] == "user"
    content = user["content"]
    positions = [
        content.index(part)
        for part in (
            "space.md",
            "Rockets reach orbit.",
            "tea.md",
            "Tea is steeped.",
            "How do rockets fly?",
        )
    ]
    assert positions == sorted(positions)


def test_proxy_uses_a_30_second_timeout():
    requests = []
    llm = create_llm(PROXY_ENV, transport=recording_transport(requests))

    llm.answer("How?", HITS)

    assert requests[0].extensions["timeout"] == {
        "connect": 30.0,
        "read": 30.0,
        "write": 30.0,
        "pool": 30.0,
    }


@pytest.mark.parametrize("key", [None, ""])
def test_no_authorization_header_without_a_key(key):
    env = {k: v for k, v in PROXY_ENV.items() if k != "LLM_PROXY_API_KEY"}
    if key is not None:
        env["LLM_PROXY_API_KEY"] = key
    requests = []
    llm = create_llm(env, transport=recording_transport(requests))

    llm.answer("How?", HITS)

    assert "authorization" not in requests[0].headers


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json=completion("ignored")),
        httpx.Response(401, text="upstream-secret"),
        httpx.Response(302, headers={"location": "http://127.0.0.1:9/other"}),
        httpx.Response(200, text="upstream-secret is not JSON"),
        httpx.Response(200, content=b"\xff\xfe\xfd"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json="text"),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": {}}),
        httpx.Response(200, json={"choices": [None]}),
        httpx.Response(200, json={"choices": [{}]}),
        httpx.Response(200, json={"choices": [{"message": None}]}),
        httpx.Response(200, json={"choices": [{"message": {}}]}),
        httpx.Response(200, json=completion(None)),
        httpx.Response(200, json=completion(42)),
        httpx.Response(200, json=completion(["a"])),
        httpx.Response(200, json=completion("")),
    ],
)
def test_bad_responses_raise_the_llm_error(response):
    llm = create_llm(PROXY_ENV, transport=recording_transport([], response))

    with pytest.raises(LLMError) as caught:
        llm.answer("How?", HITS)

    assert "dummy-key" not in str(caught.value)
    assert "upstream-secret" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize(
    "error", [httpx.ConnectError, httpx.ReadTimeout, httpx.ReadError]
)
def test_transport_failures_raise_the_llm_error_without_retrying(error):
    attempts = []

    def handler(request):
        attempts.append(request)
        raise error("boom", request=request)

    llm = create_llm(PROXY_ENV, transport=httpx.MockTransport(handler))

    with pytest.raises(LLMError):
        llm.answer("How?", HITS)
    assert len(attempts) == 1


def test_unusable_proxy_url_raises_the_llm_error():
    llm = create_llm({**PROXY_ENV, "LLM_PROXY_URL": "http://127.0.0.1:notaport"})

    with pytest.raises(LLMError):
        llm.answer("How?", HITS)
