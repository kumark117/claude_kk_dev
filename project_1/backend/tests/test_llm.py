import json

import httpx
import pytest

from app.llm import ConfigError, LLMError, generate_answer, load_settings
from app.retrieval import Chunk

CHUNKS = [
    Chunk("cats.md", 0, "Cats purr."),
    Chunk("dogs.txt", 2, "Dogs bark."),
]
KEY = "test-dummy-key"


def proxy_settings(**overrides):
    env = {
        "LLM_PROXY_URL": "http://llm.test/v1",
        "LLM_PROXY_MODEL": "test-model",
        "LLM_PROXY_API_KEY": KEY,
    }
    env.update(overrides)
    return load_settings(env)


def replying(*args, **kwargs):
    return httpx.MockTransport(lambda request: httpx.Response(*args, **kwargs))


def completion(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def ask_proxy(settings):
    """Run one proxy call and return (answer, the single request that was sent)."""
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=completion("Canned answer."))

    answer = generate_answer(
        "Do cats purr?", CHUNKS, settings, transport=httpx.MockTransport(handler)
    )
    (request,) = seen
    return answer, request


# Mode selection


@pytest.mark.parametrize("env", [{}, {"LLM_PROXY_URL": ""}])
def test_stub_mode_without_a_proxy_url(env):
    assert load_settings(env).mode == "stub"


def test_stub_mode_ignores_other_proxy_variables():
    settings = load_settings({"LLM_PROXY_MODEL": "m", "LLM_PROXY_API_KEY": KEY})

    assert settings.mode == "stub"


def test_proxy_mode_with_a_proxy_url():
    assert proxy_settings().mode == "proxy"


@pytest.mark.parametrize("model", [None, ""])
def test_proxy_mode_requires_a_model(model):
    env = {"LLM_PROXY_URL": "http://llm.test/v1"}
    if model is not None:
        env["LLM_PROXY_MODEL"] = model

    with pytest.raises(ConfigError, match="LLM_PROXY_MODEL"):
        load_settings(env)


def test_settings_repr_hides_the_key():
    assert KEY not in repr(proxy_settings())


# Stub


def test_stub_returns_exact_text_from_the_top_chunk_without_network():
    def handler(request):
        raise AssertionError("the stub must not use the network")

    answer = generate_answer(
        "Do cats purr?",
        CHUNKS,
        load_settings({}),
        transport=httpx.MockTransport(handler),
    )

    assert answer == "[stub] Based on cats.md: Cats purr."


# Proxy


@pytest.mark.parametrize(
    "base", ["http://llm.test/v1", "http://llm.test/v1/"]
)
def test_proxy_posts_to_chat_completions(base):
    answer, request = ask_proxy(proxy_settings(LLM_PROXY_URL=base))

    assert answer == "Canned answer."
    assert request.method == "POST"
    assert str(request.url) == "http://llm.test/v1/chat/completions"


def test_proxy_sends_headers_model_and_messages():
    _, request = ask_proxy(proxy_settings())

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
            "cats.md",
            "Cats purr.",
            "dogs.txt",
            "Dogs bark.",
            "Do cats purr?",
        )
    ]
    assert positions == sorted(positions)


@pytest.mark.parametrize("overrides", [{"LLM_PROXY_API_KEY": ""}, None])
def test_proxy_sends_no_auth_header_without_a_key(overrides):
    if overrides is None:
        settings = load_settings(
            {"LLM_PROXY_URL": "http://llm.test", "LLM_PROXY_MODEL": "m"}
        )
    else:
        settings = proxy_settings(**overrides)

    _, request = ask_proxy(settings)

    assert "authorization" not in request.headers


def test_proxy_uses_a_30_second_timeout():
    _, request = ask_proxy(proxy_settings())

    assert set(request.extensions["timeout"].values()) == {30.0}


def assert_llm_error(transport, settings=None):
    with pytest.raises(LLMError) as excinfo:
        generate_answer("q", CHUNKS, settings or proxy_settings(), transport)

    error = excinfo.value
    assert KEY not in str(error)
    assert "upstream-secret" not in str(error)
    # No chained exception, so a traceback cannot expose the request or reply.
    assert error.__cause__ is None
    assert error.__suppress_context__ or error.__context__ is None


@pytest.mark.parametrize(
    "exception", [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError]
)
def test_proxy_transport_failure_raises_llm_error(exception):
    def handler(request):
        raise exception("upstream-secret", request=request)

    assert_llm_error(httpx.MockTransport(handler))


@pytest.mark.parametrize("status", [301, 400, 401, 429, 500, 503])
def test_proxy_non_2xx_raises_llm_error(status):
    assert_llm_error(replying(status, json=completion("upstream-secret")))


@pytest.mark.parametrize("content", [b"upstream-secret", b"", b"\xff\xfe\xfa"])
def test_proxy_invalid_json_raises_llm_error(content):
    assert_llm_error(replying(200, content=content))


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "text",
        {},
        {"choices": None},
        {"choices": []},
        {"choices": "x"},
        {"choices": {}},
        {"choices": [None]},
        {"choices": [{}]},
        {"choices": [{"message": None}]},
        {"choices": [{"message": "x"}]},
        {"choices": [{"message": {}}]},
        completion(None),
        completion(42),
        completion([{"type": "text"}]),
        completion(""),
    ],
)
def test_proxy_unusable_content_raises_llm_error(payload):
    assert_llm_error(replying(200, content=json.dumps(payload).encode()))


@pytest.mark.parametrize(
    "overrides",
    [
        {"LLM_PROXY_URL": "not a url"},
        {"LLM_PROXY_URL": "http://[broken"},
        {"LLM_PROXY_API_KEY": "kéy"},
    ],
)
def test_proxy_unusable_settings_raise_llm_error(overrides):
    # No injected transport: these fail before anything can leave the machine.
    assert_llm_error(None, proxy_settings(**overrides))
