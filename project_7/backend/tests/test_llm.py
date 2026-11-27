import json

import httpx
import pytest

from app.llm import ConfigError, LLMConfig, LLMError, config_from_env, generate_answer
from app.retrieval import Match

CHUNKS = [
    Match(file="volcano.md", position=0, text="Lava is molten rock.", score=2.0),
    Match(file="ocean.txt", position=1, text="The ocean is salty.", score=1.0),
]
PROXY = LLMConfig(url="http://llm.test/v1", api_key="sk-secret", model="test-model")


def recording_transport(response=None, error=None):
    requests = []

    def handler(request):
        requests.append(request)
        if error is not None:
            raise error
        if response is not None:
            return response
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "Proxy says hi."}}]}
        )

    return httpx.MockTransport(handler), requests


def forbidden_transport():
    def handler(request):
        raise AssertionError("stub mode must not use the network")

    return httpx.MockTransport(handler)


# Mode selection and configuration


@pytest.mark.parametrize("env", [{}, {"LLM_PROXY_URL": ""}])
def test_stub_mode_without_proxy_url(env):
    assert config_from_env(env).mode == "stub"


def test_other_proxy_variables_do_not_enable_proxy_mode():
    env = {"LLM_PROXY_API_KEY": "k", "LLM_PROXY_MODEL": "m"}

    assert config_from_env(env).mode == "stub"


def test_proxy_mode_with_url_and_model():
    config = config_from_env(
        {
            "LLM_PROXY_URL": "http://llm.test/v1",
            "LLM_PROXY_API_KEY": "sk-secret",
            "LLM_PROXY_MODEL": "test-model",
        }
    )

    assert config.mode == "proxy"
    assert config == PROXY


@pytest.mark.parametrize("extra", [{}, {"LLM_PROXY_MODEL": ""}])
def test_proxy_mode_requires_a_model(extra):
    with pytest.raises(ConfigError, match="LLM_PROXY_MODEL"):
        config_from_env({"LLM_PROXY_URL": "http://llm.test/v1", **extra})


def test_config_repr_hides_the_key():
    assert "sk-secret" not in repr(PROXY)


# Stub


def test_stub_returns_exact_text_from_top_chunk():
    answer = generate_answer(
        "What is lava?", CHUNKS, LLMConfig(), transport=forbidden_transport()
    )

    assert answer == "[stub] Based on volcano.md: Lava is molten rock."


# Proxy


@pytest.mark.parametrize("base", ["http://llm.test/v1", "http://llm.test/v1/"])
def test_proxy_request_url_headers_model_and_messages(base):
    transport, requests = recording_transport()
    config = LLMConfig(url=base, api_key="sk-secret", model="test-model")

    answer = generate_answer("What is lava?", CHUNKS, config, transport=transport)

    assert answer == "Proxy says hi."
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "http://llm.test/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer sk-secret"
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
    order = [
        content.index(part)
        for part in (
            "volcano.md",
            "Lava is molten rock.",
            "ocean.txt",
            "The ocean is salty.",
            "What is lava?",
        )
    ]
    assert order == sorted(order)


def test_proxy_uses_a_30_second_timeout():
    transport, requests = recording_transport()

    generate_answer("q", CHUNKS, PROXY, transport=transport)

    assert requests[0].extensions["timeout"] == {
        "connect": 30.0,
        "read": 30.0,
        "write": 30.0,
        "pool": 30.0,
    }


def test_no_auth_header_without_a_key():
    transport, requests = recording_transport()
    config = LLMConfig(url="http://llm.test/v1", model="test-model")

    generate_answer("q", CHUNKS, config, transport=transport)

    assert "Authorization" not in requests[0].headers


def content_response(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="upstream-secret-body"),
        httpx.Response(401, json={"error": "upstream-secret-body"}),
        httpx.Response(302, headers={"Location": "http://elsewhere.test/"}),
        httpx.Response(200, text="upstream-secret-body not json"),
        httpx.Response(200, content=b"\xff\xfe\xfa"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json="text"),
        httpx.Response(200, json=None),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": {}}),
        httpx.Response(200, json={"choices": "abc"}),
        httpx.Response(200, json={"choices": [{}]}),
        httpx.Response(200, json={"choices": [{"message": None}]}),
        httpx.Response(200, json={"choices": [{"message": {}}]}),
        content_response(None),
        content_response(42),
        content_response(["a"]),
        content_response(""),
    ],
)
def test_bad_responses_raise_llm_error(response):
    transport, requests = recording_transport(response=response)

    with pytest.raises(LLMError) as caught:
        generate_answer("q", CHUNKS, PROXY, transport=transport)

    assert len(requests) == 1  # no retries
    assert "upstream-secret-body" not in str(caught.value)
    assert "sk-secret" not in str(caught.value)


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("refused sk-secret"),
        httpx.ReadTimeout("slow sk-secret"),
        httpx.ConnectTimeout("slow sk-secret"),
        httpx.RemoteProtocolError("garbled sk-secret"),
    ],
)
def test_transport_failures_raise_llm_error_without_retry(error):
    transport, requests = recording_transport(error=error)

    with pytest.raises(LLMError) as caught:
        generate_answer("q", CHUNKS, PROXY, transport=transport)

    assert len(requests) == 1
    assert "sk-secret" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


@pytest.mark.parametrize(
    "config",
    [
        LLMConfig(url="http://llm.test:not-a-port/v1", model="m"),
        LLMConfig(url="not-a-url", model="m"),
        LLMConfig(url="http://127.0.0.1:9/v1", api_key="clé-secrète", model="m"),
    ],
)
def test_unusable_settings_raise_llm_error(config):
    with pytest.raises(LLMError):
        generate_answer("q", CHUNKS, config)


def test_proxy_does_not_log_key_or_upstream_body(caplog):
    caplog.set_level("DEBUG")
    transport, _ = recording_transport(
        response=httpx.Response(500, text="upstream-secret-body")
    )

    with pytest.raises(LLMError):
        generate_answer("q", CHUNKS, PROXY, transport=transport)

    assert "sk-secret" not in caplog.text
    assert "upstream-secret-body" not in caplog.text
