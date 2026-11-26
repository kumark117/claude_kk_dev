import json

import httpx
import pytest

from app.llm import ConfigError, LLMError, build_llm
from app.retrieval import Chunk

CHUNKS = [
    Chunk(file="cats.md", position=0, text="Cats purr."),
    Chunk(file="dogs.md", position=2, text="Dogs bark."),
]
KEY = "dummy-test-key"
PROXY_ENV = {
    "LLM_PROXY_URL": "http://127.0.0.1:9/v1",
    "LLM_PROXY_MODEL": "test-model",
    "LLM_PROXY_API_KEY": KEY,
}


def completion(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def recording_transport(requests, response=None):
    def handler(request):
        requests.append(request)
        return response or httpx.Response(200, json=completion("canned"))

    return httpx.MockTransport(handler)


# Mode selection


@pytest.mark.parametrize("env", [{}, {"LLM_PROXY_URL": ""}, {"LLM_PROXY_MODEL": "m"}])
def test_stub_mode_when_proxy_url_is_empty(env):
    assert build_llm(env).mode == "stub"


def test_proxy_mode_when_proxy_url_is_set():
    assert build_llm(PROXY_ENV).mode == "proxy"


@pytest.mark.parametrize("model", [None, ""])
def test_proxy_mode_requires_a_model(model):
    env = {"LLM_PROXY_URL": "http://127.0.0.1:9"}
    if model is not None:
        env["LLM_PROXY_MODEL"] = model

    with pytest.raises(ConfigError, match="LLM_PROXY_MODEL"):
        build_llm(env)


# Stub


def test_stub_returns_exact_text_from_top_chunk():
    def refuse(request):
        raise AssertionError("the stub must not use the network")

    llm = build_llm({}, transport=httpx.MockTransport(refuse))

    assert llm.answer("Do cats purr?", CHUNKS) == "[stub] Based on cats.md: Cats purr."


# Proxy


def test_proxy_request_url_headers_model_and_messages():
    requests = []
    llm = build_llm(PROXY_ENV, transport=recording_transport(requests))

    assert llm.answer("Do cats purr?", CHUNKS) == "canned"

    [request] = requests
    assert request.method == "POST"
    assert str(request.url) == "http://127.0.0.1:9/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert request.headers["Content-Type"] == "application/json"
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
        for part in ["cats.md", "Cats purr.", "dogs.md", "Dogs bark.", "Do cats purr?"]
    ]
    assert positions == sorted(positions)


def test_proxy_strips_trailing_slash():
    requests = []
    env = {**PROXY_ENV, "LLM_PROXY_URL": "http://127.0.0.1:9/api/v1/"}
    build_llm(env, transport=recording_transport(requests)).answer("q", CHUNKS)

    assert str(requests[0].url) == "http://127.0.0.1:9/api/v1/chat/completions"


@pytest.mark.parametrize("key", [None, ""])
def test_proxy_sends_no_auth_header_without_a_key(key):
    requests = []
    env = {k: v for k, v in PROXY_ENV.items() if k != "LLM_PROXY_API_KEY"}
    if key is not None:
        env["LLM_PROXY_API_KEY"] = key
    build_llm(env, transport=recording_transport(requests)).answer("q", CHUNKS)

    assert "Authorization" not in requests[0].headers


def test_proxy_uses_a_30_second_timeout():
    requests = []
    build_llm(PROXY_ENV, transport=recording_transport(requests)).answer("q", CHUNKS)

    assert set(requests[0].extensions["timeout"].values()) == {30.0}


def fails_with(exception_type):
    def handler(request):
        raise exception_type(f"boom {KEY}", request=request)

    return handler


def replies(*args, **kwargs):
    return lambda request: httpx.Response(*args, **kwargs)


FAILURES = {
    "connection error": fails_with(httpx.ConnectError),
    "timeout": fails_with(httpx.ReadTimeout),
    "status 500": replies(500, json=completion("secret upstream body")),
    "status 401": replies(401, text="secret upstream body"),
    "redirect": replies(302, headers={"Location": "http://127.0.0.1:9/x"}),
    "invalid json": replies(200, text="secret upstream body"),
    "invalid utf-8": replies(200, content=b"\xff\xfe\xfa"),
    "not an object": replies(200, json=["secret upstream body"]),
    "no choices": replies(200, json={}),
    "empty choices": replies(200, json={"choices": []}),
    "choices not a list": replies(200, json={"choices": "text"}),
    "no message": replies(200, json={"choices": [{}]}),
    "no content": replies(200, json={"choices": [{"message": {}}]}),
    "null content": replies(200, json=completion(None)),
    "non-string content": replies(200, json=completion(42)),
    "empty content": replies(200, json=completion("")),
}


@pytest.mark.parametrize("handler", FAILURES.values(), ids=FAILURES.keys())
def test_proxy_failures_raise_llm_error_without_leaking(handler, caplog):
    caplog.set_level("DEBUG")
    llm = build_llm(PROXY_ENV, transport=httpx.MockTransport(handler))

    with pytest.raises(LLMError) as caught:
        llm.answer("q", CHUNKS)

    error = caught.value
    assert KEY not in str(error)
    assert "secret upstream body" not in str(error)
    # No chained exception that would carry either into a traceback.
    assert error.__cause__ is None
    assert error.__suppress_context__ or error.__context__ is None
    assert KEY not in caplog.text
    assert "secret upstream body" not in caplog.text


def test_unusable_proxy_url_raises_llm_error():
    env = {**PROXY_ENV, "LLM_PROXY_URL": "http://127.0.0.1:notaport"}

    with pytest.raises(LLMError):
        build_llm(env).answer("q", CHUNKS)
