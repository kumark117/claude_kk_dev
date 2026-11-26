"""End to end: real `next start` and Uvicorn processes talking over loopback HTTP.

The frontend must already be built (`npm --prefix frontend run build`).
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

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
FIXTURES = ROOT / "e2e" / "fixtures" / "docs"
FALLBACK = "I couldn't find anything relevant in the documents."
DUMMY_KEY = "dummy-e2e-key"
MODEL = "mock-model"
STARTUP_TIMEOUT = 180
# Settings that must not leak in from the developer's shell.
ISOLATED = {
    "DOCS_DIR", "BACKEND_URL", "LLM_PROXY_URL", "LLM_PROXY_API_KEY", "LLM_PROXY_MODEL",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
}


@pytest.fixture(scope="module", autouse=True)
def stop_servers_on_signals():
    """Turn SIGTERM and SIGHUP into an interrupt, so that teardown still runs."""

    def interrupt(signum, frame):
        raise KeyboardInterrupt(f"received signal {signum}")

    previous = {s: signal.signal(s, interrupt) for s in (signal.SIGTERM, signal.SIGHUP)}
    yield
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def stop_group(process):
    """Stop the server and everything it started: its whole process group."""
    for signum in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            break
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            continue
    process.wait(timeout=10)


@contextmanager
def server(name, command, ready_path, env, cwd):
    """Run a server on a free port, wait until it answers, and always stop it."""
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    clean = {k: v for k, v in os.environ.items() if k not in ISOLATED}
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            [*command, "--port", str(port)],
            cwd=cwd,
            env={**clean, "NEXT_TELEMETRY_DISABLED": "1", **env},
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            wait_until_ready(name, process, url + ready_path, log)
            yield url
        finally:
            stop_group(process)


def wait_until_ready(name, process, url, log):
    deadline = time.monotonic() + STARTUP_TIMEOUT
    with httpx.Client(trust_env=False, timeout=10) as client:
        while process.poll() is None and time.monotonic() < deadline:
            try:
                if client.get(url).status_code == 200:
                    # Our process must be the one answering on this port.
                    time.sleep(0.2)
                    if process.poll() is None:
                        return
            except httpx.TransportError:
                pass
            time.sleep(0.2)
    log.seek(0)
    output = log.read().decode(errors="replace")
    state = "timed out" if process.poll() is None else f"exited with {process.returncode}"
    pytest.fail(f"{name} did not start ({state}). Output:\n{output}", pytrace=False)


@contextmanager
def app_stack(backend_env):
    """Start the backend and a frontend pointed at it; yield an HTTP client."""
    backend_command = [
        sys.executable, "-m", "uvicorn", "app.main:create_app",
        "--factory", "--host", "127.0.0.1",
    ]
    next_command = [str(FRONTEND / "node_modules" / ".bin" / "next"), "start",
                    "--hostname", "127.0.0.1"]
    env = {"DOCS_DIR": str(FIXTURES), **backend_env}
    with server("Uvicorn", backend_command, "/health", env, ROOT / "backend") as backend:
        frontend_env = {"BACKEND_URL": backend}
        with server("Next.js", next_command, "/", frontend_env, FRONTEND) as frontend:
            with httpx.Client(base_url=frontend, trust_env=False, timeout=60) as client:
                yield client


@pytest.fixture(scope="module")
def stub_app():
    with app_stack({}) as client:
        yield client


@pytest.fixture(scope="module")
def mock_llm():
    with MockLLM() as mock:
        yield mock


@pytest.fixture(scope="module")
def proxy_app(mock_llm):
    env = {
        "LLM_PROXY_URL": f"{mock_llm.url}/v1",
        "LLM_PROXY_API_KEY": DUMMY_KEY,
        "LLM_PROXY_MODEL": MODEL,
    }
    with app_stack(env) as client:
        yield client


@pytest.fixture
def mock(mock_llm):
    mock_llm.requests.clear()
    mock_llm.fail = False
    return mock_llm


def ask(client, question):
    return client.post("/api/ask", json={"question": question})


def test_page_loads(stub_app):
    response = stub_app.get("/")

    assert response.status_code == 200
    assert "Ask the docs" in response.text


def test_stub_answers_a_matching_question(stub_app):
    response = ask(stub_app, "How do honeybees make honey?")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "stub"
    assert body["answer"].startswith("[stub]")
    assert body["sources"][0]["file"] == "beekeeping.md"


def test_stub_falls_back_for_an_unrelated_question(stub_app):
    response = ask(stub_app, "Quarterly revenue forecast?")

    assert response.status_code == 200
    assert response.json() == {"answer": FALLBACK, "sources": [], "mode": "stub"}


def test_proxy_returns_the_llm_answer(proxy_app, mock):
    response = ask(proxy_app, "How does a glacier form?")

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == CANNED_ANSWER
    assert body["mode"] == "proxy"
    [request] = mock.requests
    assert request["path"] == "/v1/chat/completions"
    assert request["headers"]["authorization"] == f"Bearer {DUMMY_KEY}"
    assert request["body"]["model"] == MODEL
    prompt = request["body"]["messages"][-1]["content"]
    assert "glacier.txt" in prompt
    assert "snow survives the summer" in prompt


def test_proxy_failure_returns_502(proxy_app, mock):
    mock.fail = True

    response = ask(proxy_app, "How does a glacier form?")

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert len(mock.requests) == 1
