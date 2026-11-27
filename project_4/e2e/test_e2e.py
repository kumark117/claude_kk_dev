"""End to end: real `next start` and Uvicorn processes talking over loopback.

Needs the Next.js production build, which test.sh makes before running this.
"""

import os
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
DOCS = ROOT / "e2e" / "fixtures" / "docs"
READY_TIMEOUT_SECONDS = 60
FALLBACK = "I couldn't find anything relevant in the documents."
QUESTION = "What does a lighthouse beacon warn?"
DUMMY_KEY = "dummy-e2e-key"
MODEL = "mock-model"
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


@pytest.fixture(scope="module", autouse=True)
def stop_servers_when_terminated() -> Iterator[None]:
    """Turn SIGTERM and SIGHUP into an interrupt so that fixtures still clean up."""

    def interrupt(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    previous = {s: signal.signal(s, interrupt) for s in (signal.SIGTERM, signal.SIGHUP)}
    yield
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def signal_group(process: subprocess.Popen, signum: signal.Signals) -> None:
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass


@contextmanager
def server(
    command: list[str], cwd: Path, env: dict[str, str], ready_url: str
) -> Iterator[None]:
    """Run a server in its own process group until the block ends."""
    clean = {k: v for k, v in os.environ.items() if k not in ISOLATED}
    clean.update(env, NEXT_TELEMETRY_DISABLED="1")
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=clean,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            problem = wait_until_ready(process, ready_url)
            if problem:
                output.seek(0)
                log = output.read().decode(errors="replace")
                pytest.fail(f"{' '.join(command)}: {problem}\n--- output ---\n{log}")
            yield
        finally:
            signal_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            # Catches a stuck leader and any children that outlived it.
            signal_group(process, signal.SIGKILL)
            process.wait(timeout=10)


def wait_until_ready(process: subprocess.Popen, url: str) -> str | None:
    """Return None once url answers 200, otherwise what went wrong."""
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    with httpx.Client(trust_env=False, timeout=2) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return f"exited with code {process.returncode}"
            try:
                if client.get(url).status_code == 200:
                    return None
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
    return f"not ready after {READY_TIMEOUT_SECONDS} seconds"


@contextmanager
def stack(backend_env: dict[str, str]) -> Iterator[httpx.Client]:
    """Start the backend and the frontend; yield a client for the frontend."""
    if not (FRONTEND / ".next" / "BUILD_ID").is_file():
        pytest.fail("No Next.js build found. Run ./test.sh, or npm run build first.")
    backend_port = free_port()
    backend_url = f"http://127.0.0.1:{backend_port}"
    backend = [
        sys.executable, "-m", "uvicorn", "--factory", "app.main:create_app",
        "--app-dir", str(ROOT / "backend"),
        "--host", "127.0.0.1", "--port", str(backend_port),
    ]
    with server(
        backend, ROOT, {"DOCS_DIR": str(DOCS), **backend_env}, f"{backend_url}/health"
    ):
        web_port = free_port()
        web_url = f"http://127.0.0.1:{web_port}"
        frontend = [
            str(FRONTEND / "node_modules" / ".bin" / "next"), "start",
            "--hostname", "127.0.0.1", "--port", str(web_port),
        ]
        with server(frontend, FRONTEND, {"BACKEND_URL": backend_url}, web_url):
            with httpx.Client(base_url=web_url, trust_env=False, timeout=60) as client:
                yield client


@pytest.fixture(scope="module")
def stub_web() -> Iterator[httpx.Client]:
    with stack({}) as client:
        yield client


@pytest.fixture(scope="module")
def mock_llm() -> Iterator[MockLLM]:
    with MockLLM() as mock:
        yield mock


@pytest.fixture(scope="module")
def proxy_web(mock_llm: MockLLM) -> Iterator[httpx.Client]:
    env = {
        "LLM_PROXY_URL": f"{mock_llm.url}/v1",
        "LLM_PROXY_API_KEY": DUMMY_KEY,
        "LLM_PROXY_MODEL": MODEL,
    }
    with stack(env) as client:
        yield client


def test_page_is_served(stub_web):
    response = stub_web.get("/")

    assert response.status_code == 200
    assert "Ask the docs" in response.text


def test_stub_answers_a_matching_question(stub_web):
    response = stub_web.post("/api/ask", json={"question": QUESTION})

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "stub"
    assert body["answer"].startswith("[stub]")
    assert body["sources"][0]["file"] == "lighthouses.md"


def test_stub_falls_back_for_an_unrelated_question(stub_web):
    response = stub_web.post("/api/ask", json={"question": "Zebra xylophone?"})

    assert response.status_code == 200
    assert response.json() == {"answer": FALLBACK, "sources": [], "mode": "stub"}


def test_proxy_returns_the_llm_answer(proxy_web, mock_llm):
    mock_llm.reset()

    response = proxy_web.post("/api/ask", json={"question": QUESTION})

    assert response.status_code == 200
    body = response.json()
    assert (body["answer"], body["mode"]) == (CANNED_ANSWER, "proxy")
    [request] = mock_llm.requests
    assert request["path"] == "/v1/chat/completions"
    assert request["authorization"] == f"Bearer {DUMMY_KEY}"
    assert request["body"]["model"] == MODEL
    prompt = request["body"]["messages"][-1]["content"]
    assert body["sources"][0]["text"] in prompt


def test_proxy_failure_is_reported_as_502(proxy_web, mock_llm):
    mock_llm.reset()
    mock_llm.fail = True

    response = proxy_web.post("/api/ask", json={"question": QUESTION})

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert len(mock_llm.requests) == 1
