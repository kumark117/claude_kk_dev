import json
import logging

import httpx
import pytest

from app.llm import LLMConfigError, LLMError, build_llm
from app.retrieval import Hit

HITS = [
    Hit("moon.md", 0, "The Moon orbits the Earth.", 2.5),
    Hit("sun.txt", 3, "The Sun is a star.", 1.0),
]
KEY = "dummy-secret-key"
PROXY_ENV = {
    "LLM_PROXY_URL": "http://llm.test/v1",
    "LLM_PROXY_MODEL": "test-model",
    "LLM_PROXY_API_KEY": KEY,
}


def reply(content: object) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def recording_transport(requests: list, response: httpx.Response | None = None):
    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response or reply("canned answer")

    return httpx.MockTransport(handle)


def forbidden_transport():
    def handle(request: httpx.Request) -> httpx.Response:
        raise AssertionError("stub mode must not use the network")

    return httpx.MockTransport(handle)


# Mode selection


@pytest.mark.parametrize("env", [{}, {"LLM_PROXY_URL": ""}])
def test_stub_mode_without_a_proxy_url(env):
    assert build_llm(env).mode == "stub"


def test_proxy_mode_with_a_proxy_url():
    assert build_llm(PROXY_ENV).mode == "proxy"


@pytest.mark.parametrize("model", [None, ""])
def test_proxy_mode_without_a_model_fails_startup(model):
    env = {"LLM_PROXY_URL": "http://llm.test/v1"}
    if model is not None:
        env["LLM_PROXY_MODEL"] = model

    with pytest.raises(LLMConfigError, match="LLM_PROXY_MODEL"):
        build_llm(env)


# Stub


def test_stub_returns_exact_text_from_the_top_chunk():
    llm = build_llm({}, transport=forbidden_transport())

    assert llm.answer("Where is the Moon?", HITS) == (
        "[stub] Based on moon.md: The Moon orbits the Earth."
    )


# Proxy


@pytest.mark.parametrize(
    "base", ["http://llm.test/api/v1", "http://llm.test/api/v1/"]
)
def test_proxy_posts_to_chat_completions_under_the_base(base):
    requests: list[httpx.Request] = []
    llm = build_llm(
        {**PROXY_ENV, "LLM_PROXY_URL": base}, transport=recording_transport(requests)
    )

    assert llm.answer("q", HITS) == "canned answer"
    assert [(r.method, str(r.url)) for r in requests] == [
        ("POST", "http://llm.test/api/v1/chat/completions")
    ]


def test_proxy_sends_headers_model_and_messages():
    requests: list[httpx.Request] = []
    llm = build_llm(PROXY_ENV, transport=recording_transport(requests))

    llm.answer("Where is the Moon?", HITS)

    [request] = requests
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
            "moon.md",
            "The Moon orbits the Earth.",
            "sun.txt",
            "The Sun is a star.",
            "Where is the Moon?",
        )
    ]
    assert positions == sorted(positions)


@pytest.mark.parametrize("key", [None, ""])
def test_proxy_sends_no_auth_header_without_a_key(key):
    env = {k: v for k, v in PROXY_ENV.items() if k != "LLM_PROXY_API_KEY"}
    if key is not None:
        env["LLM_PROXY_API_KEY"] = key
    requests: list[httpx.Request] = []

    build_llm(env, transport=recording_transport(requests)).answer("q", HITS)

    assert "authorization" not in requests[0].headers


def test_proxy_uses_a_30_second_timeout():
    requests: list[httpx.Request] = []

    build_llm(PROXY_ENV, transport=recording_transport(requests)).answer("q", HITS)

    assert set(requests[0].extensions["timeout"].values()) == {30.0}


def raising(error_type):
    def handle(request: httpx.Request) -> httpx.Response:
        raise error_type(f"failed with {KEY}", request=request)

    return handle


def responding(status: int = 200, **kwargs):
    return lambda request: httpx.Response(status, **kwargs)


GOOD = {"choices": [{"message": {"content": "ok"}}]}


@pytest.mark.parametrize(
    "handler",
    [
        pytest.param(raising(httpx.ConnectError), id="connection-error"),
        pytest.param(raising(httpx.ReadTimeout), id="timeout"),
        pytest.param(responding(500, text="UPSTREAM-BODY"), id="status-500"),
        pytest.param(responding(401, json=GOOD), id="status-401"),
        pytest.param(responding(302, json=GOOD), id="status-302"),
        pytest.param(responding(text="UPSTREAM-BODY"), id="invalid-json"),
        pytest.param(responding(content=b"\xff\xfe\xfd"), id="undecodable"),
        pytest.param(responding(json=[]), id="not-an-object"),
        pytest.param(responding(json={}), id="no-choices"),
        pytest.param(responding(json={"choices": []}), id="empty-choices"),
        pytest.param(responding(json={"choices": "text"}), id="choices-string"),
        pytest.param(responding(json={"choices": [{}]}), id="no-message"),
        pytest.param(responding(json={"choices": [{"message": None}]}), id="null"),
        pytest.param(responding(json={"choices": [{"message": {}}]}), id="no-content"),
        pytest.param(lambda r: reply(None), id="null-content"),
        pytest.param(lambda r: reply(42), id="number-content"),
        pytest.param(lambda r: reply(["x"]), id="list-content"),
        pytest.param(lambda r: reply(""), id="empty-content"),
    ],
)
def test_proxy_failures_raise_the_llm_error_without_leaking(handler, caplog):
    llm = build_llm(PROXY_ENV, transport=httpx.MockTransport(handler))

    with caplog.at_level(logging.DEBUG), pytest.raises(LLMError) as caught:
        llm.answer("q", HITS)

    # Nothing sensitive in the error, its visible chain, or the logs.
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ or caught.value.__context__ is None
    for secret in (KEY, "UPSTREAM-BODY"):
        assert secret not in str(caught.value)
        assert secret not in caplog.text


@pytest.mark.parametrize("url", ["not-a-url", "http://[broken"])
def test_unusable_proxy_url_raises_the_llm_error(url):
    llm = build_llm({**PROXY_ENV, "LLM_PROXY_URL": url})

    with pytest.raises(LLMError):
        llm.answer("q", HITS)


def test_unencodable_api_key_raises_the_llm_error():
    llm = build_llm(
        {**PROXY_ENV, "LLM_PROXY_API_KEY": "kéy-€"},
        transport=recording_transport([]),
    )

    with pytest.raises(LLMError) as caught:
        llm.answer("q", HITS)

    assert "kéy" not in str(caught.value)
