"""End to end: real Next.js and FastAPI processes, all requests over loopback HTTP.

Needs the Next.js production build (`npm --prefix frontend run build`).
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

from mock_llm import MockLLM

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "e2e" / "fixtures" / "docs"
STARTUP_TIMEOUT = 90
DUMMY_KEY = "dummy-e2e-key"
MODEL = "e2e-model"
QUESTION = "How should I feed a sourdough starter?"
APP_VARIABLES = (
    "DOCS_DIR",
    "BACKEND_URL",
    "LLM_PROXY_URL",
    "LLM_PROXY_API_KEY",
    "LLM_PROXY_MODEL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def clean_env(**extra) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in APP_VARIABLES}
    return {**env, "NEXT_TELEMETRY_DISABLED": "1", **extra}


def stop_group(process: subprocess.Popen) -> None:
    """Stop the process and everything it started (it leads its own group)."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            process.poll()  # reap the leader so the group can disappear
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
    process.wait(timeout=10)


@contextmanager
def server(command: list[str], ready_url: str, env: dict[str, str], cwd: Path):
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            wait_until_ready(process, ready_url, output)
            yield
        finally:
            stop_group(process)


def wait_until_ready(process, url, output) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    with httpx.Client(trust_env=False, timeout=5) as client:
        while time.monotonic() < deadline and process.poll() is None:
            try:
                if client.get(url).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.2)
    output.seek(0)
    log = output.read().decode(errors="replace")
    pytest.fail(f"Server did not become ready at {url}. Output:\n{log}", pytrace=False)


@contextmanager
def stack(**backend_env):
    """Start FastAPI and Next.js; yield a client for the Next.js server."""
    backend_port, frontend_port = free_port(), free_port()
    backend_url = f"http://127.0.0.1:{backend_port}"
    frontend_url = f"http://127.0.0.1:{frontend_port}"
    backend = [
        sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
        "--app-dir", str(ROOT / "backend"),
        "--host", "127.0.0.1", "--port", str(backend_port),
    ]
    # The next binary directly, so there is no npm wrapper process in between.
    frontend = [
        "node", str(ROOT / "frontend" / "node_modules" / "next" / "dist" / "bin" / "next"),
        "start", "--hostname", "127.0.0.1", "--port", str(frontend_port),
    ]
    with server(
        backend,
        f"{backend_url}/health",
        clean_env(DOCS_DIR=str(DOCS), **backend_env),
        ROOT,
    ):
        with server(
            frontend,
            frontend_url,
            clean_env(BACKEND_URL=backend_url),
            ROOT / "frontend",
        ):
            with httpx.Client(base_url=frontend_url, trust_env=False, timeout=60) as client:
                yield client


@pytest.fixture(scope="module")
def stub_app():
    with stack() as client:
        yield client


@pytest.fixture(scope="module")
def mock_llm():
    with MockLLM() as mock:
        yield mock


@pytest.fixture(scope="module")
def proxy_app(mock_llm):
    with stack(
        LLM_PROXY_URL=f"{mock_llm.url}/v1",
        LLM_PROXY_API_KEY=DUMMY_KEY,
        LLM_PROXY_MODEL=MODEL,
    ) as client:
        yield client


def ask(client, question):
    return client.post("/api/ask", json={"question": question})


def test_home_page(stub_app):
    response = stub_app.get("/")

    assert response.status_code == 200
    assert "Ask the docs" in response.text


def test_stub_answers_a_matching_question(stub_app):
    response = ask(stub_app, QUESTION)

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "stub"
    assert body["answer"].startswith("[stub]")
    assert body["sources"][0]["file"] == "sourdough.txt"


def test_stub_falls_back_on_an_unrelated_question(stub_app):
    response = ask(stub_app, "Quantum chromodynamics gluons?")

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "I couldn't find anything relevant in the documents."
    assert body["sources"] == []


def test_proxy_returns_the_llm_answer(proxy_app, mock_llm):
    mock_llm.reset(status=200)

    response = ask(proxy_app, QUESTION)

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == mock_llm.ANSWER
    assert body["mode"] == "proxy"
    [request] = mock_llm.requests
    assert request["path"] == "/v1/chat/completions"
    assert request["authorization"] == f"Bearer {DUMMY_KEY}"
    assert request["body"]["model"] == MODEL
    prompt = request["body"]["messages"][-1]["content"]
    assert "sourdough.txt" in prompt
    assert "Feed the sourdough starter equal weights of flour and water" in prompt


def test_proxy_failure_is_reported(proxy_app, mock_llm):
    mock_llm.reset(status=500)

    response = ask(proxy_app, QUESTION)

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert len(mock_llm.requests) == 1
