import json

import httpx
import pytest

from app.llm import LLMConfig, LLMConfigError, LLMError, config_from_env, generate_answer
from app.retrieval import Result

CHUNKS = [
    Result(file="tea.md", position=0, text="Boil the water.", score=2.0),
    Result(file="coffee.txt", position=1, text="Grind the beans.", score=1.0),
]
KEY = "dummy-key-123"
PROXY = LLMConfig(url="http://127.0.0.1:9/v1", model="test-model", api_key=KEY)


def reply(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def run(config, handler):
    return generate_answer(
        "How do I make tea?", CHUNKS, config, transport=httpx.MockTransport(handler)
    )


def capture(config):
    seen = []

    def handler(request):
        seen.append(request)
        return reply("The answer.")

    answer = run(config, handler)
    assert len(seen) == 1
    return answer, seen[0]


# Stub


def test_stub_returns_exact_text_from_the_top_chunk_without_network():
    def handler(request):
        raise AssertionError("stub mode must not use the network")

    answer = run(LLMConfig(), handler)

    assert answer == "[stub] Based on tea.md: Boil the water."


# Proxy request


def test_proxy_posts_to_chat_completions_and_returns_content():
    answer, request = capture(PROXY)

    assert answer == "The answer."
    assert request.method == "POST"
    assert str(request.url) == "http://127.0.0.1:9/v1/chat/completions"


def test_proxy_strips_trailing_slash_from_base():
    config = config_from_env(
        {"LLM_PROXY_URL": "http://127.0.0.1:9/v1/", "LLM_PROXY_MODEL": "m"}
    )

    _, request = capture(config)

    assert str(request.url) == "http://127.0.0.1:9/v1/chat/completions"


def test_proxy_sends_bearer_key_and_json_content_type():
    _, request = capture(PROXY)

    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["content-type"] == "application/json"


def test_proxy_sends_no_auth_header_without_a_key():
    _, request = capture(LLMConfig(url=PROXY.url, model="test-model"))

    assert "authorization" not in request.headers


def test_proxy_body_has_model_and_two_messages():
    _, request = capture(PROXY)
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
            "tea.md",
            "Boil the water.",
            "coffee.txt",
            "Grind the beans.",
            "How do I make tea?",
        )
    ]
    assert positions == sorted(positions)


def test_proxy_uses_a_30_second_timeout():
    _, request = capture(PROXY)

    assert request.extensions["timeout"] == {
        "connect": 30.0,
        "read": 30.0,
        "write": 30.0,
        "pool": 30.0,
    }


# Proxy failures


def raising(error_type):
    def handler(request):
        raise error_type("secret upstream detail", request=request)

    return handler


def responding(*args, **kwargs):
    return lambda request: httpx.Response(*args, **kwargs)


def with_content(content):
    return responding(200, json={"choices": [{"message": {"content": content}}]})


@pytest.mark.parametrize(
    "handler",
    [
        pytest.param(raising(httpx.ConnectError), id="connection-error"),
        pytest.param(raising(httpx.ReadTimeout), id="timeout"),
        pytest.param(responding(500, text="secret upstream detail"), id="500"),
        pytest.param(responding(401, json={"error": "secret upstream detail"}), id="401"),
        pytest.param(responding(302, headers={"location": "/x"}), id="redirect"),
        pytest.param(responding(200, text="secret upstream detail"), id="invalid-json"),
        pytest.param(responding(200, content=b"\xff\xfe\xfa"), id="undecodable"),
        pytest.param(responding(200, json={}), id="no-choices"),
        pytest.param(responding(200, json=[]), id="not-an-object"),
        pytest.param(responding(200, json={"choices": []}), id="empty-choices"),
        pytest.param(responding(200, json={"choices": None}), id="null-choices"),
        pytest.param(responding(200, json={"choices": [{}]}), id="no-message"),
        pytest.param(responding(200, json={"choices": [{"message": {}}]}), id="no-content"),
        pytest.param(with_content(None), id="null-content"),
        pytest.param(with_content(42), id="number-content"),
        pytest.param(with_content(["x"]), id="list-content"),
        pytest.param(with_content(""), id="empty-content"),
    ],
)
def test_each_proxy_failure_raises_llm_error_without_leaking(handler, caplog):
    with pytest.raises(LLMError) as caught:
        run(PROXY, handler)

    message = str(caught.value)
    assert KEY not in message
    assert "secret upstream detail" not in message
    # No chained exception that a traceback would print.
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None or caught.value.__suppress_context__
    assert KEY not in caplog.text
    assert "secret upstream detail" not in caplog.text


@pytest.mark.parametrize("url", ["not a url", "http://[bad", "ftp://127.0.0.1/v1"])
def test_unusable_base_url_raises_llm_error(url):
    config = LLMConfig(url=url, model="m")

    with pytest.raises(LLMError):
        generate_answer("q", CHUNKS, config)


# Mode selection


@pytest.mark.parametrize("env", [{}, {"LLM_PROXY_URL": ""}, {"LLM_PROXY_MODEL": "m"}])
def test_mode_is_stub_without_a_proxy_url(env):
    assert config_from_env(env).mode == "stub"


def test_mode_is_proxy_with_a_url():
    config = config_from_env(
        {
            "LLM_PROXY_URL": "http://127.0.0.1:9/v1",
            "LLM_PROXY_MODEL": "m",
            "LLM_PROXY_API_KEY": KEY,
        }
    )

    assert (config.mode, config.model, config.api_key) == ("proxy", "m", KEY)


def test_config_repr_hides_the_key():
    assert KEY not in repr(PROXY)


@pytest.mark.parametrize("model", [{}, {"LLM_PROXY_MODEL": ""}])
def test_missing_model_in_proxy_mode_is_a_config_error(model):
    with pytest.raises(LLMConfigError, match="LLM_PROXY_MODEL"):
        config_from_env({"LLM_PROXY_URL": "http://127.0.0.1:9/v1", **model})
