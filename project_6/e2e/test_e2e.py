"""End to end: real `next start` and Uvicorn processes, over loopback HTTP.

Needs a finished frontend build (`npm --prefix frontend run build`).
"""

import contextlib
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

from mock_llm import ANSWER, MockLLM

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
DOCS = ROOT / "e2e" / "fixtures" / "docs"
STARTUP_TIMEOUT = 60
FALLBACK = "I couldn't find anything relevant in the documents."
DUMMY_KEY = "dummy-e2e-key"
MODEL = "mock-model"
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
def stop_servers_on_termination():
    """Turn SIGTERM and SIGHUP into an interrupt, so fixtures still clean up."""

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signals = (signal.SIGTERM, signal.SIGHUP)
    previous = [signal.signal(s, interrupt) for s in signals]
    yield
    for s, handler in zip(signals, previous):
        signal.signal(s, handler)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def server_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ISOLATED}
    env["NEXT_TELEMETRY_DISABLED"] = "1"
    env.update(extra)
    return env


def signal_group(process: subprocess.Popen, signum: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signum)


def wait_until_ready(process: subprocess.Popen, url: str, output) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    with httpx.Client(trust_env=False, timeout=5) as client:
        while process.poll() is None and time.monotonic() < deadline:
            with contextlib.suppress(httpx.HTTPError):
                if client.get(url).status_code == 200:
                    return
            time.sleep(0.2)
    output.seek(0)
    pytest.fail(
        f"Server did not become ready at {url} (exit code {process.poll()}).\n"
        f"Command: {process.args}\nOutput:\n{output.read()}"
    )


@contextlib.contextmanager
def server(command: list[str], cwd: Path, env: dict[str, str], ready_url: str):
    """Run a server in its own process group and always stop the whole group."""
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
            signal_group(process, signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=10)
            signal_group(process, signal.SIGKILL)
            process.wait()


@contextlib.contextmanager
def app_stack(**backend_env: str):
    """Start Uvicorn and `next start`; yield a client for the Next.js server."""
    node = shutil.which("node")
    assert node, "node is not on PATH"
    assert (FRONTEND / ".next" / "BUILD_ID").is_file(), (
        "No frontend build; run: npm --prefix frontend run build"
    )

    backend_port = free_port()
    backend_url = f"http://127.0.0.1:{backend_port}"
    backend_command = [
        sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
        "--app-dir", "backend", "--host", "127.0.0.1", "--port", str(backend_port),
    ]  # fmt: skip
    backend = server(
        backend_command,
        ROOT,
        server_env(DOCS_DIR=str(DOCS), **backend_env),
        f"{backend_url}/health",
    )
    with backend:
        frontend_port = free_port()
        frontend_url = f"http://127.0.0.1:{frontend_port}"
        next_bin = FRONTEND / "node_modules" / "next" / "dist" / "bin" / "next"
        frontend = server(
            [
                node, str(next_bin), "start",
                "-H", "127.0.0.1", "-p", str(frontend_port),
            ],  # fmt: skip
            FRONTEND,
            server_env(BACKEND_URL=backend_url),
            f"{frontend_url}/",
        )
        with frontend:
            with httpx.Client(
                base_url=frontend_url, trust_env=False, timeout=40
            ) as client:
                yield client


@pytest.fixture(scope="module")
def stub_app():
    with app_stack() as client:
        yield client


@pytest.fixture(scope="module")
def mock_llm():
    with MockLLM() as mock:
        yield mock


@pytest.fixture(scope="module")
def proxy_app(mock_llm):
    with app_stack(
        LLM_PROXY_URL=f"{mock_llm.url}/v1",
        LLM_PROXY_API_KEY=DUMMY_KEY,
        LLM_PROXY_MODEL=MODEL,
    ) as client:
        yield client


@pytest.fixture
def llm(mock_llm):
    mock_llm.requests.clear()
    mock_llm.fail = False
    return mock_llm


def ask(client: httpx.Client, question: str) -> httpx.Response:
    return client.post("/api/ask", json={"question": question})


def test_home_page(stub_app):
    response = stub_app.get("/")

    assert response.status_code == 200
    assert "Ask the docs" in response.text


def test_stub_answers_a_matching_question(stub_app):
    response = ask(stub_app, "How do emperor penguins stay warm?")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "stub"
    assert body["answer"].startswith("[stub]")
    assert body["sources"][0]["file"] == "penguins.txt"


def test_stub_gives_the_fallback_for_an_unrelated_question(stub_app):
    response = ask(stub_app, "Quarterly accounting spreadsheet?")

    assert response.status_code == 200
    assert response.json() == {"answer": FALLBACK, "sources": [], "mode": "stub"}


def test_proxy_returns_the_llm_answer(proxy_app, llm):
    response = ask(proxy_app, "Why does a volcano erupt?")

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == ANSWER
    assert body["mode"] == "proxy"
    (request,) = llm.requests
    assert request["path"] == "/v1/chat/completions"
    assert request["authorization"] == f"Bearer {DUMMY_KEY}"
    assert request["body"]["model"] == MODEL
    prompt = request["body"]["messages"][-1]["content"]
    assert body["sources"][0]["file"] == "volcanoes.md"
    assert body["sources"][0]["text"] in prompt
    assert "magma rises through the crust" in prompt


def test_proxy_failure_is_a_502(proxy_app, llm):
    llm.fail = True

    response = ask(proxy_app, "Why does a volcano erupt?")

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert len(llm.requests) == 1
