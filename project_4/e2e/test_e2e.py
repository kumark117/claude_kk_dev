"""End to end: real Next.js and FastAPI processes talking over loopback HTTP.

Needs the frontend to be built first (`npm --prefix frontend run build`).
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

from mock_llm import CANNED_ANSWER, MockLLM

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
FIXTURE_DOCS = ROOT / "e2e" / "fixtures" / "docs"
STARTUP_TIMEOUT = 90
MATCHING_QUESTION = "How often does the Fixture Point lighthouse flash?"
EXPECTED_FILE = "lighthouse.md"
EXPECTED_TEXT = "The Fixture Point lighthouse flashes twice every ten seconds."
NO_MATCH_ANSWER = "I couldn't find anything relevant in the documents."
DUMMY_KEY = "dummy-e2e-key"
MODEL = "mock-model"


def clean_env(**extra):
    """The current environment without app settings or network proxies."""
    removed = {
        "DOCS_DIR", "BACKEND_URL",
        "LLM_PROXY_URL", "LLM_PROXY_API_KEY", "LLM_PROXY_MODEL",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    }
    env = {k: v for k, v in os.environ.items() if k.upper() not in removed}
    env["NEXT_TELEMETRY_DISABLED"] = "1"
    env.update(extra)
    return env


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def stop(process):
    """Stop the whole process group that the process leads."""
    for signum in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            break
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    process.wait(timeout=10)


@contextmanager
def server(command, cwd, env, ready_url):
    with tempfile.TemporaryFile() as log:

        def startup_failure(reason):
            log.seek(0)
            output = log.read().decode(errors="replace")
            pytest.fail(
                f"{reason}: {' '.join(command)}\n--- process output ---\n{output}",
                pytrace=False,
            )

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
            with httpx.Client(trust_env=False, timeout=10) as client:
                while True:
                    if process.poll() is not None:
                        startup_failure(f"Server exited with {process.returncode}")
                    try:
                        if client.get(ready_url).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() > deadline:
                        startup_failure("Server was not ready in time")
                    time.sleep(0.2)
            yield
        finally:
            stop(process)


@contextmanager
def app_stack(**backend_env):
    """Run the backend and the built frontend; yield a client for the frontend."""
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is not on PATH", pytrace=False)
    backend_port = free_port()
    backend_url = f"http://127.0.0.1:{backend_port}"
    backend_command = [
        sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
        "--app-dir", str(BACKEND),
        "--host", "127.0.0.1", "--port", str(backend_port),
    ]
    backend_settings = clean_env(DOCS_DIR=str(FIXTURE_DOCS), **backend_env)
    with server(backend_command, ROOT, backend_settings, f"{backend_url}/health"):
        frontend_port = free_port()
        frontend_url = f"http://127.0.0.1:{frontend_port}"
        frontend_command = [
            node, str(FRONTEND / "node_modules" / "next" / "dist" / "bin" / "next"),
            "start", "--hostname", "127.0.0.1", "--port", str(frontend_port),
        ]
        frontend_settings = clean_env(BACKEND_URL=backend_url)
        with server(frontend_command, FRONTEND, frontend_settings, f"{frontend_url}/"):
            with httpx.Client(
                base_url=frontend_url, trust_env=False, timeout=60
            ) as client:
                yield client


@pytest.fixture(scope="module", autouse=True)
def stop_servers_on_sigterm():
    """Turn SIGTERM into an interrupt, so that the fixtures still clean up."""

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupt)
    yield
    signal.signal(signal.SIGTERM, previous)


@pytest.fixture(scope="module")
def stub_app():
    with app_stack() as client:
        yield client


@pytest.fixture(scope="module")
def mock_server():
    with MockLLM() as mock:
        yield mock


@pytest.fixture(scope="module")
def proxy_app(mock_server):
    with app_stack(
        LLM_PROXY_URL=mock_server.url,
        LLM_PROXY_API_KEY=DUMMY_KEY,
        LLM_PROXY_MODEL=MODEL,
    ) as client:
        yield client


@pytest.fixture
def mock_llm(mock_server):
    mock_server.reset()
    return mock_server


def ask(client, question):
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
    assert body["sources"][0]["file"] == EXPECTED_FILE
    assert EXPECTED_TEXT in body["sources"][0]["text"]


def test_stub_falls_back_for_an_unrelated_question(stub_app):
    response = ask(stub_app, "Explain quantum chromodynamics")

    assert response.status_code == 200
    assert response.json() == {
        "answer": NO_MATCH_ANSWER,
        "sources": [],
        "mode": "stub",
    }


def test_proxy_returns_the_llm_answer(proxy_app, mock_llm):
    response = ask(proxy_app, MATCHING_QUESTION)

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == CANNED_ANSWER
    assert body["mode"] == "proxy"
    assert len(mock_llm.requests) == 1
    request = mock_llm.requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["headers"]["authorization"] == f"Bearer {DUMMY_KEY}"
    assert request["body"]["model"] == MODEL
    prompt = request["body"]["messages"][-1]["content"]
    assert EXPECTED_TEXT in prompt


def test_proxy_failure_gives_502(proxy_app, mock_llm):
    mock_llm.fail = True

    response = ask(proxy_app, MATCHING_QUESTION)

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert len(mock_llm.requests) == 1
