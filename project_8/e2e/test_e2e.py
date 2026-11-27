"""End to end: real Next.js and FastAPI processes talking over loopback HTTP.

Needs a production build of the frontend (`npm --prefix frontend run build`).
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
DOCS = ROOT / "e2e" / "fixtures" / "docs"
STARTUP_TIMEOUT = 60
DUMMY_KEY = "dummy-e2e-key"
MODEL = "e2e-model"
MATCHING = "How do honeybees make honey?"
UNRELATED = "Explain quantum chromodynamics"
ISOLATED = {
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
}


@pytest.fixture(scope="module", autouse=True)
def stop_servers_on_sigterm():
    """Turn SIGTERM into an interrupt, so that fixtures still clean up."""

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupt)
    yield
    signal.signal(signal.SIGTERM, previous)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def signal_group(process, signum):
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass


def wait_until_ready(process, url, log):
    deadline = time.monotonic() + STARTUP_TIMEOUT
    with httpx.Client(trust_env=False, timeout=5) as client:
        while process.poll() is None and time.monotonic() < deadline:
            try:
                if client.get(url).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
    log.seek(0)
    output = log.read().decode(errors="replace")
    state = (
        "timed out" if process.poll() is None else f"exited with {process.returncode}"
    )
    pytest.fail(f"Server at {url} {state}. Output:\n{output}", pytrace=False)


@contextmanager
def server(command, url, cwd, env):
    clean = {k: v for k, v in os.environ.items() if k not in ISOLATED}
    clean.update(env, NEXT_TELEMETRY_DISABLED="1")
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=clean,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            wait_until_ready(process, url, log)
            yield
        finally:
            signal_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            # Anything in the group that ignored SIGTERM.
            signal_group(process, signal.SIGKILL)
            process.wait()


@contextmanager
def stack(backend_env):
    """Start FastAPI and Next.js, and yield an HTTP client for Next.js."""
    backend_port = free_port()
    backend_url = f"http://127.0.0.1:{backend_port}"
    backend = [
        sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
        "--app-dir", str(ROOT / "backend"),
        "--host", "127.0.0.1", "--port", str(backend_port),
    ]  # fmt: skip
    env = {"DOCS_DIR": str(DOCS), **backend_env}
    with server(backend, f"{backend_url}/health", ROOT, env):
        frontend_port = free_port()
        frontend_url = f"http://127.0.0.1:{frontend_port}"
        frontend = [
            str(FRONTEND / "node_modules" / ".bin" / "next"), "start",
            "--hostname", "127.0.0.1", "--port", str(frontend_port),
        ]  # fmt: skip
        env = {"BACKEND_URL": backend_url}
        with server(frontend, f"{frontend_url}/", FRONTEND, env):
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
    env = {
        "LLM_PROXY_URL": f"{mock_llm.url}/v1",
        "LLM_PROXY_MODEL": MODEL,
        "LLM_PROXY_API_KEY": DUMMY_KEY,
    }
    with stack(env) as client:
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
    assert response.json() == {
        "answer": "I couldn't find anything relevant in the documents.",
        "sources": [],
        "mode": "stub",
    }


def test_proxy_returns_the_llm_answer(proxy_app, mock_llm):
    response = ask(proxy_app, MATCHING)

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == CANNED_ANSWER
    assert body["mode"] == "proxy"
    [request] = mock_llm.requests
    assert request["path"] == "/v1/chat/completions"
    assert request["authorization"] == f"Bearer {DUMMY_KEY}"
    assert request["body"]["model"] == MODEL
    prompt = request["body"]["messages"][-1]["content"]
    assert body["sources"][0]["text"] in prompt


def test_proxy_failure_gives_502(proxy_app, mock_llm):
    mock_llm.fail = True

    response = ask(proxy_app, MATCHING)

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert len(mock_llm.requests) == 1
