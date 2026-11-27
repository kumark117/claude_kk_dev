"""End to end: real Next.js and FastAPI processes talking over loopback HTTP.

Needs the frontend to be built already (test.sh does that).
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
MATCHING = "How do glaciers move over bedrock?"
UNRELATED = "Quarterly tax filing deadline?"
ISOLATED = (
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


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ISOLATED}
    return {**env, "NEXT_TELEMETRY_DISABLED": "1", **extra}


def signal_group(process: subprocess.Popen, signum: int):
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass


def wait_until_ready(process: subprocess.Popen, url: str):
    deadline = time.monotonic() + STARTUP_TIMEOUT
    with httpx.Client(trust_env=False, timeout=5) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"process exited with {process.returncode}")
            try:
                if client.get(url).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.2)
    raise RuntimeError(f"not ready after {STARTUP_TIMEOUT} s: {url}")


@contextmanager
def server(command: list[str], cwd: Path, env: dict[str, str], ready_url: str):
    """Run a server in its own process group and always stop the whole group."""
    with tempfile.TemporaryFile(mode="w+", errors="replace") as log:
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
            try:
                wait_until_ready(process, ready_url)
            except RuntimeError as error:
                log.seek(0)
                pytest.fail(
                    f"{' '.join(command)} failed to start: {error}\n{log.read()}"
                )
            yield
        finally:
            signal_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            signal_group(process, signal.SIGKILL)
            process.wait()


@contextmanager
def stack(**backend_env: str):
    """Start FastAPI and Next.js; yield an HTTP client pointed at Next.js."""
    api_port = free_port()
    api_url = f"http://127.0.0.1:{api_port}"
    api = [sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory"]
    api += ["--host", "127.0.0.1", "--port", str(api_port)]
    with server(
        api,
        ROOT / "backend",
        clean_env(DOCS_DIR=str(DOCS), **backend_env),
        f"{api_url}/health",
    ):
        # Chosen only now, so the backend cannot be holding the same port.
        web_port = free_port()
        web_url = f"http://127.0.0.1:{web_port}"
        web = [str(FRONTEND / "node_modules" / ".bin" / "next"), "start"]
        web += ["--hostname", "127.0.0.1", "--port", str(web_port)]
        with server(web, FRONTEND, clean_env(BACKEND_URL=api_url), f"{web_url}/"):
            with httpx.Client(base_url=web_url, trust_env=False, timeout=40) as client:
                yield client


def interrupt(signum, frame):
    raise KeyboardInterrupt


@pytest.fixture(scope="module", autouse=True)
def stop_servers_on_termination():
    """Turn SIGTERM and SIGHUP into an interrupt so the teardown still runs."""
    previous = {
        signum: signal.signal(signum, interrupt)
        for signum in (signal.SIGTERM, signal.SIGHUP)
    }
    yield
    for signum, handler in previous.items():
        signal.signal(signum, handler)


@pytest.fixture(scope="module")
def stub_app():
    with stack() as client:
        yield client


@pytest.fixture(scope="module")
def mock_llm():
    with MockLLM() as mock:
        yield mock


@pytest.fixture(scope="module")
def proxy_app(mock_llm):
    with stack(
        LLM_PROXY_URL=mock_llm.url,
        LLM_PROXY_API_KEY="dummy-key",
        LLM_PROXY_MODEL="dummy-model",
    ) as client:
        yield client


@pytest.fixture
def llm(mock_llm):
    mock_llm.reset()
    return mock_llm


def test_home_page(stub_app):
    response = stub_app.get("/")

    assert response.status_code == 200
    assert "Ask the docs" in response.text


def test_stub_answers_a_matching_question(stub_app):
    response = stub_app.post("/api/ask", json={"question": MATCHING})

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "stub"
    assert body["answer"].startswith("[stub]")
    assert body["sources"][0]["file"] == "glaciers.md"


def test_stub_falls_back_for_an_unrelated_question(stub_app):
    response = stub_app.post("/api/ask", json={"question": UNRELATED})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "I couldn't find anything relevant in the documents."
    assert body["sources"] == []


def test_proxy_returns_the_llm_answer(proxy_app, llm):
    response = proxy_app.post("/api/ask", json={"question": MATCHING})

    assert response.status_code == 200
    assert response.json()["answer"] == ANSWER
    assert response.json()["mode"] == "proxy"
    [request] = llm.requests
    assert request.path == "/v1/chat/completions"
    assert request.authorization == "Bearer dummy-key"
    assert request.body["model"] == "dummy-model"
    retrieved = response.json()["sources"][0]["text"]
    assert "meltwater" in retrieved
    assert retrieved in request.body["messages"][1]["content"]


def test_proxy_failure_gives_502(proxy_app, llm):
    llm.fail = True

    response = proxy_app.post("/api/ask", json={"question": MATCHING})

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert len(llm.requests) == 1
