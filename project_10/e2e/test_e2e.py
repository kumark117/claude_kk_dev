"""End to end: real Next.js and FastAPI processes talking over loopback HTTP.

Needs a Next.js build (`npm --prefix frontend run build`); test.sh makes one.
"""

import contextlib
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

from mock_llm import CANNED_ANSWER, MockLLM

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
DOCS = ROOT / "e2e" / "fixtures" / "docs"
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


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() not in APP_VARIABLES}
    return {**env, "NEXT_TELEMETRY_DISABLED": "1", **extra}


def signal_group(process: subprocess.Popen, signum: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signum)


def wait_until_ready(process, url: str) -> bool:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    with httpx.Client(trust_env=False, timeout=5) as client:
        while time.monotonic() < deadline and process.poll() is None:
            with contextlib.suppress(httpx.HTTPError):
                if client.get(url).status_code == 200:
                    return process.poll() is None
            time.sleep(0.1)
    return False


@contextlib.contextmanager
def server(command: list[str], ready_url: str, cwd: Path, env: dict[str, str]):
    """Run a server in its own process group and always stop the whole group."""
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
            if not wait_until_ready(process, ready_url):
                log.seek(0)
                output = log.read().decode(errors="replace")
                pytest.fail(
                    f"Server did not become ready: {' '.join(command)}\n"
                    f"exit code: {process.poll()}\n{output}"
                )
            yield
        finally:
            signal_group(process, signal.SIGTERM)
            try:
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=10)
            finally:
                signal_group(process, signal.SIGKILL)
                process.wait()


@contextlib.contextmanager
def app_stack(**backend_env: str):
    """Start FastAPI and Next.js; yield the Next.js base URL."""
    backend_port, frontend_port = free_port(), free_port()
    backend_url = f"http://127.0.0.1:{backend_port}"
    frontend_url = f"http://127.0.0.1:{frontend_port}"
    backend = [
        sys.executable, "-m", "uvicorn", "--factory", "app.main:create_app",
        "--app-dir", "backend", "--host", "127.0.0.1", "--port", str(backend_port),
    ]
    frontend = [
        str(FRONTEND / "node_modules" / ".bin" / "next"), "start",
        "-H", "127.0.0.1", "-p", str(frontend_port),
    ]
    with server(
        backend,
        f"{backend_url}/health",
        ROOT,
        clean_env(DOCS_DIR=str(DOCS), **backend_env),
    ):
        with server(
            frontend,
            f"{frontend_url}/",
            FRONTEND,
            clean_env(BACKEND_URL=backend_url),
        ):
            yield frontend_url


@pytest.fixture(scope="module", autouse=True)
def stop_servers_on_sigterm():
    """Turn SIGTERM into an interrupt so that fixture teardown still runs."""

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupt)
    yield
    signal.signal(signal.SIGTERM, previous)


@pytest.fixture(scope="module", autouse=True)
def require_build():
    if not (FRONTEND / ".next" / "BUILD_ID").is_file():
        pytest.fail("No Next.js build. Run: npm --prefix frontend run build")


@contextlib.contextmanager
def http_client(base_url: str):
    with httpx.Client(base_url=base_url, trust_env=False, timeout=60) as client:
        yield client


@pytest.fixture(scope="module")
def stub_client():
    with app_stack() as url, http_client(url) as client:
        yield client


@pytest.fixture(scope="module")
def mock_server():
    with MockLLM() as mock:
        yield mock


@pytest.fixture(scope="module")
def proxy_client(mock_server):
    with app_stack(
        LLM_PROXY_URL=mock_server.base_url,
        LLM_PROXY_MODEL="e2e-model",
        LLM_PROXY_API_KEY="e2e-dummy-key",
    ) as url, http_client(url) as client:
        yield client


@pytest.fixture
def mock_llm(mock_server):
    mock_server.requests.clear()
    mock_server.fail = False
    return mock_server


def ask(client: httpx.Client, question: str) -> httpx.Response:
    return client.post("/api/ask", json={"question": question})


def test_page_is_served(stub_client):
    response = stub_client.get("/")

    assert response.status_code == 200
    assert "Ask the docs" in response.text


def test_stub_answers_a_matching_question(stub_client):
    response = ask(stub_client, "When does a volcano erupt with lava?")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "stub"
    assert body["answer"].startswith("[stub]")
    assert body["sources"][0]["file"] == "volcanoes.md"


def test_stub_falls_back_for_an_unrelated_question(stub_client):
    response = ask(stub_client, "quarterly tax spreadsheet")

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "I couldn't find anything relevant in the documents."
    assert body["sources"] == []


def test_proxy_returns_the_llm_answer(proxy_client, mock_llm):
    response = ask(proxy_client, "How does castling work in chess?")

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == CANNED_ANSWER
    assert body["mode"] == "proxy"
    assert len(mock_llm.requests) == 1
    request = mock_llm.requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["authorization"] == "Bearer e2e-dummy-key"
    assert request["body"]["model"] == "e2e-model"
    prompt = request["body"]["messages"][-1]["content"]
    assert "castling moves the king two squares" in prompt


def test_proxy_failure_is_reported_as_502(proxy_client, mock_llm):
    mock_llm.fail = True

    response = ask(proxy_client, "How does castling work in chess?")

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert len(mock_llm.requests) == 1
