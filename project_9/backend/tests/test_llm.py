import json

import httpx
import pytest

from app.llm import LLMError, generate_answer, load_settings
from app.retrieval import Hit

HITS = [
    Hit("birds.md", 0, "Penguins huddle to keep warm.", 2.0),
    Hit("sea.txt", 1, "The tide turns twice a day.", 1.0),
]

PROXY_ENV = {
    "LLM_PROXY_URL": "http://127.0.0.1:9999/v1",
    "LLM_PROXY_API_KEY": "secret-key",
    "LLM_PROXY_MODEL": "test-model",
}


def completion(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def recording_transport(requests, response=None):
    def handler(request):
        requests.append(request)
        return response or httpx.Response(200, json=completion("canned answer"))

    return httpx.MockTransport(handler)


def ask_proxy(env=PROXY_ENV, response=None):
    requests = []
    answer = generate_answer(
        load_settings(env),
        "How do penguins keep warm?",
        HITS,
        transport=recording_transport(requests, response),
    )
    return answer, requests


# Mode selection


@pytest.mark.parametrize("env", [{}, {"LLM_PROXY_URL": ""}])
def test_stub_mode_without_a_proxy_url(env):
    assert load_settings(env).mode == "stub"


def test_stub_mode_ignores_key_and_model():
    env = {"LLM_PROXY_API_KEY": "k", "LLM_PROXY_MODEL": "m"}

    assert load_settings(env).mode == "stub"


def test_proxy_mode_with_a_proxy_url():
    assert load_settings(PROXY_ENV).mode == "proxy"


@pytest.mark.parametrize("model", [None, ""])
def test_proxy_mode_requires_a_model(model):
    env = {"LLM_PROXY_URL": "http://127.0.0.1:9999/v1"}
    if model is not None:
        env["LLM_PROXY_MODEL"] = model

    with pytest.raises(ValueError, match="LLM_PROXY_MODEL"):
        load_settings(env)


def test_settings_repr_hides_the_key():
    assert "secret-key" not in repr(load_settings(PROXY_ENV))


# Stub


def test_stub_returns_exact_text_from_top_chunk_without_network():
    def handler(request):
        raise AssertionError("the stub must not use the network")

    answer = generate_answer(
        load_settings({}), "question", HITS, transport=httpx.MockTransport(handler)
    )

    assert answer == "[stub] Based on birds.md: Penguins huddle to keep warm."


# Proxy


def test_proxy_returns_the_message_content():
    answer, _ = ask_proxy()

    assert answer == "canned answer"


@pytest.mark.parametrize("suffix", ["", "/"])
def test_proxy_posts_to_chat_completions_under_the_base(suffix):
    env = {**PROXY_ENV, "LLM_PROXY_URL": "http://127.0.0.1:9999/api/v1" + suffix}

    _, requests = ask_proxy(env)

    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == "http://127.0.0.1:9999/api/v1/chat/completions"


def test_proxy_sends_bearer_key_and_json_content_type():
    _, requests = ask_proxy()

    assert requests[0].headers["authorization"] == "Bearer secret-key"
    assert requests[0].headers["content-type"] == "application/json"


@pytest.mark.parametrize("key", [None, ""])
def test_proxy_sends_no_auth_header_without_a_key(key):
    env = {k: v for k, v in PROXY_ENV.items() if k != "LLM_PROXY_API_KEY"}
    if key is not None:
        env["LLM_PROXY_API_KEY"] = key

    _, requests = ask_proxy(env)

    assert "authorization" not in requests[0].headers


def test_proxy_sends_model_and_messages():
    _, requests = ask_proxy()
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
        content.index("birds.md"),
        content.index("Penguins huddle to keep warm."),
        content.index("sea.txt"),
        content.index("The tide turns twice a day."),
        content.index("How do penguins keep warm?"),
    ]
    assert positions == sorted(positions)


def test_proxy_uses_a_30_second_timeout():
    _, requests = ask_proxy()

    assert requests[0].extensions["timeout"] == {
        "connect": 30.0, "read": 30.0, "write": 30.0, "pool": 30.0,
    }


# Failures


def assert_llm_error(handler, env=PROXY_ENV):
    calls = []

    def counting(request):
        calls.append(request)
        return handler(request)

    with pytest.raises(LLMError) as caught:
        generate_answer(
            load_settings(env), "question", HITS, transport=httpx.MockTransport(counting)
        )

    assert len(calls) <= 1, "no retries"
    message = str(caught.value)
    assert "secret-key" not in message
    assert "upstream-secret" not in message
    # The original exception must not be chained into tracebacks.
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None or caught.value.__suppress_context__


@pytest.mark.parametrize(
    "error", [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError]
)
def test_connection_errors_and_timeouts_raise_llm_error(error):
    def handler(request):
        raise error("boom upstream-secret", request=request)

    assert_llm_error(handler)


@pytest.mark.parametrize("status", [301, 400, 401, 429, 500, 503])
def test_non_2xx_status_raises_llm_error(status):
    assert_llm_error(
        lambda request: httpx.Response(status, json=completion("upstream-secret"))
    )


@pytest.mark.parametrize("body", [b"", b"upstream-secret not json", b"\xff\xfe\xfa"])
def test_invalid_json_raises_llm_error(body):
    assert_llm_error(lambda request: httpx.Response(200, content=body))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        None,
        "upstream-secret",
        {"choices": []},
        {"choices": {}},
        {"choices": None},
        {"choices": [None]},
        {"choices": [{}]},
        {"choices": [{"message": None}]},
        {"choices": [{"message": "upstream-secret"}]},
        {"choices": [{"message": {}}]},
        completion(None),
        completion(42),
        completion(["upstream-secret"]),
        completion(""),
    ],
)
def test_missing_or_bad_content_raises_llm_error(payload):
    assert_llm_error(
        lambda request: httpx.Response(200, content=json.dumps(payload).encode())
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"LLM_PROXY_URL": "http://127.0.0.1:not-a-port"},
        {"LLM_PROXY_API_KEY": "secret-key-é"},
    ],
)
def test_unusable_configuration_values_raise_llm_error(overrides):
    assert_llm_error(
        lambda request: httpx.Response(200, json=completion("ok")),
        env={**PROXY_ENV, **overrides},
    )
