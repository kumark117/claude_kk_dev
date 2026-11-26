"""End to end: real Next.js and FastAPI processes talking over loopback HTTP.

Needs a Next.js production build (`npm --prefix frontend run build`).
"""

import os
import shutil
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

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DOCS = ROOT / "e2e" / "fixtures" / "docs"
STARTUP_TIMEOUT = 90
ISOLATED_VARIABLES = (
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


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in ISOLATED_VARIABLES}
    return {**env, "NEXT_TELEMETRY_DISABLED": "1", **extra}


def stop_group(process):
    """Stop the process and everything it started."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        pass
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


@contextmanager
def server(command, *, cwd, env, ready_url):
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
            stop_group(process)


def wait_until_ready(process, url, log):
    deadline = time.monotonic() + STARTUP_TIMEOUT
    with httpx.Client(trust_env=False, timeout=5) as client:
        # Checking the process first avoids accepting someone else's server.
        while process.poll() is None and time.monotonic() < deadline:
            try:
                if client.get(url).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
    log.seek(0)
    output = log.read().decode(errors="replace")
    pytest.fail(
        f"Server at {url} did not start (exit code {process.poll()}):\n{output}",
        pytrace=False,
    )


@contextmanager
def running_app(**backend_env):
    """Start the backend and the frontend; yield a client for the frontend."""
    node = shutil.which("node")
    assert node, "node is not on PATH"
    backend_port, frontend_port = free_port(), free_port()
    backend_url = f"http://127.0.0.1:{backend_port}"
    frontend_url = f"http://127.0.0.1:{frontend_port}"
    backend = server(
        [
            sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
            "--host", "127.0.0.1", "--port", str(backend_port),
        ],
        cwd=ROOT / "backend",
        env=clean_env(DOCS_DIR=str(FIXTURE_DOCS), **backend_env),
        ready_url=f"{backend_url}/health",
    )
    frontend = server(
        [
            node, "node_modules/next/dist/bin/next", "start",
            "--hostname", "127.0.0.1", "--port", str(frontend_port),
        ],
        cwd=ROOT / "frontend",
        env=clean_env(BACKEND_URL=backend_url),
        ready_url=f"{frontend_url}/",
    )
    with backend, frontend:
        with httpx.Client(
            base_url=frontend_url, trust_env=False, timeout=40
        ) as client:
            yield client


@pytest.fixture(scope="module", autouse=True)
def stop_servers_on_signals():
    """Turn termination signals into an interrupt, so fixtures still clean up."""

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    previous = {
        s: signal.signal(s, interrupt) for s in (signal.SIGTERM, signal.SIGHUP)
    }
    yield
    for signum, handler in previous.items():
        signal.signal(signum, handler)


@pytest.fixture(scope="module")
def stub_app():
    with running_app() as client:
        yield client


@pytest.fixture(scope="module")
def mock_llm():
    with MockLLM() as mock:
        yield mock


@pytest.fixture(scope="module")
def proxy_app(mock_llm):
    with running_app(
        LLM_PROXY_URL=mock_llm.url,
        LLM_PROXY_API_KEY="dummy-key",
        LLM_PROXY_MODEL="dummy-model",
    ) as client:
        yield client


def ask(client, question):
    return client.post("/api/ask", json={"question": question})


def test_page_is_served(stub_app):
    response = stub_app.get("/")

    assert response.status_code == 200
    assert "Ask the docs" in response.text


def test_stub_answers_a_matching_question(stub_app):
    response = ask(stub_app, "Why does the moon cause tides?")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "stub"
    assert body["answer"].startswith("[stub]")
    assert body["sources"][0]["file"] == "tides.md"


def test_stub_falls_back_for_an_unrelated_question(stub_app):
    response = ask(stub_app, "Explain quantum chromodynamics")

    assert response.status_code == 200
    assert response.json() == {
        "answer": "I couldn't find anything relevant in the documents.",
        "sources": [],
        "mode": "stub",
    }


def test_proxy_returns_the_llm_answer(proxy_app, mock_llm):
    mock_llm.succeed("Canned answer from the mock.")

    response = ask(proxy_app, "How do honeybees make honey?")

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Canned answer from the mock."
    assert body["mode"] == "proxy"
    [request] = mock_llm.requests
    assert request["path"] == "/v1/chat/completions"
    assert request["authorization"] == "Bearer dummy-key"
    assert request["body"]["model"] == "dummy-model"
    prompt = request["body"]["messages"][-1]["content"]
    assert "bees.md" in prompt
    assert "turn it into honey inside the hive" in prompt


def test_proxy_failure_becomes_502(proxy_app, mock_llm):
    mock_llm.fail(500)

    response = ask(proxy_app, "How do honeybees make honey?")

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert len(mock_llm.requests) == 1
