import json

import httpx
import pytest

from app.llm import ConfigError, LLMError, create_llm
from app.retrieval import ScoredChunk

CHUNKS = [
    ScoredChunk(file="first.md", position=0, text="Alpha text.", score=2.0),
    ScoredChunk(file="second.txt", position=3, text="Beta text.", score=1.0),
]
PROXY_ENV = {
    "LLM_PROXY_URL": "http://127.0.0.1:9/v1/",
    "LLM_PROXY_MODEL": "test-model",
    "LLM_PROXY_API_KEY": "secret-key",
}


def reply(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def proxy_with(handler, env=PROXY_ENV):
    return create_llm(env, transport=httpx.MockTransport(handler))


def test_stub_returns_exact_text_from_top_chunk():
    llm = create_llm({})

    assert llm.mode == "stub"
    assert llm.answer("q?", CHUNKS) == "[stub] Based on first.md: Alpha text."


@pytest.mark.parametrize(
    "env", [{}, {"LLM_PROXY_URL": ""}, {"LLM_PROXY_MODEL": "m", "LLM_PROXY_API_KEY": "k"}]
)
def test_stub_mode_without_a_proxy_url(env):
    assert create_llm(env).mode == "stub"


def test_proxy_mode_with_a_url():
    assert create_llm(PROXY_ENV).mode == "proxy"


@pytest.mark.parametrize("model", [None, ""])
def test_missing_model_fails_startup(model):
    env = {"LLM_PROXY_URL": "http://127.0.0.1:9/v1"}
    if model is not None:
        env["LLM_PROXY_MODEL"] = model

    with pytest.raises(ConfigError, match="LLM_PROXY_MODEL"):
        create_llm(env)


def test_proxy_sends_the_expected_request_and_returns_content():
    seen = []

    def handler(request):
        seen.append(request)
        return reply("Proxy answer.")

    answer = proxy_with(handler).answer("What is alpha?", CHUNKS)

    assert answer == "Proxy answer."
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == "http://127.0.0.1:9/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer secret-key"
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
    order = [
        content.index(part)
        for part in ("first.md", "Alpha text.", "second.txt", "Beta text.", "What is alpha?")
    ]
    assert order == sorted(order)


def test_proxy_uses_a_30_second_timeout():
    seen = []

    def handler(request):
        seen.append(request.extensions["timeout"])
        return reply("ok")

    proxy_with(handler).answer("q", CHUNKS)

    assert seen == [{"connect": 30.0, "read": 30.0, "write": 30.0, "pool": 30.0}]


@pytest.mark.parametrize("key", [None, ""])
def test_no_auth_header_without_a_key(key):
    env = {k: v for k, v in PROXY_ENV.items() if k != "LLM_PROXY_API_KEY"}
    if key is not None:
        env["LLM_PROXY_API_KEY"] = key
    seen = []

    def handler(request):
        seen.append(request)
        return reply("ok")

    proxy_with(handler, env).answer("q", CHUNKS)

    assert "authorization" not in seen[0].headers


def raise_connect_error(request):
    raise httpx.ConnectError("refused", request=request)


def raise_timeout(request):
    raise httpx.ReadTimeout("slow", request=request)


def respond(*args, **kwargs):
    return lambda request: httpx.Response(*args, **kwargs)


@pytest.mark.parametrize(
    "handler",
    [
        raise_connect_error,
        raise_timeout,
        respond(500, json={"choices": [{"message": {"content": "no"}}]}),
        respond(401, text="upstream-secret-body"),
        respond(302, headers={"location": "http://127.0.0.1:9/other"}),
        respond(200, text="not json"),
        respond(200, content=b"\xff\xfe\xfa"),
        respond(200, json=[]),
        respond(200, json={}),
        respond(200, json={"choices": []}),
        respond(200, json={"choices": "text"}),
        respond(200, json={"choices": [{}]}),
        respond(200, json={"choices": [{"message": None}]}),
        respond(200, json={"choices": [{"message": {}}]}),
        respond(200, json={"choices": [{"message": {"content": None}}]}),
        respond(200, json={"choices": [{"message": {"content": 5}}]}),
        respond(200, json={"choices": [{"message": {"content": ""}}]}),
    ],
)
def test_every_failure_raises_the_llm_error_without_secrets(handler):
    with pytest.raises(LLMError) as caught:
        proxy_with(handler).answer("q", CHUNKS)

    assert "secret-key" not in str(caught.value)
    assert "upstream-secret-body" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


@pytest.mark.parametrize(
    "overrides",
    [
        {"LLM_PROXY_URL": "http://127.0.0.1:notaport"},
        {"LLM_PROXY_API_KEY": "kéy-☃"},
    ],
)
def test_unusable_settings_raise_the_llm_error(overrides):
    with pytest.raises(LLMError):
        proxy_with(lambda request: reply("ok"), {**PROXY_ENV, **overrides}).answer("q", CHUNKS)
