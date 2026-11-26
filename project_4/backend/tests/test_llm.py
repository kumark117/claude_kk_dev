import json

import httpx
import pytest

from app.llm import ConfigError, LLMError, generate_answer, load_config
from app.retrieval import Result

KEY = "dummy-test-key"
CHUNKS = [
    Result(file="first.md", position=0, text="Alpha text.", score=2.0),
    Result(file="second.txt", position=3, text="Beta text.", score=1.0),
]


def proxy_config(**overrides):
    env = {
        "LLM_PROXY_URL": "http://llm.test/v1",
        "LLM_PROXY_API_KEY": KEY,
        "LLM_PROXY_MODEL": "test-model",
    }
    env.update(overrides)
    return load_config(env)


def reply(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def recording_transport(response=None):
    requests = []

    def handler(request):
        requests.append(request)
        return response or httpx.Response(200, json=reply("Proxy answer"))

    return httpx.MockTransport(handler), requests


# --- mode selection ---


@pytest.mark.parametrize("env", [{}, {"LLM_PROXY_URL": ""}], ids=["unset", "empty"])
def test_stub_mode_without_proxy_url(env):
    assert load_config(env).mode == "stub"


def test_stub_mode_ignores_other_proxy_variables():
    config = load_config({"LLM_PROXY_MODEL": "m", "LLM_PROXY_API_KEY": "k"})

    assert config.mode == "stub"


def test_proxy_mode_with_proxy_url():
    assert proxy_config().mode == "proxy"


@pytest.mark.parametrize("model", [None, ""], ids=["unset", "empty"])
def test_missing_model_fails_in_proxy_mode(model):
    env = {"LLM_PROXY_URL": "http://llm.test/v1"}
    if model is not None:
        env["LLM_PROXY_MODEL"] = model

    with pytest.raises(ConfigError, match="LLM_PROXY_MODEL"):
        load_config(env)


def test_config_repr_does_not_expose_the_key():
    assert KEY not in repr(proxy_config())


# --- stub ---


def test_stub_answer_is_exact_and_uses_the_top_chunk():
    def fail(request):
        raise AssertionError("the stub must not use the network")

    answer = generate_answer(
        "Question?", CHUNKS, load_config({}), transport=httpx.MockTransport(fail)
    )

    assert answer == "[stub] Based on first.md: Alpha text."


# --- proxy request ---


@pytest.mark.parametrize(
    "base", ["http://llm.test/v1", "http://llm.test/v1/"], ids=["plain", "slash"]
)
def test_proxy_posts_to_chat_completions(base):
    transport, requests = recording_transport()

    answer = generate_answer(
        "Question?", CHUNKS, proxy_config(LLM_PROXY_URL=base), transport=transport
    )

    assert answer == "Proxy answer"
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == "http://llm.test/v1/chat/completions"


def test_proxy_sends_headers_model_and_messages():
    transport, requests = recording_transport()

    generate_answer("What is alpha?", CHUNKS, proxy_config(), transport=transport)

    request = requests[0]
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["content-type"] == "application/json"
    body = json.loads(request.content)
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
            "first.md", "Alpha text.", "second.txt", "Beta text.", "What is alpha?",
        )
    ]
    assert positions == sorted(positions)


@pytest.mark.parametrize("key", [None, ""], ids=["unset", "empty"])
def test_no_auth_header_without_a_key(key):
    env = {"LLM_PROXY_URL": "http://llm.test/v1", "LLM_PROXY_MODEL": "m"}
    if key is not None:
        env["LLM_PROXY_API_KEY"] = key
    transport, requests = recording_transport()

    generate_answer("Q", CHUNKS, load_config(env), transport=transport)

    assert "authorization" not in requests[0].headers


def test_proxy_uses_a_30_second_timeout():
    transport, requests = recording_transport()

    generate_answer("Q", CHUNKS, proxy_config(), transport=transport)

    assert set(requests[0].extensions["timeout"].values()) == {30.0}


# --- proxy failures ---


def raising(error):
    def handler(request):
        raise error

    return handler


def returning(*args, **kwargs):
    def handler(request):
        return httpx.Response(*args, **kwargs)

    return handler


FAILURES = {
    "connection-error": raising(httpx.ConnectError("refused")),
    "timeout": raising(httpx.ReadTimeout("slow")),
    "redirect": returning(302, headers={"location": "http://llm.test/other"}),
    "client-error": returning(401, json=reply("ignored")),
    "server-error": returning(500, text="upstream secret body"),
    "invalid-json": returning(200, text="not json"),
    "invalid-utf8": returning(200, content=b"\xff\xfe\xfa"),
    "not-an-object": returning(200, json=["choices"]),
    "no-choices": returning(200, json={}),
    "empty-choices": returning(200, json={"choices": []}),
    "choices-not-a-list": returning(200, json={"choices": {"a": 1}}),
    "no-message": returning(200, json={"choices": [{}]}),
    "message-not-an-object": returning(200, json={"choices": [{"message": "x"}]}),
    "no-content": returning(200, json={"choices": [{"message": {}}]}),
    "null-content": returning(200, json=reply(None)),
    "non-string-content": returning(200, json=reply(["a"])),
    "empty-content": returning(200, json=reply("")),
}


@pytest.mark.parametrize("handler", FAILURES.values(), ids=FAILURES.keys())
def test_each_failure_raises_the_llm_error(handler):
    with pytest.raises(LLMError) as caught:
        generate_answer(
            "Q", CHUNKS, proxy_config(), transport=httpx.MockTransport(handler)
        )

    message = str(caught.value)
    assert KEY not in message
    assert "upstream secret body" not in message
    # No chained error that a traceback would print.
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None or caught.value.__suppress_context__


@pytest.mark.parametrize(
    "overrides",
    [
        {"LLM_PROXY_URL": "not a url"},
        {"LLM_PROXY_URL": "http://llm.test:notaport/v1"},
        {"LLM_PROXY_API_KEY": "clé-non-ascii"},
    ],
    ids=["no-scheme", "bad-port", "unencodable-key"],
)
def test_unusable_settings_raise_the_llm_error(overrides):
    config = proxy_config(**overrides)
    transport = None if "not a url" in overrides.values() else recording_transport()[0]

    with pytest.raises(LLMError):
        generate_answer("Q", CHUNKS, config, transport=transport)


def test_failure_is_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    with pytest.raises(LLMError):
        generate_answer(
            "Q", CHUNKS, proxy_config(), transport=httpx.MockTransport(handler)
        )

    assert len(calls) == 1
