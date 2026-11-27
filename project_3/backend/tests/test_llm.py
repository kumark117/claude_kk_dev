import json

import httpx
import pytest

from app.llm import ConfigError, LLMError, create_llm
from app.retrieval import Hit

HITS = [
    Hit("a.md", 0, "Alpha text.", 2.0),
    Hit("b.txt", 3, "Beta text.", 1.0),
]
KEY = "dummy-test-key"
PROXY_ENV = {
    "LLM_PROXY_URL": "http://llm.test/v1/",
    "LLM_PROXY_MODEL": "test-model",
    "LLM_PROXY_API_KEY": KEY,
}


def reply(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def proxy(handler, **overrides):
    return create_llm({**PROXY_ENV, **overrides}, httpx.MockTransport(handler))


def recording(requests):
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=reply("Proxy answer"))

    return handler


def no_network(request):
    raise AssertionError("no request expected")


def test_stub_returns_exact_text_from_top_chunk():
    llm = create_llm({}, httpx.MockTransport(no_network))

    assert llm.mode == "stub"
    assert llm.answer("question?", HITS) == "[stub] Based on a.md: Alpha text."


def test_empty_url_selects_stub():
    assert create_llm({"LLM_PROXY_URL": ""}).mode == "stub"


def test_non_empty_url_selects_proxy():
    assert proxy(no_network).mode == "proxy"


@pytest.mark.parametrize("model", [None, ""])
def test_proxy_mode_requires_a_model(model):
    env = {"LLM_PROXY_URL": "http://llm.test/v1"}
    if model is not None:
        env["LLM_PROXY_MODEL"] = model

    with pytest.raises(ConfigError, match="LLM_PROXY_MODEL"):
        create_llm(env)


@pytest.mark.parametrize("base", ["http://llm.test/v1", "http://llm.test/v1/"])
def test_proxy_request_url_headers_and_body(base):
    requests = []
    llm = proxy(recording(requests), LLM_PROXY_URL=base)

    assert llm.answer("What is alpha?", HITS) == "Proxy answer"

    [request] = requests
    assert request.method == "POST"
    assert str(request.url) == "http://llm.test/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["content-type"] == "application/json"
    body = json.loads(request.content)
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
        for part in ["a.md", "Alpha text.", "b.txt", "Beta text.", "What is alpha?"]
    ]
    assert positions == sorted(positions)


@pytest.mark.parametrize("key", [None, ""])
def test_no_auth_header_without_a_key(key):
    requests = []
    env = {"LLM_PROXY_URL": "http://llm.test", "LLM_PROXY_MODEL": "m"}
    if key is not None:
        env["LLM_PROXY_API_KEY"] = key

    create_llm(env, httpx.MockTransport(recording(requests))).answer("q", HITS)

    assert "authorization" not in requests[0].headers


def test_request_timeout_is_30_seconds():
    requests = []

    proxy(recording(requests)).answer("q", HITS)

    assert requests[0].extensions["timeout"] == {
        "connect": 30.0,
        "read": 30.0,
        "write": 30.0,
        "pool": 30.0,
    }


def raiser(error_type):
    def handler(request):
        raise error_type("boom UPSTREAM-SECRET", request=request)

    return handler


def responder(status=200, **kwargs):
    return lambda request: httpx.Response(status, **kwargs)


@pytest.mark.parametrize(
    "handler",
    [
        raiser(httpx.ConnectError),
        raiser(httpx.ReadTimeout),
        responder(500, text="UPSTREAM-SECRET"),
        responder(401, json=reply("UPSTREAM-SECRET")),
        responder(302, json=reply("UPSTREAM-SECRET")),
        responder(text="UPSTREAM-SECRET not json"),
        responder(content=b"\xff\xfe\xfa"),
        responder(json=[]),
        responder(json={}),
        responder(json={"choices": []}),
        responder(json={"choices": None}),
        responder(json={"choices": {}}),
        responder(json={"choices": ["text"]}),
        responder(json={"choices": [{}]}),
        responder(json={"choices": [{"message": None}]}),
        responder(json={"choices": [{"message": {}}]}),
        responder(json=reply(None)),
        responder(json=reply(42)),
        responder(json=reply(["part"])),
        responder(json=reply("")),
    ],
)
def test_every_failure_raises_llm_error_without_secrets(handler):
    calls = []

    def counting(request):
        calls.append(request)
        return handler(request)

    with pytest.raises(LLMError) as caught:
        proxy(counting).answer("q", HITS)

    assert len(calls) == 1  # no retries
    assert KEY not in str(caught.value)
    assert "UPSTREAM-SECRET" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ or caught.value.__context__ is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"LLM_PROXY_URL": "not-a-url"},
        {"LLM_PROXY_URL": "http://[broken"},
        {"LLM_PROXY_API_KEY": "kéy-☃"},
    ],
)
def test_unusable_settings_raise_llm_error(overrides):
    # A real transport is not needed: these fail before anything is sent,
    # or are rejected by the mock below.
    def handler(request):
        raise httpx.UnsupportedProtocol("unsupported", request=request)

    with pytest.raises(LLMError):
        proxy(handler, **overrides).answer("q", HITS)


def test_failures_do_not_log_key_or_body(caplog):
    caplog.set_level("DEBUG")

    with pytest.raises(LLMError):
        proxy(responder(500, text="UPSTREAM-SECRET")).answer("q", HITS)

    assert KEY not in caplog.text
    assert "UPSTREAM-SECRET" not in caplog.text
