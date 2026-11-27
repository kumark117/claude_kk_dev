import json

import httpx
import pytest

from app.llm import LLMError, LLMSettings, generate_answer
from app.retrieval import Hit

CHUNKS = [
    Hit("volcano.md", "Lava is molten rock.", 2.0),
    Hit("ocean.txt", "Tides follow the moon.", 1.0),
]
KEY = "dummy-key-123"


def proxy_settings(**overrides) -> LLMSettings:
    env = {
        "LLM_PROXY_URL": "http://llm.test/v1",
        "LLM_PROXY_MODEL": "test-model",
        "LLM_PROXY_API_KEY": KEY,
    }
    env.update(overrides)
    return LLMSettings.from_env(env)


def ok_response(content="An answer."):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


class Recorder:
    def __init__(self, response=None):
        self.requests: list[httpx.Request] = []
        self.response = response or ok_response()
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request):
        self.requests.append(request)
        return self.response


# --- mode selection ---


@pytest.mark.parametrize("env", [{}, {"LLM_PROXY_URL": ""}, {"LLM_PROXY_URL": "  "}])
def test_stub_mode_when_proxy_url_is_empty(env):
    assert LLMSettings.from_env(env).mode == "stub"


def test_stub_mode_ignores_other_proxy_variables():
    env = {"LLM_PROXY_MODEL": "m", "LLM_PROXY_API_KEY": "k"}

    assert LLMSettings.from_env(env).mode == "stub"


def test_proxy_mode_when_url_is_set():
    assert proxy_settings().mode == "proxy"


@pytest.mark.parametrize("model", [None, "", "  "])
def test_missing_model_fails_in_proxy_mode(model):
    env = {"LLM_PROXY_URL": "http://llm.test/v1"}
    if model is not None:
        env["LLM_PROXY_MODEL"] = model

    with pytest.raises(ValueError, match="LLM_PROXY_MODEL"):
        LLMSettings.from_env(env)


def test_settings_repr_hides_the_api_key():
    assert KEY not in repr(proxy_settings())


# --- stub ---


def test_stub_returns_exact_text_from_top_chunk_without_network():
    def explode(request):
        raise AssertionError("stub must not use the network")

    answer = generate_answer(
        "q", CHUNKS, LLMSettings.from_env({}), transport=httpx.MockTransport(explode)
    )

    assert answer == "[stub] Based on volcano.md: Lava is molten rock."


# --- proxy request ---


@pytest.mark.parametrize("base", ["http://llm.test/v1", "http://llm.test/v1/"])
def test_proxy_posts_to_chat_completions(base):
    recorder = Recorder()

    generate_answer("q", CHUNKS, proxy_settings(LLM_PROXY_URL=base), recorder.transport)

    [request] = recorder.requests
    assert request.method == "POST"
    assert str(request.url) == "http://llm.test/v1/chat/completions"


def test_proxy_sends_bearer_key_and_json():
    recorder = Recorder()

    generate_answer("q", CHUNKS, proxy_settings(), recorder.transport)

    [request] = recorder.requests
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["content-type"] == "application/json"


def test_proxy_sends_no_auth_header_without_a_key():
    recorder = Recorder()

    generate_answer(
        "q", CHUNKS, proxy_settings(LLM_PROXY_API_KEY=""), recorder.transport
    )

    assert "authorization" not in recorder.requests[0].headers


def test_proxy_sends_model_and_messages():
    recorder = Recorder()

    generate_answer("What is lava?", CHUNKS, proxy_settings(), recorder.transport)

    body = json.loads(recorder.requests[0].content)
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
            "volcano.md",
            "Lava is molten rock.",
            "ocean.txt",
            "Tides follow the moon.",
            "What is lava?",
        )
    ]
    assert positions == sorted(positions)


def test_proxy_returns_message_content():
    recorder = Recorder(ok_response("Molten rock."))

    assert (
        generate_answer("q", CHUNKS, proxy_settings(), recorder.transport)
        == "Molten rock."
    )


def test_proxy_uses_a_30_second_timeout():
    recorder = Recorder()

    generate_answer("q", CHUNKS, proxy_settings(), recorder.transport)

    timeout = recorder.requests[0].extensions["timeout"]
    assert timeout == {"connect": 30.0, "read": 30.0, "write": 30.0, "pool": 30.0}


# --- proxy failures ---


def raising(error_type):
    def handle(request):
        raise error_type("boom", request=request)

    return handle


def replying(*args, **kwargs):
    return lambda request: httpx.Response(*args, **kwargs)


@pytest.mark.parametrize(
    "handler",
    [
        pytest.param(raising(httpx.ConnectError), id="connection-error"),
        pytest.param(raising(httpx.ReadTimeout), id="timeout"),
        pytest.param(replying(500, text="upstream-secret"), id="500"),
        pytest.param(replying(401, json={"error": "upstream-secret"}), id="401"),
        pytest.param(
            replying(302, headers={"location": "http://elsewhere.test/"}),
            id="redirect",
        ),
        pytest.param(replying(200, text="upstream-secret"), id="invalid-json"),
        pytest.param(replying(200, content=b"\xff\xfe\xfa"), id="undecodable"),
        pytest.param(replying(200, json=[]), id="not-an-object"),
        pytest.param(replying(200, json={}), id="no-choices"),
        pytest.param(replying(200, json={"choices": []}), id="empty-choices"),
        pytest.param(replying(200, json={"choices": "x"}), id="choices-not-list"),
        pytest.param(replying(200, json={"choices": [{}]}), id="no-message"),
        pytest.param(
            replying(200, json={"choices": [{"message": None}]}), id="null-message"
        ),
        pytest.param(
            replying(200, json={"choices": [{"message": {}}]}), id="no-content"
        ),
        pytest.param(lambda r: ok_response(None), id="null-content"),
        pytest.param(lambda r: ok_response(42), id="non-string-content"),
        pytest.param(lambda r: ok_response(""), id="empty-content"),
        pytest.param(lambda r: ok_response("  \n"), id="blank-content"),
    ],
)
def test_proxy_failure_raises_llm_error_without_leaking(handler, caplog):
    caplog.set_level("DEBUG")

    with pytest.raises(LLMError) as caught:
        generate_answer("q", CHUNKS, proxy_settings(), httpx.MockTransport(handler))

    for secret in (KEY, "upstream-secret"):
        assert secret not in str(caught.value)
        assert secret not in caplog.text


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"LLM_PROXY_URL": "http://llm.test:notaport"}, id="bad-port"),
        pytest.param({"LLM_PROXY_URL": "not-a-url"}, id="no-scheme"),
        pytest.param({"LLM_PROXY_API_KEY": "kéy-☃"}, id="unencodable-key"),
    ],
)
def test_unusable_configuration_raises_llm_error(overrides):
    # A mock transport keeps these cases off the network.
    def handle(request):
        if request.url.scheme not in ("http", "https"):
            raise httpx.UnsupportedProtocol("unsupported", request=request)
        return ok_response()

    with pytest.raises(LLMError):
        generate_answer(
            "q", CHUNKS, proxy_settings(**overrides), httpx.MockTransport(handle)
        )
