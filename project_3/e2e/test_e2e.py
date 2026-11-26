"""End to end: real Next.js and FastAPI processes talking over HTTP."""

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
NO_MATCH = "I couldn't find anything relevant in the documents."
STARTUP_TIMEOUT = 60
ISOLATED = (
    "DOCS_DIR", "BACKEND_URL", "LLM_PROXY_URL", "LLM_PROXY_API_KEY",
    "LLM_PROXY_MODEL", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
)


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in ISOLATED}
    return {**env, "NEXT_TELEMETRY_DISABLED": "1", "PYTHONUNBUFFERED": "1", **extra}


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def stop_group(process):
    """Stop the server and anything it spawned."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            continue
        if sig == signal.SIGTERM:
            time.sleep(0.2)  # grace period for children before the final SIGKILL
    process.wait(timeout=10)


@contextmanager
def server(name, command, *, cwd, env, port, ready_line, path):
    """Run a server in its own process group until the block ends.

    Ready means our process logged that it is listening *and* answers over HTTP,
    so another program that grabbed the port cannot be mistaken for it.
    """
    url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
        try:
            deadline = time.monotonic() + STARTUP_TIMEOUT
            with httpx.Client(trust_env=False, timeout=5) as client:
                while True:
                    log.seek(0)
                    output = log.read().decode(errors="replace")
                    if process.poll() is not None:
                        pytest.fail(f"{name} exited with {process.returncode}:\n{output}")
                    if ready_line in output:
                        try:
                            if client.get(url + path).status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                    if time.monotonic() > deadline:
                        pytest.fail(f"{name} was not ready in time:\n{output}")
                    time.sleep(0.1)
            yield url
        finally:
            stop_group(process)


@contextmanager
def stack(**backend_env):
    """Start FastAPI on the fixture documents and Next.js in front of it."""
    backend_port = free_port()
    backend_command = [
        sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
        "--app-dir", "backend", "--host", "127.0.0.1", "--port", str(backend_port),
    ]
    with server(
        "FastAPI", backend_command, cwd=ROOT,
        env=clean_env(DOCS_DIR=str(DOCS), **backend_env),
        port=backend_port, ready_line="Uvicorn running on", path="/health",
    ) as backend_url:
        web_port = free_port()
        web_command = [
            str(FRONTEND / "node_modules" / ".bin" / "next"), "start",
            "--hostname", "127.0.0.1", "--port", str(web_port),
        ]
        with server(
            "Next.js", web_command, cwd=FRONTEND,
            env=clean_env(BACKEND_URL=backend_url),
            port=web_port, ready_line="Ready", path="/",
        ) as web_url:
            with httpx.Client(base_url=web_url, trust_env=False, timeout=45) as client:
                yield client


@pytest.fixture(scope="module", autouse=True)
def stop_servers_when_terminated():
    """Turn SIGTERM and SIGHUP into an interrupt so cleanup still runs."""

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    previous = {s: signal.signal(s, interrupt) for s in (signal.SIGTERM, signal.SIGHUP)}
    yield
    for signum, handler in previous.items():
        signal.signal(signum, handler)


@pytest.fixture(scope="module")
def stub_app():
    with stack() as client:
        yield client


@pytest.fixture(scope="module")
def mock_llm():
    mock = MockLLM()
    mock.start()
    yield mock
    mock.stop()


@pytest.fixture(scope="module")
def proxy_app(mock_llm):
    with stack(
        LLM_PROXY_URL=f"{mock_llm.url}/v1/",
        LLM_PROXY_API_KEY="dummy-key",
        LLM_PROXY_MODEL="mock-model",
    ) as client:
        yield client


def ask(client, question):
    return client.post("/api/ask", json={"question": question})


def test_home_page(stub_app):
    response = stub_app.get("/")

    assert response.status_code == 200
    assert "Ask the docs" in response.text


def test_stub_matching_question(stub_app):
    response = ask(stub_app, "How often does the lighthouse flash its beam?")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "stub"
    assert body["answer"].startswith("[stub]")
    assert body["sources"][0]["file"] == "lighthouse.md"


def test_stub_unrelated_question(stub_app):
    response = ask(stub_app, "Explain quantum chromodynamics")

    assert response.status_code == 200
    assert response.json() == {"answer": NO_MATCH, "sources": [], "mode": "stub"}


def test_proxy_success(proxy_app, mock_llm):
    mock_llm.reset()

    response = ask(proxy_app, "How do honeybees survive winter?")

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == CANNED_ANSWER
    assert body["mode"] == "proxy"
    (request,) = mock_llm.requests
    assert request["path"] == "/v1/chat/completions"
    assert request["authorization"] == "Bearer dummy-key"
    assert request["body"]["model"] == "mock-model"
    assert body["sources"][0]["file"] == "beekeeping.txt"
    assert body["sources"][0]["text"] in request["body"]["messages"][1]["content"]


def test_proxy_failure(proxy_app, mock_llm):
    mock_llm.reset()
    mock_llm.fail = True

    response = ask(proxy_app, "How do honeybees survive winter?")

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert len(mock_llm.requests) == 1
