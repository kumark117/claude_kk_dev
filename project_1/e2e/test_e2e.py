"""End to end: real Next.js and FastAPI processes, talking over HTTP.

Needs the production build in frontend/.next, which test.sh makes first.
"""

import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from mock_llm import CANNED_ANSWER, MockLLM

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
DOCS = ROOT / "e2e" / "fixtures" / "docs"
STARTUP_TIMEOUT_SECONDS = 60
STOP_TIMEOUT_SECONDS = 10
ISOLATED_VARIABLES = (
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
MODEL = "mock-model"
DUMMY_KEY = "dummy-key"


@pytest.fixture(scope="module", autouse=True)
def stop_servers_when_terminated():
    """Turn termination signals into an interrupt so fixtures still clean up."""

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


def stop(process: subprocess.Popen) -> None:
    """Stop the whole process group that this test started."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        try:
            process.wait(STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            pass
    process.wait()


@contextmanager
def server(command, *, cwd, env, log_path, ready_text, ready_url):
    """Run a server until the block ends.

    ready_text must appear in its output, which shows that this process (and not
    another one holding the port) is listening, and ready_url must respond.
    """
    with log_path.open("wb") as log:
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
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while True:
            output = log_path.read_text(errors="replace")
            if process.poll() is not None:
                pytest.fail(
                    f"{command} exited with {process.returncode}:\n{output}",
                    pytrace=False,
                )
            if ready_text in output:
                try:
                    with httpx.Client(trust_env=False) as client:
                        client.get(ready_url, timeout=5)
                    break
                except httpx.TransportError:
                    pass
            if time.monotonic() > deadline:
                pytest.fail(f"{command} did not become ready:\n{output}", pytrace=False)
            time.sleep(0.1)
        yield
    finally:
        stop(process)


@contextmanager
def app_stack(logs: Path, **backend_settings: str):
    """Start FastAPI and Next.js and yield a client for the Next.js server."""
    if not (FRONTEND / ".next" / "BUILD_ID").is_file():
        pytest.fail("No Next.js build found. Run ./test.sh.", pytrace=False)
    env = {k: v for k, v in os.environ.items() if k not in ISOLATED_VARIABLES}
    env["NEXT_TELEMETRY_DISABLED"] = "1"

    backend_port = free_port()
    backend_url = f"http://127.0.0.1:{backend_port}"
    backend = server(
        [
            sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
            "--host", "127.0.0.1", "--port", str(backend_port),
        ],
        cwd=ROOT / "backend",
        env={**env, "DOCS_DIR": str(DOCS), **backend_settings},
        log_path=logs / "backend.log",
        ready_text="Uvicorn running on",
        ready_url=f"{backend_url}/health",
    )  # fmt: skip
    with backend:
        web_port = free_port()
        web_url = f"http://127.0.0.1:{web_port}"
        web = server(
            [
                str(FRONTEND / "node_modules" / ".bin" / "next"), "start",
                "--hostname", "127.0.0.1", "--port", str(web_port),
            ],
            cwd=FRONTEND,
            env={**env, "BACKEND_URL": backend_url},
            log_path=logs / "web.log",
            ready_text="Ready",
            ready_url=web_url,
        )  # fmt: skip
        with web, httpx.Client(base_url=web_url, trust_env=False, timeout=60) as client:
            yield client


@pytest.fixture(scope="module")
def stub_app(tmp_path_factory):
    with app_stack(tmp_path_factory.mktemp("stub")) as client:
        yield client


@pytest.fixture(scope="module")
def mock_llm():
    with MockLLM() as mock:
        yield mock


@pytest.fixture(scope="module")
def proxy_app(tmp_path_factory, mock_llm):
    with app_stack(
        tmp_path_factory.mktemp("proxy"),
        LLM_PROXY_URL=mock_llm.base_url,
        LLM_PROXY_API_KEY=DUMMY_KEY,
        LLM_PROXY_MODEL=MODEL,
    ) as client:
        yield client


@pytest.fixture(autouse=True)
def reset_mock(mock_llm):
    mock_llm.fail = False
    mock_llm.requests.clear()


def ask(client: httpx.Client, question: str) -> httpx.Response:
    return client.post("/api/ask", json={"question": question})


def test_home_page(stub_app):
    response = stub_app.get("/")

    assert response.status_code == 200
    assert "Ask the docs" in response.text


def test_stub_answers_a_matching_question(stub_app):
    response = ask(stub_app, "Which lava flows quickly?")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "stub"
    assert body["answer"].startswith("[stub]")
    assert body["sources"][0]["file"] == "volcanoes.md"


def test_stub_falls_back_for_an_unrelated_question(stub_app):
    response = ask(stub_app, "Quarterly tax filing deadline?")

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "I couldn't find anything relevant in the documents."
    assert body["sources"] == []


def test_proxy_returns_the_llm_answer(proxy_app, mock_llm):
    response = ask(proxy_app, "How do emperor penguins stay warm?")

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == CANNED_ANSWER
    assert body["mode"] == "proxy"

    [request] = mock_llm.requests
    assert request["path"] == "/v1/chat/completions"
    assert request["authorization"] == f"Bearer {DUMMY_KEY}"
    assert request["body"]["model"] == MODEL
    assert "huddle together" in request["body"]["messages"][1]["content"]


def test_proxy_failure_gives_502(proxy_app, mock_llm):
    mock_llm.fail = True

    response = ask(proxy_app, "How do emperor penguins stay warm?")

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert len(mock_llm.requests) == 1
