import json

import httpx
import pytest

from app.llm import LLMConfig, LLMError, generate_answer, load_config
from app.retrieval import Match

CHUNKS = [
    Match("first.md", "Alpha text.", 2.0),
    Match("second.txt", "Beta text.", 1.0),
]
KEY = "dummy-test-key"


def proxy_config(**overrides) -> LLMConfig:
    env = {
        "LLM_PROXY_URL": "http://llm.test/v1",
        "LLM_PROXY_MODEL": "test-model",
        "LLM_PROXY_API_KEY": KEY,
    }
    env.update(overrides)
    return load_config(env)


def recording_transport(requests, response=None):
    def handler(request):
        requests.append(request)
        return response or httpx.Response(
            200, json={"choices": [{"message": {"content": "canned answer"}}]}
        )

    return httpx.MockTransport(handler)


# Mode selection


@pytest.mark.parametrize("env", [{}, {"LLM_PROXY_URL": ""}, {"LLM_PROXY_MODEL": "m"}])
def test_stub_mode_without_a_proxy_url(env):
    assert load_config(env).mode == "stub"


def test_proxy_mode_with_a_proxy_url():
    assert proxy_config().mode == "proxy"


@pytest.mark.parametrize("model", [None, ""])
def test_proxy_mode_requires_a_model(model):
    env = {"LLM_PROXY_URL": "http://llm.test/v1"}
    if model is not None:
        env["LLM_PROXY_MODEL"] = model

    with pytest.raises(ValueError, match="LLM_PROXY_MODEL"):
        load_config(env)


def test_config_repr_hides_the_api_key():
    assert KEY not in repr(proxy_config())


# Stub


def test_stub_returns_exact_text_from_the_top_chunk():
    def handler(request):
        raise AssertionError("the stub must not use the network")

    answer = generate_answer(
        "question?", CHUNKS, load_config({}), transport=httpx.MockTransport(handler)
    )

    assert answer == "[stub] Based on first.md: Alpha text."


# Proxy


@pytest.mark.parametrize("base", ["http://llm.test/v1", "http://llm.test/v1/"])
def test_proxy_posts_to_chat_completions_and_returns_content(base):
    requests = []

    answer = generate_answer(
        "What is alpha?",
        CHUNKS,
        proxy_config(LLM_PROXY_URL=base),
        transport=recording_transport(requests),
    )

    assert answer == "canned answer"
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == "http://llm.test/v1/chat/completions"


def test_proxy_sends_headers_model_and_messages():
    requests = []

    generate_answer(
        "What is alpha?", CHUNKS, proxy_config(), transport=recording_transport(requests)
    )

    request = requests[0]
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["content-type"] == "application/json"
    body = json.loads(request.content)
    assert set(body) == {"model", "messages"}
    assert body["model"] == "test-model"
    system, user = body["messages"]
    assert system["role"] == "system"
    assert "only" in system["content"].lower()
    assert "not enough" in system["content"].lower()
    assert user["role"] == "user"
    content = user["content"]
    positions = [
        content.index(part)
        for part in (
            "first.md",
            "Alpha text.",
            "second.txt",
            "Beta text.",
            "What is alpha?",
        )
    ]
    assert positions == sorted(positions)


@pytest.mark.parametrize("key", [None, ""])
def test_proxy_sends_no_auth_header_without_a_key(key):
    env = {"LLM_PROXY_URL": "http://llm.test/v1", "LLM_PROXY_MODEL": "test-model"}
    if key is not None:
        env["LLM_PROXY_API_KEY"] = key
    requests = []

    generate_answer("q", CHUNKS, load_config(env), transport=recording_transport(requests))

    assert "authorization" not in requests[0].headers


def test_proxy_uses_a_30_second_timeout():
    requests = []

    generate_answer("q", CHUNKS, proxy_config(), transport=recording_transport(requests))

    assert set(requests[0].extensions["timeout"].values()) == {30.0}


# Failures


def fail_with(handler, **config):
    calls = []

    def counting(request):
        calls.append(request)
        return handler(request)

    with pytest.raises(LLMError) as caught:
        generate_answer(
            "q", CHUNKS, proxy_config(**config), transport=httpx.MockTransport(counting)
        )
    return caught.value, calls


@pytest.mark.parametrize(
    "error", [httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout]
)
def test_transport_failures_raise_llm_error_without_retrying(error):
    def handler(request):
        raise error("boom", request=request)

    _, calls = fail_with(handler)

    assert len(calls) == 1


@pytest.mark.parametrize("status", [301, 400, 401, 429, 500, 503])
def test_non_2xx_status_raises_llm_error(status):
    valid = {"choices": [{"message": {"content": "answer"}}]}

    _, calls = fail_with(lambda request: httpx.Response(status, json=valid))

    assert len(calls) == 1


@pytest.mark.parametrize("body", [b"not json", b"", b"\xff\xfe\xfa"])
def test_invalid_json_raises_llm_error(body):
    fail_with(lambda request: httpx.Response(200, content=body))


@pytest.mark.parametrize(
    "body",
    [
        {},
        [],
        "text",
        None,
        {"choices": []},
        {"choices": None},
        {"choices": "abc"},
        {"choices": {}},
        {"choices": [{}]},
        {"choices": [{"message": None}]},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": None}}]},
        {"choices": [{"message": {"content": 42}}]},
        {"choices": [{"message": {"content": ["part"]}}]},
        {"choices": [{"message": {"content": ""}}]},
    ],
)
def test_missing_non_string_or_empty_content_raises_llm_error(body):
    fail_with(lambda request: httpx.Response(200, content=json.dumps(body).encode()))


def test_unusable_url_raises_llm_error():
    with pytest.raises(LLMError):
        generate_answer("q", CHUNKS, proxy_config(LLM_PROXY_URL="http://[bad"))


@pytest.mark.parametrize("key", ["secret\nvalue", "secret-é-value"])
def test_unsendable_key_raises_llm_error_without_revealing_it(key):
    # A closed loopback port: whatever fails, the key must stay hidden.
    with pytest.raises(LLMError) as caught:
        generate_answer(
            "q",
            CHUNKS,
            proxy_config(LLM_PROXY_URL="http://127.0.0.1:9", LLM_PROXY_API_KEY=key),
        )

    assert "secret" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_errors_and_logs_do_not_contain_key_or_response_body(caplog):
    caplog.set_level("DEBUG")

    error, _ = fail_with(lambda request: httpx.Response(500, text="upstream-secret-body"))

    for text in (str(error), repr(error), caplog.text):
        assert KEY not in text
        assert "upstream-secret-body" not in text
