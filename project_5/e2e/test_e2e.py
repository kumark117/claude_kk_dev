"""Real Uvicorn and "next start" processes, talked to over loopback HTTP.

Needs the production build of the frontend, which ./test.sh makes first.
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

from mock_llm import ANSWER, MockLLM

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
FIXTURE_DOCS = ROOT / "e2e" / "fixtures" / "docs"
STARTUP_TIMEOUT = 60.0
SHUTDOWN_TIMEOUT = 10.0
DUMMY_KEY = "dummy-e2e-key"
MODEL = "mock-model"
QUESTION = "How do I feed a sourdough starter?"
EXPECTED_FILE = "sourdough.txt"
EXPECTED_TEXT = "Feed a sourdough starter with equal weights of flour and water."
FALLBACK = "I couldn't find anything relevant in the documents."


@pytest.fixture(scope="module", autouse=True)
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


def clean_environment(**extra: str) -> dict[str, str]:
    removed = {
        "DOCS_DIR",
        "BACKEND_URL",
        "LLM_PROXY_URL",
        "LLM_PROXY_API_KEY",
        "LLM_PROXY_MODEL",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
    }
    env = {k: v for k, v in os.environ.items() if k.upper() not in removed}
    env["NEXT_TELEMETRY_DISABLED"] = "1"
    env.update(extra)
    return env


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def stop_group(process: subprocess.Popen) -> None:
    """Stop the process and everything it started; it leads its own group."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + SHUTDOWN_TIMEOUT
        while time.monotonic() < deadline:
            process.poll()  # reap the leader so that the group can empty
            os.killpg(process.pid, 0)
            time.sleep(0.05)
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=SHUTDOWN_TIMEOUT)


def wait_until_ready(process, url: str, output) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    problem = "no reply yet"
    with httpx.Client(trust_env=False, timeout=5) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                problem = f"process exited with status {process.returncode}"
                break
            try:
                response = client.get(url)
                if response.status_code == 200:
                    return
                problem = f"status {response.status_code}"
            except httpx.HTTPError as error:
                problem = repr(error)
            time.sleep(0.2)
    output.seek(0)
    pytest.fail(
        f"Server did not become ready at {url}: {problem}\n"
        f"Command: {process.args}\n--- output ---\n{output.read()}"
    )


@contextmanager
def server(command: list[str], cwd: Path, env: dict[str, str], ready_url: str):
    with tempfile.TemporaryFile("w+", errors="replace") as output:
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


@contextmanager
def stack(**backend_env: str):
    """Run a backend and a frontend that forwards to it; yield an HTTP client."""
    node = shutil.which("node")
    assert node, "node is not on PATH"
    assert (FRONTEND / ".next" / "BUILD_ID").is_file(), (
        "No frontend build found. Run ./test.sh, which builds it first."
    )

    backend_port = free_port()
    backend_url = f"http://127.0.0.1:{backend_port}"
    backend_command = [
        sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
        "--app-dir", str(ROOT / "backend"),
        "--host", "127.0.0.1", "--port", str(backend_port),
    ]  # fmt: skip
    env = clean_environment(DOCS_DIR=str(FIXTURE_DOCS), **backend_env)

    with server(backend_command, ROOT, env, f"{backend_url}/health"):
        # Chosen only now, so it cannot collide with the backend's port.
        frontend_port = free_port()
        frontend_url = f"http://127.0.0.1:{frontend_port}"
        frontend_command = [
            node, str(FRONTEND / "node_modules" / "next" / "dist" / "bin" / "next"),
            "start", "--hostname", "127.0.0.1", "--port", str(frontend_port),
        ]  # fmt: skip
        env = clean_environment(BACKEND_URL=backend_url)
        with server(frontend_command, FRONTEND, env, f"{frontend_url}/"):
            with httpx.Client(
                base_url=frontend_url, trust_env=False, timeout=60
            ) as client:
                yield client


@pytest.fixture(scope="module")
def stub_client():
    with stack() as client:
        yield client


@pytest.fixture(scope="module")
def proxy():
    with MockLLM() as mock:
        with stack(
            LLM_PROXY_URL=mock.url,
            LLM_PROXY_MODEL=MODEL,
            LLM_PROXY_API_KEY=DUMMY_KEY,
        ) as client:
            yield client, mock


@pytest.fixture
def proxy_client(proxy):
    client, mock = proxy
    mock.fail = False
    mock.requests.clear()
    return client, mock


def ask(client: httpx.Client, question: str) -> httpx.Response:
    return client.post("/api/ask", json={"question": question})


def test_page_is_served(stub_client):
    response = stub_client.get("/")

    assert response.status_code == 200
    assert "Ask the docs" in response.text


def test_stub_answers_a_matching_question(stub_client):
    response = ask(stub_client, QUESTION)

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "stub"
    assert body["answer"].startswith("[stub]")
    assert body["sources"][0]["file"] == EXPECTED_FILE


def test_stub_gives_the_fallback_for_an_unrelated_question(stub_client):
    response = ask(stub_client, "Tell me about quantum chromodynamics")

    assert response.status_code == 200
    assert response.json() == {"answer": FALLBACK, "sources": [], "mode": "stub"}


def test_proxy_returns_the_llm_answer(proxy_client):
    client, mock = proxy_client

    response = ask(client, QUESTION)

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == ANSWER
    assert body["mode"] == "proxy"
    assert len(mock.requests) == 1
    received = mock.requests[0]
    assert received["path"] == "/v1/chat/completions"
    assert received["authorization"] == f"Bearer {DUMMY_KEY}"
    assert received["body"]["model"] == MODEL
    assert EXPECTED_TEXT in received["body"]["messages"][-1]["content"]


def test_proxy_failure_becomes_502(proxy_client):
    client, mock = proxy_client
    mock.fail = True

    response = ask(client, QUESTION)

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert len(mock.requests) == 1
