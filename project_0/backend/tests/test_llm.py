import json

import httpx
import pytest

from app.llm import ConfigError, LLMError, create_llm
from app.retrieval import Chunk

CHUNKS = [
    Chunk(file="lava.md", position=0, text="Lava is molten rock."),
    Chunk(file="ash.txt", position=2, text="Ash clouds travel far."),
]
KEY = "dummy-test-key"
PROXY_ENV = {
    "LLM_PROXY_URL": "http://127.0.0.1:9/v1",
    "LLM_PROXY_API_KEY": KEY,
    "LLM_PROXY_MODEL": "test-model",
}


def completion(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def recording_transport(response=None):
    requests = []

    def handler(request):
        requests.append(request)
        return response or httpx.Response(200, json=completion("canned"))

    return httpx.MockTransport(handler), requests


# Mode selection


@pytest.mark.parametrize("env", [{}, {"LLM_PROXY_URL": ""}])
def test_stub_mode_without_a_proxy_url(env):
    assert create_llm(env).mode == "stub"


def test_proxy_mode_with_a_proxy_url():
    assert create_llm(PROXY_ENV).mode == "proxy"


@pytest.mark.parametrize("model", [None, ""])
def test_missing_model_fails_startup(model):
    env = {"LLM_PROXY_URL": "http://127.0.0.1:9/v1"}
    if model is not None:
        env["LLM_PROXY_MODEL"] = model

    with pytest.raises(ConfigError, match="LLM_PROXY_MODEL"):
        create_llm(env)


# Stub


def test_stub_text_is_exact_and_uses_the_top_chunk():
    transport, requests = recording_transport()

    answer = create_llm({}, transport=transport).answer("What is lava?", CHUNKS)

    assert answer == "[stub] Based on lava.md: Lava is molten rock."
    assert requests == []


# Proxy


@pytest.mark.parametrize(
    "base", ["http://127.0.0.1:9/api/v1", "http://127.0.0.1:9/api/v1/"]
)
def test_proxy_request(base):
    transport, requests = recording_transport()
    llm = create_llm({**PROXY_ENV, "LLM_PROXY_URL": base}, transport=transport)

    answer = llm.answer("What is lava?", CHUNKS)

    assert answer == "canned"
    [request] = requests
    assert request.method == "POST"
    assert str(request.url) == "http://127.0.0.1:9/api/v1/chat/completions"
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
    for item in CHUNKS:
        assert content.index(item.file) < content.index(item.text)
    assert content.index(CHUNKS[-1].text) < content.index("What is lava?")


def test_proxy_uses_a_30_second_timeout():
    transport, requests = recording_transport()

    create_llm(PROXY_ENV, transport=transport).answer("q", CHUNKS)

    assert set(requests[0].extensions["timeout"].values()) == {30.0}


@pytest.mark.parametrize("key", [None, ""])
def test_no_auth_header_without_a_key(key):
    env = dict(PROXY_ENV)
    del env["LLM_PROXY_API_KEY"]
    if key is not None:
        env["LLM_PROXY_API_KEY"] = key
    transport, requests = recording_transport()

    create_llm(env, transport=transport).answer("q", CHUNKS)

    assert "authorization" not in requests[0].headers


def raising(error_type):
    def handler(request):
        raise error_type("boom", request=request)

    return handler


def replying(status=200, **kwargs):
    def handler(request):
        return httpx.Response(status, **kwargs)

    return handler


@pytest.mark.parametrize(
    "handler",
    [
        pytest.param(raising(httpx.ConnectError), id="connection-error"),
        pytest.param(raising(httpx.ReadTimeout), id="timeout"),
        pytest.param(replying(500, json=completion("x")), id="500"),
        pytest.param(replying(401, text="secret upstream body"), id="401"),
        pytest.param(replying(302, headers={"location": "/x"}), id="redirect"),
        pytest.param(replying(content=b"not json"), id="invalid-json"),
        pytest.param(replying(content=b"\xff\xfe\xff"), id="undecodable"),
        pytest.param(replying(json=[]), id="not-an-object"),
        pytest.param(replying(json={}), id="no-choices"),
        pytest.param(replying(json={"choices": []}), id="empty-choices"),
        pytest.param(replying(json={"choices": "text"}), id="choices-string"),
        pytest.param(replying(json={"choices": [{}]}), id="no-message"),
        pytest.param(replying(json={"choices": [{"message": {}}]}), id="no-content"),
        pytest.param(replying(json=completion(None)), id="null-content"),
        pytest.param(replying(json=completion(7)), id="non-string-content"),
        pytest.param(replying(json=completion("")), id="empty-content"),
    ],
)
def test_each_failure_raises_the_llm_error(handler):
    llm = create_llm(PROXY_ENV, transport=httpx.MockTransport(handler))

    with pytest.raises(LLMError) as caught:
        llm.answer("q", CHUNKS)

    message = str(caught.value)
    assert KEY not in message
    assert "secret upstream body" not in message


@pytest.mark.parametrize("url", ["not a url", "ftp://127.0.0.1/v1", "http://[bad"])
def test_unusable_proxy_url_raises_the_llm_error(url):
    llm = create_llm({**PROXY_ENV, "LLM_PROXY_URL": url})

    with pytest.raises(LLMError):
        llm.answer("q", CHUNKS)


def test_unencodable_key_raises_the_llm_error():
    transport, _ = recording_transport()
    env = {**PROXY_ENV, "LLM_PROXY_API_KEY": "kéy"}

    with pytest.raises(LLMError):
        create_llm(env, transport=transport).answer("q", CHUNKS)
