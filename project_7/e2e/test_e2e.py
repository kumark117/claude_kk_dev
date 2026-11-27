"""End to end: real Next.js and FastAPI processes, talking over loopback HTTP.

Needs a finished frontend build (test.sh runs `npm run build` first).
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
DOCS = ROOT / "e2e" / "fixtures" / "docs"
STARTUP_TIMEOUT = 60
FALLBACK = "I couldn't find anything relevant in the documents."
MATCHING_QUESTION = "How do volcanoes erupt?"
ISOLATED_VARIABLES = {
    "DOCS_DIR",
    "BACKEND_URL",
    "LLM_PROXY_URL",
    "LLM_PROXY_API_KEY",
    "LLM_PROXY_MODEL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
}


def clean_env(**extra: str) -> dict[str, str]:
    env = {
        name: value
        for name, value in os.environ.items()
        if name.upper() not in ISOLATED_VARIABLES
    }
    return {**env, "NEXT_TELEMETRY_DISABLED": "1", **extra}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def stop_group(process: subprocess.Popen) -> None:
    """Stop the process and everything it started."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            continue
    process.wait(timeout=10)


@contextmanager
def server(command: list[str], cwd: Path, env: dict[str, str], ready_url: str):
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
            deadline = time.monotonic() + STARTUP_TIMEOUT
            with httpx.Client(trust_env=False, timeout=5) as client:
                while True:
                    if process.poll() is not None:
                        problem = f"exited with code {process.returncode}"
                        break
                    try:
                        if client.get(ready_url).status_code == 200:
                            problem = None
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() > deadline:
                        problem = f"was not ready after {STARTUP_TIMEOUT} seconds"
                        break
                    time.sleep(0.2)
            if problem:
                log.seek(0)
                output = log.read().decode("utf-8", errors="replace")
                pytest.fail(f"{command} {problem}. Output:\n{output}", pytrace=False)
            yield
        finally:
            stop_group(process)


@contextmanager
def running_app(**backend_env: str):
    """Start FastAPI and Next.js; yield an HTTP client pointed at Next.js."""
    backend_port = free_port()
    backend_url = f"http://127.0.0.1:{backend_port}"
    backend = [
        sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
        "--app-dir", str(ROOT / "backend"),
        "--host", "127.0.0.1", "--port", str(backend_port),
    ]  # fmt: skip
    env = clean_env(DOCS_DIR=str(DOCS), **backend_env)
    with server(backend, ROOT, env, f"{backend_url}/health"):
        port = free_port()
        url = f"http://127.0.0.1:{port}"
        frontend = [
            str(FRONTEND / "node_modules" / ".bin" / "next"), "start",
            "-H", "127.0.0.1", "-p", str(port),
        ]  # fmt: skip
        env = clean_env(BACKEND_URL=backend_url)
        with server(frontend, FRONTEND, env, f"{url}/"):
            with httpx.Client(base_url=url, trust_env=False, timeout=40) as client:
                yield client


@pytest.fixture(scope="module", autouse=True)
def clean_up_on_termination():
    """Turn SIGTERM and SIGHUP into an interrupt so that servers are stopped."""

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    previous = {
        sig: signal.signal(sig, interrupt)
        for sig in (signal.SIGTERM, signal.SIGHUP)
    }
    yield
    for sig, handler in previous.items():
        signal.signal(sig, handler)


@pytest.fixture(scope="module")
def stub_app():
    with running_app() as client:
        yield client


@pytest.fixture(scope="module")
def mock_server():
    with MockLLM() as mock:
        yield mock


@pytest.fixture(scope="module")
def proxy_app(mock_server):
    with running_app(
        LLM_PROXY_URL=f"{mock_server.url}/v1",
        LLM_PROXY_API_KEY="dummy-key",
        LLM_PROXY_MODEL="dummy-model",
    ) as client:
        yield client


@pytest.fixture
def mock_llm(mock_server):
    mock_server.reset()
    return mock_server


def ask(client: httpx.Client, question: str) -> httpx.Response:
    return client.post("/api/ask", json={"question": question})


def test_home_page(stub_app):
    response = stub_app.get("/")

    assert response.status_code == 200
    assert "Ask the docs" in response.text


def test_stub_answers_a_matching_question(stub_app):
    response = ask(stub_app, MATCHING_QUESTION)

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "stub"
    assert body["answer"].startswith("[stub]")
    assert body["sources"][0]["file"] == "volcanoes.md"


def test_stub_gives_the_fallback_for_an_unrelated_question(stub_app):
    response = ask(stub_app, "Explain quantum chromodynamics")

    assert response.status_code == 200
    assert response.json() == {"answer": FALLBACK, "sources": [], "mode": "stub"}


def test_proxy_returns_the_llm_answer(proxy_app, mock_llm):
    response = ask(proxy_app, MATCHING_QUESTION)

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == ANSWER
    assert body["mode"] == "proxy"
    [request] = mock_llm.requests
    assert request["path"] == "/v1/chat/completions"
    assert request["authorization"] == "Bearer dummy-key"
    assert request["body"]["model"] == "dummy-model"
    prompt = request["body"]["messages"][-1]["content"]
    assert body["sources"][0]["text"] in prompt
    assert "molten rock" in prompt


def test_proxy_failure_gives_502(proxy_app, mock_llm):
    mock_llm.fail = True

    response = ask(proxy_app, MATCHING_QUESTION)

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert len(mock_llm.requests) == 1
