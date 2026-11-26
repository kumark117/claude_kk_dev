"""Browser-side requests through real Next.js and FastAPI processes.

Needs a finished `npm --prefix frontend run build`; test.sh does that first.
"""

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from mock_llm import CANNED_ANSWER, MockLLM

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
FIXTURE_DOCS = ROOT / "e2e" / "fixtures" / "docs"
START_TIMEOUT = 60
DUMMY_KEY = "dummy-key"
MODEL = "mock-model"
FALLBACK = "I couldn't find anything relevant in the documents."

# Inherited values for these must not change what the servers do.
ISOLATED = {
    "DOCS_DIR",
    "BACKEND_URL",
    "LLM_PROXY_URL",
    "LLM_PROXY_API_KEY",
    "LLM_PROXY_MODEL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
}


@pytest.fixture(scope="module", autouse=True)
def stop_servers_on_signals():
    """Turn termination signals into an exception, so that cleanup still runs."""

    def interrupt(signum, frame):
        raise KeyboardInterrupt(f"received signal {signum}")

    signals = (signal.SIGTERM, signal.SIGHUP)
    previous = {s: signal.signal(s, interrupt) for s in signals}
    yield
    for s, handler in previous.items():
        signal.signal(s, handler)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def client(base_url: str) -> httpx.Client:
    return httpx.Client(base_url=base_url, trust_env=False, timeout=40)


def signal_group(process: subprocess.Popen, signum: int) -> None:
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass


@contextmanager
def server(command: list[str], ready_url: str, env: dict[str, str], cwd: Path):
    """Run a server in its own process group until the block ends."""
    base = {k: v for k, v in os.environ.items() if k.upper() not in ISOLATED}
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env={**base, "NEXT_TELEMETRY_DISABLED": "1", **env},
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            wait_until_ready(process, ready_url, log)
            yield
        finally:
            signal_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            # Anything left in the group, including children of the leader.
            signal_group(process, signal.SIGKILL)
            process.wait()


def wait_until_ready(process, url, log) -> None:
    deadline = time.monotonic() + START_TIMEOUT
    with httpx.Client(trust_env=False, timeout=5) as http:
        while time.monotonic() < deadline and process.poll() is None:
            try:
                http.get(url)
                # Any HTTP reply means the server is up; tests judge the content.
                if process.poll() is None:
                    return
            except httpx.TransportError:
                time.sleep(0.1)
    log.seek(0)
    output = log.read().decode(errors="replace")
    pytest.fail(f"{process.args} did not become ready at {url}\n{output}")


@contextmanager
def stack(backend_env: dict[str, str]):
    """Start FastAPI and Next.js; yield a client for the Next.js server."""
    backend_port, frontend_port = free_port(), free_port()
    backend_url = f"http://127.0.0.1:{backend_port}"
    frontend_url = f"http://127.0.0.1:{frontend_port}"
    backend_command = [
        sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
        "--app-dir", str(ROOT / "backend"),
        "--host", "127.0.0.1", "--port", str(backend_port),
    ]  # fmt: skip
    frontend_command = [
        str(FRONTEND / "node_modules" / ".bin" / "next"), "start",
        "--hostname", "127.0.0.1", "--port", str(frontend_port),
    ]  # fmt: skip
    with (
        server(
            backend_command,
            f"{backend_url}/health",
            {"DOCS_DIR": str(FIXTURE_DOCS), **backend_env},
            ROOT,
        ),
        server(
            frontend_command,
            frontend_url,
            {"BACKEND_URL": backend_url},
            FRONTEND,
        ),
        client(frontend_url) as http,
    ):
        yield http


@pytest.fixture(scope="module")
def stub():
    with stack({}) as http:
        yield http


@pytest.fixture(scope="module")
def mock_llm():
    with MockLLM() as mock:
        yield mock


@pytest.fixture(scope="module")
def proxy(mock_llm):
    env = {
        "LLM_PROXY_URL": f"{mock_llm.url}/v1",
        "LLM_PROXY_API_KEY": DUMMY_KEY,
        "LLM_PROXY_MODEL": MODEL,
    }
    with stack(env) as http:
        yield http


@pytest.fixture(autouse=True)
def reset_mock(request):
    if "mock_llm" in request.fixturenames:
        mock = request.getfixturevalue("mock_llm")
        mock.fail = False
        mock.requests.clear()


def ask(http: httpx.Client, question: str) -> httpx.Response:
    return http.post("/api/ask", json={"question": question})


def test_page_is_served(stub):
    response = stub.get("/")

    assert response.status_code == 200
    assert "Ask the docs" in response.text


def test_stub_answers_a_matching_question(stub):
    response = ask(stub, "Why do volcanoes erupt?")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "stub"
    assert body["answer"].startswith("[stub]")
    assert body["sources"][0]["file"] == "volcanoes.md"


def test_stub_gives_fallback_for_an_unrelated_question(stub):
    response = ask(stub, "Quarterly tax deadlines?")

    assert response.status_code == 200
    assert response.json() == {"answer": FALLBACK, "sources": [], "mode": "stub"}


def test_proxy_returns_the_llm_answer(proxy, mock_llm):
    response = ask(proxy, "How is espresso brewed?")

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == CANNED_ANSWER
    assert body["mode"] == "proxy"
    [sent] = mock_llm.requests
    assert sent["path"] == "/v1/chat/completions"
    assert sent["authorization"] == f"Bearer {DUMMY_KEY}"
    assert sent["body"]["model"] == MODEL
    user_message = sent["body"]["messages"][1]["content"]
    assert body["sources"][0]["text"] in user_message
    assert "finely ground coffee beans" in user_message


def test_proxy_failure_is_reported(proxy, mock_llm):
    mock_llm.fail = True

    response = ask(proxy, "How is espresso brewed?")

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert len(mock_llm.requests) == 1
