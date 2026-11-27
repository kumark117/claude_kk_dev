import json

import httpx
import pytest

from app.llm import LLMError, generate_answer, load_settings
from app.retrieval import Chunk

CHUNKS = [
    Chunk("bees.md", 0, "Bees make honey."),
    Chunk("bikes.txt", 2, "Bikes have wheels."),
]
KEY = "test-secret-key"
SECRET_BODY = "upstream-secret-body"


def proxy_env(**overrides):
    env = {
        "LLM_PROXY_URL": "http://proxy.test/v1",
        "LLM_PROXY_API_KEY": KEY,
        "LLM_PROXY_MODEL": "test-model",
    }
    return {**env, **overrides}


def ok_reply(content="Proxy answer"):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def call_proxy(handler, **env):
    return generate_answer(
        "Who makes honey?",
        CHUNKS,
        load_settings(proxy_env(**env)),
        transport=httpx.MockTransport(handler),
    )


def record_request(**env):
    seen = []

    def handler(request):
        seen.append(request)
        return ok_reply()

    call_proxy(handler, **env)
    [request] = seen
    return request


# Mode selection


@pytest.mark.parametrize("env", [{}, {"LLM_PROXY_URL": ""}])
def test_stub_mode_without_a_proxy_url(env):
    assert load_settings(env).mode == "stub"


def test_proxy_mode_with_a_proxy_url():
    assert load_settings(proxy_env()).mode == "proxy"


@pytest.mark.parametrize("model", [None, ""])
def test_proxy_mode_without_a_model_fails(model):
    env = proxy_env()
    if model is None:
        del env["LLM_PROXY_MODEL"]
    else:
        env["LLM_PROXY_MODEL"] = model

    with pytest.raises(ValueError, match="LLM_PROXY_MODEL"):
        load_settings(env)


def test_stub_mode_does_not_need_a_model():
    assert load_settings({"LLM_PROXY_API_KEY": KEY}).mode == "stub"


def test_settings_repr_hides_the_key():
    assert KEY not in repr(load_settings(proxy_env()))


# Stub


def test_stub_returns_the_exact_text_from_the_top_chunk_without_network():
    def handler(request):
        raise AssertionError("stub must not use the network")

    answer = generate_answer(
        "Who makes honey?",
        CHUNKS,
        load_settings({}),
        transport=httpx.MockTransport(handler),
    )

    assert answer == "[stub] Based on bees.md: Bees make honey."


# Proxy request


@pytest.mark.parametrize("base", ["http://proxy.test/v1", "http://proxy.test/v1/"])
def test_proxy_posts_to_chat_completions(base):
    request = record_request(LLM_PROXY_URL=base)

    assert request.method == "POST"
    assert str(request.url) == "http://proxy.test/v1/chat/completions"


def test_proxy_sends_the_key_as_a_bearer_token():
    assert record_request().headers["authorization"] == f"Bearer {KEY}"


def test_proxy_sends_no_auth_header_without_a_key():
    request = record_request(LLM_PROXY_API_KEY="")

    assert "authorization" not in request.headers


def test_proxy_sends_model_and_messages():
    request = record_request()
    body = json.loads(request.content)

    assert request.headers["content-type"] == "application/json"
    assert set(body) == {"model", "messages"}
    assert body["model"] == "test-model"

    system, user = body["messages"]
    assert system["role"] == "system"
    assert "only" in system["content"] and "context" in system["content"]
    assert "not enough" in system["content"]

    assert user["role"] == "user"
    content = user["content"]
    positions = [
        content.index(part)
        for part in (
            "bees.md",
            "Bees make honey.",
            "bikes.txt",
            "Bikes have wheels.",
            "Who makes honey?",
        )
    ]
    assert positions == sorted(positions)


def test_proxy_uses_a_30_second_timeout():
    timeout = record_request().extensions["timeout"]

    assert timeout == {"connect": 30, "read": 30, "write": 30, "pool": 30}


def test_proxy_returns_the_message_content():
    assert call_proxy(lambda request: ok_reply("Bees do.")) == "Bees do."


# Proxy failures


def raising(error_type):
    def handler(request):
        raise error_type(f"{SECRET_BODY} {KEY}", request=request)

    return handler


def replying(status=200, **kwargs):
    return lambda request: httpx.Response(status, **kwargs)


def with_content(content):
    return replying(json={"choices": [{"message": {"content": content}}]})


def assert_nothing_chained(error):
    assert error.__cause__ is None
    assert error.__context__ is None or error.__suppress_context__


FAILURES = {
    "connection error": raising(httpx.ConnectError),
    "timeout": raising(httpx.ReadTimeout),
    "server error": replying(500, text=SECRET_BODY),
    "client error": replying(401, text=SECRET_BODY),
    "redirect": replying(302, headers={"location": "http://elsewhere.test"}),
    "error status with a valid body": replying(
        500, json={"choices": [{"message": {"content": "no"}}]}
    ),
    "invalid JSON": replying(text=SECRET_BODY),
    "not an object": replying(json=[SECRET_BODY]),
    "no choices": replying(json={"error": SECRET_BODY}),
    "empty choices": replying(json={"choices": []}),
    "choices not a list": replying(json={"choices": 5}),
    "no message": replying(json={"choices": [{}]}),
    "no content": replying(json={"choices": [{"message": {}}]}),
    "null content": with_content(None),
    "non-string content": with_content([SECRET_BODY]),
    "empty content": with_content(""),
}


@pytest.mark.parametrize("handler", FAILURES.values(), ids=FAILURES.keys())
def test_proxy_failure_raises_llm_error_without_secrets(handler, caplog):
    caplog.set_level("DEBUG")

    with pytest.raises(LLMError) as caught:
        call_proxy(handler)

    for text in (str(caught.value), caplog.text):
        assert KEY not in text
        assert SECRET_BODY not in text
    # No chained exception that a traceback would print with upstream details.
    assert_nothing_chained(caught.value)


@pytest.mark.parametrize(
    "env",
    [{"LLM_PROXY_URL": "http://proxy.test:notaport"}, {"LLM_PROXY_API_KEY": "clé-secrète"}],
    ids=["unusable URL", "key that cannot be a header"],
)
def test_unusable_proxy_settings_raise_llm_error(env):
    with pytest.raises(LLMError) as caught:
        call_proxy(lambda request: ok_reply(), **env)

    assert "secrète" not in str(caught.value)
    assert_nothing_chained(caught.value)
