import json

import httpx
import pytest

from app.llm import ConfigError, LLMError, generate_answer, load_settings
from app.retrieval import Result

CHUNKS = [
    Result("rockets.md", 0, "Rockets burn fuel.", 2.0),
    Result("bread.txt", 3, "Dough needs yeast.", 1.0),
]
PROXY_ENV = {
    "LLM_PROXY_URL": "http://llm.test/v1",
    "LLM_PROXY_API_KEY": "dummy-key",
    "LLM_PROXY_MODEL": "dummy-model",
}


def completion(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def ask_proxy(handler, env=PROXY_ENV):
    return generate_answer(
        "How do rockets fly?",
        CHUNKS,
        load_settings(env),
        transport=httpx.MockTransport(handler),
    )


def capture(env=PROXY_ENV):
    seen = []

    def handler(request):
        seen.append(request)
        return completion("canned")

    answer = ask_proxy(handler, env)
    assert len(seen) == 1
    return answer, seen[0]


# --- mode selection ----------------------------------------------------------


@pytest.mark.parametrize("env", [{}, {"LLM_PROXY_URL": ""}, {"LLM_PROXY_MODEL": "m"}])
def test_stub_mode_without_a_proxy_url(env):
    assert load_settings(env).mode == "stub"


def test_proxy_mode_with_a_proxy_url():
    assert load_settings(PROXY_ENV).mode == "proxy"


@pytest.mark.parametrize("extra", [{}, {"LLM_PROXY_MODEL": ""}])
def test_proxy_mode_without_a_model_fails(extra):
    with pytest.raises(ConfigError, match="LLM_PROXY_MODEL"):
        load_settings({"LLM_PROXY_URL": "http://llm.test/v1", **extra})


def test_settings_repr_does_not_show_the_api_key():
    assert "dummy-key" not in repr(load_settings(PROXY_ENV))


# --- stub ------------------------------------------------------------------


def test_stub_answer_is_exact_and_uses_the_top_chunk():
    def no_network(request):
        raise AssertionError("stub mode must not use the network")

    answer = generate_answer(
        "How do rockets fly?",
        CHUNKS,
        load_settings({}),
        transport=httpx.MockTransport(no_network),
    )

    assert answer == "[stub] Based on rockets.md: Rockets burn fuel."


# --- proxy request ----------------------------------------------------------


def test_proxy_returns_the_message_content():
    answer, _ = capture()

    assert answer == "canned"


@pytest.mark.parametrize(
    "base", ["http://llm.test/v1", "http://llm.test/v1/"]
)
def test_proxy_posts_to_chat_completions_under_the_base(base):
    _, request = capture({**PROXY_ENV, "LLM_PROXY_URL": base})

    assert request.method == "POST"
    assert str(request.url) == "http://llm.test/v1/chat/completions"


def test_proxy_sends_bearer_key_and_json():
    _, request = capture()

    assert request.headers["authorization"] == "Bearer dummy-key"
    assert request.headers["content-type"] == "application/json"


@pytest.mark.parametrize("key", [None, ""])
def test_proxy_sends_no_auth_header_without_a_key(key):
    env = {k: v for k, v in PROXY_ENV.items() if k != "LLM_PROXY_API_KEY"}
    if key is not None:
        env["LLM_PROXY_API_KEY"] = key

    _, request = capture(env)

    assert "authorization" not in request.headers


def test_proxy_body_has_model_and_messages():
    _, request = capture()

    body = json.loads(request.content)
    assert set(body) == {"model", "messages"}
    assert body["model"] == "dummy-model"
    system, user = body["messages"]
    assert system["role"] == "system"
    assert "only" in system["content"].lower()
    assert "context" in system["content"].lower()
    assert "not enough" in system["content"].lower()
    assert user["role"] == "user"
    positions = [
        user["content"].index(part)
        for part in (
            "rockets.md",
            "Rockets burn fuel.",
            "bread.txt",
            "Dough needs yeast.",
            "How do rockets fly?",
        )
    ]
    assert positions == sorted(positions)


def test_proxy_uses_a_30_second_timeout():
    _, request = capture()

    assert request.extensions["timeout"] == {
        "connect": 30.0,
        "read": 30.0,
        "write": 30.0,
        "pool": 30.0,
    }


# --- proxy failures ---------------------------------------------------------


def raising(error_type):
    def handler(request):
        raise error_type("boom dummy-key", request=request)

    return handler


def returning(*args, **kwargs):
    return lambda request: httpx.Response(*args, **kwargs)


FAILURES = {
    "connection error": raising(httpx.ConnectError),
    "timeout": raising(httpx.ReadTimeout),
    "protocol error": raising(httpx.RemoteProtocolError),
    "redirect": returning(302, headers={"location": "http://llm.test/other"}),
    "client error": returning(401, json={"error": "upstream secret"}),
    "server error": returning(500, text="upstream secret"),
    "error status with valid body": returning(
        500, json={"choices": [{"message": {"content": "x"}}]}
    ),
    "invalid json": returning(200, text="upstream secret"),
    "invalid encoding": returning(200, content=b"\xff\xfe\xff"),
    "not an object": returning(200, json=["upstream secret"]),
    "no choices": returning(200, json={}),
    "empty choices": returning(200, json={"choices": []}),
    "choices not a list": returning(200, json={"choices": "upstream secret"}),
    "choices is an object": returning(200, json={"choices": {}}),
    "no message": returning(200, json={"choices": [{}]}),
    "null message": returning(200, json={"choices": [{"message": None}]}),
    "no content": returning(200, json={"choices": [{"message": {}}]}),
    "null content": returning(200, json={"choices": [{"message": {"content": None}}]}),
    "numeric content": returning(200, json={"choices": [{"message": {"content": 5}}]}),
    "empty content": returning(200, json={"choices": [{"message": {"content": ""}}]}),
}


@pytest.mark.parametrize("handler", FAILURES.values(), ids=FAILURES.keys())
def test_every_proxy_failure_raises_llm_error_without_leaking(handler):
    with pytest.raises(LLMError) as caught:
        ask_proxy(handler)

    message = str(caught.value)
    assert "dummy-key" not in message
    assert "upstream secret" not in message
    # The original exception is not chained, so tracebacks cannot leak it.
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_proxy_failure_is_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    with pytest.raises(LLMError):
        ask_proxy(handler)

    assert len(calls) == 1


@pytest.mark.parametrize(
    "env",
    [
        {**PROXY_ENV, "LLM_PROXY_URL": "http://llm.test:notaport/v1"},
        {**PROXY_ENV, "LLM_PROXY_API_KEY": "key-é"},
    ],
    ids=["unusable url", "unencodable key"],
)
def test_unusable_configuration_raises_llm_error(env):
    with pytest.raises(LLMError):
        ask_proxy(lambda request: completion("never"), env)
