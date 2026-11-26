"""Real Next.js and FastAPI processes, talking over loopback HTTP.

Needs a production build of the frontend (`npm --prefix frontend run build`).
"""

import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from mock_llm import CANNED_ANSWER, MockLLM

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
FIXTURE_DOCS = ROOT / "e2e" / "fixtures" / "docs"
STARTUP_SECONDS = 120
FALLBACK = "I couldn't find anything relevant in the documents."
QUESTION = "How does a lighthouse send its beam?"
DUMMY_KEY = "e2e-dummy-key"
ISOLATED = {
    "DOCS_DIR",
    "BACKEND_URL",
    "LLM_PROXY_URL",
    "LLM_PROXY_API_KEY",
    "LLM_PROXY_MODEL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
}


@pytest.fixture(scope="module", autouse=True)
def clean_up_when_terminated() -> Iterator[None]:
    """Turn termination signals into an exception so that fixtures still clean up."""

    def interrupt(signum: int, frame: object) -> None:
        raise KeyboardInterrupt(f"received signal {signum}")

    signals = (signal.SIGTERM, signal.SIGHUP)
    previous = [signal.signal(number, interrupt) for number in signals]
    yield
    for number, handler in zip(signals, previous):
        signal.signal(number, handler)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def server_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() not in ISOLATED}
    return {**env, "NEXT_TELEMETRY_DISABLED": "1", **extra}


def stop(process: subprocess.Popen) -> None:
    """Stop the whole process group that this test started."""
    for number in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, number)
        except ProcessLookupError:
            break
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            continue
    process.wait(timeout=10)


@contextmanager
def server(
    command: list[str], cwd: Path, env: dict[str, str], ready_url: str
) -> Iterator[None]:
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
            deadline = time.monotonic() + STARTUP_SECONDS
            with httpx.Client(trust_env=False, timeout=5) as client:
                while True:
                    if process.poll() is None:
                        try:
                            if client.get(ready_url).status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                    if process.poll() is not None or time.monotonic() > deadline:
                        log.seek(0)
                        output = log.read().decode(errors="replace")
                        pytest.fail(
                            f"Server did not start: {' '.join(command)}\n{output}",
                            pytrace=False,
                        )
                    time.sleep(0.2)
            yield
        finally:
            stop(process)


@contextmanager
def stack(**backend_env: str) -> Iterator[str]:
    """Start a backend and a Next.js server pointed at it; yield the UI address."""
    backend_port = free_port()
    backend_url = f"http://127.0.0.1:{backend_port}"
    backend_command = [
        sys.executable, "-m", "uvicorn", "--factory", "app.main:create_app",
        "--app-dir", "backend",
        "--host", "127.0.0.1", "--port", str(backend_port),
    ]  # fmt: skip
    env = server_env(DOCS_DIR=str(FIXTURE_DOCS), **backend_env)
    with server(backend_command, ROOT, env, f"{backend_url}/health"):
        node = shutil.which("node")
        assert node, "node is not on PATH"
        ui_port = free_port()
        ui_url = f"http://127.0.0.1:{ui_port}"
        ui_command = [
            node, "node_modules/next/dist/bin/next", "start",
            "--hostname", "127.0.0.1", "--port", str(ui_port),
        ]  # fmt: skip
        ui_env = server_env(BACKEND_URL=backend_url)
        with server(ui_command, FRONTEND, ui_env, f"{ui_url}/"):
            yield ui_url


@pytest.fixture(scope="module")
def stub_ui() -> Iterator[httpx.Client]:
    with stack() as url, httpx.Client(base_url=url, trust_env=False, timeout=40) as c:
        yield c


@pytest.fixture(scope="module")
def mock_server() -> Iterator[MockLLM]:
    with MockLLM() as mock:
        yield mock


@pytest.fixture
def mock_llm(mock_server: MockLLM) -> MockLLM:
    mock_server.reset()
    return mock_server


@pytest.fixture(scope="module")
def proxy_ui(mock_server: MockLLM) -> Iterator[httpx.Client]:
    with (
        stack(
            LLM_PROXY_URL=mock_server.base_url,
            LLM_PROXY_MODEL="e2e-model",
            LLM_PROXY_API_KEY=DUMMY_KEY,
        ) as url,
        httpx.Client(base_url=url, trust_env=False, timeout=40) as client,
    ):
        yield client


def test_home_page_is_served(stub_ui):
    response = stub_ui.get("/")

    assert response.status_code == 200
    assert "Ask the docs" in response.text


def test_stub_answers_a_matching_question(stub_ui):
    response = stub_ui.post("/api/ask", json={"question": QUESTION})

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "stub"
    assert body["answer"].startswith("[stub]")
    assert body["sources"][0]["file"] == "lighthouse.md"


def test_stub_falls_back_for_an_unrelated_question(stub_ui):
    response = stub_ui.post("/api/ask", json={"question": "quantum chromodynamics"})

    assert response.status_code == 200
    assert response.json() == {"answer": FALLBACK, "sources": [], "mode": "stub"}


def test_proxy_returns_the_mock_answer(proxy_ui, mock_llm):
    response = proxy_ui.post("/api/ask", json={"question": QUESTION})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == CANNED_ANSWER
    assert body["mode"] == "proxy"
    [request] = mock_llm.requests
    assert request["path"] == "/v1/chat/completions"
    assert request["authorization"] == f"Bearer {DUMMY_KEY}"
    assert request["body"]["model"] == "e2e-model"
    prompt = request["body"]["messages"][-1]["content"]
    assert body["sources"][0]["text"] in prompt
    assert "rotating Fresnel lens" in prompt


def test_proxy_failure_is_reported_as_502(proxy_ui, mock_llm):
    mock_llm.fail = True

    response = proxy_ui.post("/api/ask", json={"question": QUESTION})

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert len(mock_llm.requests) == 1
