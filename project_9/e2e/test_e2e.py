"""End to end: real `next start` and Uvicorn processes, talking over HTTP.

Needs the frontend build (`npm --prefix frontend run build`).
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

from mock_llm import ANSWER, MockLLM

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
FIXTURE_DOCS = ROOT / "e2e" / "fixtures" / "docs"
STARTUP_TIMEOUT = 120
APP_VARIABLES = (
    "DOCS_DIR",
    "BACKEND_URL",
    "LLM_PROXY_URL",
    "LLM_PROXY_API_KEY",
    "LLM_PROXY_MODEL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)
MATCHING = "Why do honeybees dance?"
UNRELATED = "Quantum chromodynamics gluons?"
NO_MATCH = "I couldn't find anything relevant in the documents."


@pytest.fixture(scope="session", autouse=True)
def stop_servers_when_terminated():
    """Turn SIGTERM and SIGHUP into an interrupt, so that teardown still runs."""

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    previous = {
        number: signal.signal(number, interrupt)
        for number in (signal.SIGTERM, signal.SIGHUP)
    }
    yield
    for number, handler in previous.items():
        signal.signal(number, handler)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def base_environment() -> dict[str, str]:
    blocked = {name.lower() for name in APP_VARIABLES}
    env = {k: v for k, v in os.environ.items() if k.lower() not in blocked}
    env["NEXT_TELEMETRY_DISABLED"] = "1"
    return env


def signal_group(process, number):
    try:
        os.killpg(process.pid, number)
    except ProcessLookupError:
        pass


@contextmanager
def server(command, cwd, env, ready_url):
    """Run a server in its own process group until the block ends."""
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
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
            # Also removes any child that outlived its parent.
            signal_group(process, signal.SIGKILL)
            process.wait()


def wait_until_ready(process, url, log):
    deadline = time.monotonic() + STARTUP_TIMEOUT
    with httpx.Client(trust_env=False, timeout=5) as client:
        while process.poll() is None and time.monotonic() < deadline:
            try:
                if client.get(url).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.2)
    log.seek(0)
    output = log.read().decode(errors="replace")
    pytest.fail(
        f"Server did not become ready at {url} (exit code {process.poll()}).\n"
        f"Command: {process.args}\nOutput:\n{output}"
    )


@contextmanager
def stack(llm_env):
    """Start the backend and the frontend; yield a client for the frontend."""
    backend_port = free_port()
    backend_url = f"http://127.0.0.1:{backend_port}"
    backend = server(
        [
            sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
            "--app-dir", str(ROOT / "backend"),
            "--host", "127.0.0.1", "--port", str(backend_port),
        ],
        cwd=ROOT,
        env={**base_environment(), "DOCS_DIR": str(FIXTURE_DOCS), **llm_env},
        ready_url=f"{backend_url}/health",
    )
    with backend:
        # Chosen only now, so it cannot collide with the backend's port.
        frontend_port = free_port()
        frontend_url = f"http://127.0.0.1:{frontend_port}"
        frontend = server(
            [
                str(FRONTEND / "node_modules" / ".bin" / "next"), "start",
                "--hostname", "127.0.0.1", "--port", str(frontend_port),
            ],
            cwd=FRONTEND,
            env={**base_environment(), "BACKEND_URL": backend_url},
            ready_url=frontend_url,
        )
        with frontend:
            with httpx.Client(
                base_url=frontend_url, trust_env=False, timeout=60
            ) as client:
                yield client


@pytest.fixture(scope="module")
def stub_app():
    with stack({}) as client:
        yield client


@pytest.fixture(scope="module")
def mock_llm():
    with MockLLM() as mock:
        yield mock


@pytest.fixture(scope="module")
def proxy_app(mock_llm):
    llm_env = {
        "LLM_PROXY_URL": mock_llm.base_url,
        "LLM_PROXY_API_KEY": "dummy-key",
        "LLM_PROXY_MODEL": "dummy-model",
    }
    with stack(llm_env) as client:
        yield client


@pytest.fixture(autouse=True)
def reset_mock(mock_llm):
    mock_llm.requests.clear()
    mock_llm.fail = False


def ask(client, question):
    return client.post("/api/ask", json={"question": question})


def test_page_is_served(stub_app):
    response = stub_app.get("/")

    assert response.status_code == 200
    assert "Ask the docs" in response.text


def test_stub_answers_a_matching_question(stub_app):
    response = ask(stub_app, MATCHING)

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "stub"
    assert body["answer"].startswith("[stub]")
    assert body["sources"][0]["file"] == "beekeeping.txt"


def test_stub_falls_back_for_an_unrelated_question(stub_app):
    response = ask(stub_app, UNRELATED)

    assert response.status_code == 200
    assert response.json() == {"answer": NO_MATCH, "sources": [], "mode": "stub"}


def test_proxy_returns_the_llm_answer(proxy_app, mock_llm):
    response = ask(proxy_app, MATCHING)

    assert response.status_code == 200
    assert response.json()["answer"] == ANSWER
    assert response.json()["mode"] == "proxy"
    [request] = mock_llm.requests
    assert request["path"] == "/v1/chat/completions"
    assert request["headers"]["authorization"] == "Bearer dummy-key"
    assert request["body"]["model"] == "dummy-model"
    assert "waggle dance" in request["body"]["messages"][-1]["content"]


def test_proxy_failure_is_reported(proxy_app, mock_llm):
    mock_llm.fail = True

    response = ask(proxy_app, MATCHING)

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert len(mock_llm.requests) == 1
