"""Real Next.js and Uvicorn processes, talking over loopback HTTP.

Needs the Next.js build made by `npm --prefix frontend run build`.
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
NEXT = FRONTEND / "node_modules" / ".bin" / "next"
FALLBACK = "I couldn't find anything relevant in the documents."
KEY = "dummy-e2e-key"
MODEL = "dummy-e2e-model"
STARTUP_TIMEOUT = 60
SETTINGS = (
    "DOCS_DIR",
    "BACKEND_URL",
    "LLM_PROXY_URL",
    "LLM_PROXY_API_KEY",
    "LLM_PROXY_MODEL",
)


@pytest.fixture(scope="session", autouse=True)
def stop_servers_when_terminated():
    """Turn SIGTERM and SIGHUP into an interrupt, so fixtures still clean up."""

    def interrupt(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    previous = {
        number: signal.signal(number, interrupt)
        for number in (signal.SIGTERM, signal.SIGHUP)
    }
    yield
    for number, handler in previous.items():
        signal.signal(number, handler)


def clean_env(**settings):
    env = {
        name: value
        for name, value in os.environ.items()
        if name not in SETTINGS and not name.lower().endswith("_proxy")
    }
    return {**env, "NEXT_TELEMETRY_DISABLED": "1", **settings}


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def signal_group(process, number):
    try:
        os.killpg(process.pid, number)
    except ProcessLookupError:
        pass


def stop(process):
    """Stop the whole process group, including any children left behind."""
    signal_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    finally:
        signal_group(process, signal.SIGKILL)
        process.wait()


def wait_until_ready(name, process, url, log):
    deadline = time.monotonic() + STARTUP_TIMEOUT
    with httpx.Client(trust_env=False, timeout=5) as client:
        while time.monotonic() < deadline and process.poll() is None:
            try:
                if client.get(url).status_code == 200:
                    # Still running, so the reply did not come from another process.
                    if process.poll() is None:
                        return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
    log.seek(0)
    output = log.read().decode(errors="replace")
    pytest.fail(f"{name} did not start. Output:\n{output}", pytrace=False)


@contextmanager
def server(name, command, port, ready_path, env, cwd):
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
            url = f"http://127.0.0.1:{port}"
            wait_until_ready(name, process, url + ready_path, log)
            yield url
        finally:
            stop(process)


@contextmanager
def stack(**backend_settings):
    """Start the backend and the frontend; yield a client for the frontend."""
    backend_port = free_port()
    backend_command = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:create_app",
        "--factory",
        "--app-dir",
        str(ROOT / "backend"),
        "--host",
        "127.0.0.1",
        "--port",
        str(backend_port),
    ]
    backend_env = clean_env(DOCS_DIR=str(DOCS), **backend_settings)
    with server(
        "Backend", backend_command, backend_port, "/health", backend_env, ROOT
    ) as backend_url:
        port = free_port()
        command = [str(NEXT), "start", "-H", "127.0.0.1", "-p", str(port)]
        env = clean_env(BACKEND_URL=backend_url)
        with server("Frontend", command, port, "/", env, FRONTEND) as url:
            with httpx.Client(base_url=url, trust_env=False, timeout=60) as client:
                yield client


@pytest.fixture(scope="module", autouse=True)
def built_frontend():
    if not (FRONTEND / ".next" / "BUILD_ID").is_file():
        pytest.fail(
            "No Next.js build. Run: npm --prefix frontend run build", pytrace=False
        )


@pytest.fixture(scope="module")
def stub_client():
    with stack() as client:
        yield client


@pytest.fixture(scope="module")
def mock_llm():
    with MockLLM() as mock:
        yield mock


@pytest.fixture(scope="module")
def proxy_client(mock_llm):
    with stack(
        LLM_PROXY_URL=mock_llm.url, LLM_PROXY_API_KEY=KEY, LLM_PROXY_MODEL=MODEL
    ) as client:
        yield client


@pytest.fixture
def mock(mock_llm):
    mock_llm.reset()
    return mock_llm


def ask(client, question):
    return client.post("/api/ask", json={"question": question})


def test_home_page(stub_client):
    response = stub_client.get("/")

    assert response.status_code == 200
    assert "Ask the docs" in response.text


def test_stub_matching_question(stub_client):
    response = ask(stub_client, "What is a moraine?")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "stub"
    assert body["answer"].startswith("[stub]")
    assert body["sources"][0]["file"] == "glaciers.md"


def test_stub_unrelated_question(stub_client):
    response = ask(stub_client, "Quarterly tax deadlines?")

    assert response.status_code == 200
    assert response.json() == {"answer": FALLBACK, "sources": [], "mode": "stub"}


def test_proxy_success(proxy_client, mock):
    response = ask(proxy_client, "What does a beekeeper use a smoker for?")

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == ANSWER
    assert body["mode"] == "proxy"
    [request] = mock.requests
    assert request.path == "/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.body["model"] == MODEL
    prompt = request.body["messages"][-1]["content"]
    assert body["sources"][0]["file"] == "beekeeping.txt"
    assert body["sources"][0]["text"] in prompt


def test_proxy_failure(proxy_client, mock):
    mock.fail = True

    response = ask(proxy_client, "What does a beekeeper use a smoker for?")

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert len(mock.requests) == 1
