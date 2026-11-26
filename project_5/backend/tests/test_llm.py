import json
import traceback

import httpx
import pytest

from app.llm import LLMConfig, LLMError, generate_answer, load_config
from app.retrieval import Chunk

CHUNKS = [
    Chunk("tides.txt", 0, "Tides follow the moon."),
    Chunk("bees.md", 2, "Bees make honey."),
]

PROXY = LLMConfig(url="http://llm.test/v1", api_key="secret-key", model="m-1")


def reply(content="An answer."):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def transport_returning(*, status=200, **kwargs):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, **kwargs)

    return httpx.MockTransport(handler), requests


def forbidden_transport():
    def handler(request):
        raise AssertionError("no request expected")

    return httpx.MockTransport(handler)


# Mode selection


@pytest.mark.parametrize("env", [{}, {"LLM_PROXY_URL": ""}])
def test_stub_mode_without_proxy_url(env):
    assert load_config(env).mode == "stub"


def test_model_and_key_alone_do_not_enable_proxy_mode():
    env = {"LLM_PROXY_MODEL": "m", "LLM_PROXY_API_KEY": "k"}

    assert load_config(env).mode == "stub"


def test_proxy_mode_reads_settings():
    config = load_config(
        {
            "LLM_PROXY_URL": "http://llm.test/v1",
            "LLM_PROXY_API_KEY": "secret-key",
            "LLM_PROXY_MODEL": "m-1",
        }
    )

    assert config == PROXY
    assert config.mode == "proxy"


@pytest.mark.parametrize("extra", [{}, {"LLM_PROXY_MODEL": ""}])
def test_proxy_mode_requires_a_model(extra):
    with pytest.raises(ValueError, match="LLM_PROXY_MODEL"):
        load_config({"LLM_PROXY_URL": "http://llm.test/v1", **extra})


def test_config_repr_hides_the_key():
    assert "secret-key" not in repr(PROXY)


# Stub


def test_stub_returns_exact_text_from_top_chunk():
    answer = generate_answer(
        "tides?", CHUNKS, LLMConfig(), transport=forbidden_transport()
    )

    assert answer == "[stub] Based on tides.txt: Tides follow the moon."


# Proxy requests


@pytest.mark.parametrize(
    "base",
    ["http://llm.test/v1", "http://llm.test/v1/"],
)
def test_proxy_posts_to_chat_completions(base):
    transport, requests = transport_returning(json=reply())
    config = LLMConfig(url=base, api_key="secret-key", model="m-1")

    answer = generate_answer("Why tides?", CHUNKS, config, transport=transport)

    assert answer == "An answer."
    [request] = requests
    assert request.method == "POST"
    assert str(request.url) == "http://llm.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer secret-key"
    assert request.headers["content-type"] == "application/json"


def test_proxy_sends_model_and_messages():
    transport, requests = transport_returning(json=reply())

    generate_answer("Why tides?", CHUNKS, PROXY, transport=transport)

    body = json.loads(requests[0].content)
    assert set(body) == {"model", "messages"}
    assert body["model"] == "m-1"
    system, user = body["messages"]
    assert system["role"] == "system"
    assert "only" in system["content"]
    assert "context" in system["content"]
    assert "not enough" in system["content"]
    assert user["role"] == "user"
    content = user["content"]
    positions = [
        content.index(part)
        for part in (
            "tides.txt",
            "Tides follow the moon.",
            "bees.md",
            "Bees make honey.",
            "Why tides?",
        )
    ]
    assert positions == sorted(positions)


def test_proxy_omits_authorization_without_a_key():
    transport, requests = transport_returning(json=reply())
    config = LLMConfig(url="http://llm.test/v1", model="m-1")

    generate_answer("q", CHUNKS, config, transport=transport)

    assert "authorization" not in requests[0].headers


def test_proxy_uses_a_30_second_timeout():
    transport, requests = transport_returning(json=reply())

    generate_answer("q", CHUNKS, PROXY, transport=transport)

    assert requests[0].extensions["timeout"] == {
        "connect": 30,
        "read": 30,
        "write": 30,
        "pool": 30,
    }


# Proxy failures


@pytest.mark.parametrize(
    "error", [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError]
)
def test_transport_failures_raise_llm_error_without_retrying(error):
    calls = []

    def handler(request):
        calls.append(request)
        raise error("boom", request=request)

    with pytest.raises(LLMError):
        generate_answer("q", CHUNKS, PROXY, transport=httpx.MockTransport(handler))

    assert len(calls) == 1


@pytest.mark.parametrize("status", [301, 400, 401, 429, 500, 503])
def test_non_2xx_status_raises_llm_error(status):
    transport, requests = transport_returning(status=status, json=reply())

    with pytest.raises(LLMError):
        generate_answer("q", CHUNKS, PROXY, transport=transport)

    assert len(requests) == 1


@pytest.mark.parametrize("body", [b"not json", b"", b"\xff\xfe\xfa"])
def test_invalid_json_raises_llm_error(body):
    transport, _ = transport_returning(content=body)

    with pytest.raises(LLMError):
        generate_answer("q", CHUNKS, PROXY, transport=transport)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        "text",
        None,
        {"choices": None},
        {"choices": []},
        {"choices": {}},
        {"choices": "abc"},
        {"choices": [{}]},
        {"choices": [{"message": None}]},
        {"choices": [{"message": {}}]},
        reply(None),
        reply(42),
        reply(["a"]),
        reply(""),
    ],
)
def test_unusable_content_raises_llm_error(payload):
    transport, _ = transport_returning(content=json.dumps(payload).encode())

    with pytest.raises(LLMError):
        generate_answer("q", CHUNKS, PROXY, transport=transport)


@pytest.mark.parametrize(
    "config",
    [
        LLMConfig(url="llm.test/v1", api_key="secret-key", model="m"),
        LLMConfig(url="http://llm.test:bad", api_key="secret-key", model="m"),
        LLMConfig(url="http://llm.test", api_key="secret-key-é", model="m"),
    ],
)
def test_unusable_settings_raise_llm_error_without_the_key(config):
    with pytest.raises(LLMError) as caught:
        generate_answer("q", CHUNKS, config)

    assert "secret-key" not in "".join(traceback.format_exception(caught.value))


def test_llm_error_hides_key_and_upstream_body():
    transport, _ = transport_returning(status=500, text="upstream-private")

    with pytest.raises(LLMError) as caught:
        generate_answer("q", CHUNKS, PROXY, transport=transport)

    trace = "".join(traceback.format_exception(caught.value))
    assert "secret-key" not in trace
    assert "upstream-private" not in trace
