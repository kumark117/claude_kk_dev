"""End to end: real Next.js and FastAPI processes, talked to over loopback HTTP.

Needs the production build from ``npm --prefix frontend run build``.
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
FIXTURE_DOCS = ROOT / "e2e" / "fixtures" / "docs"
STARTUP_TIMEOUT = 120
DUMMY_KEY = "dummy-e2e-key"
FALLBACK = "I couldn't find anything relevant in the documents."
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
def stop_servers_when_terminated():
    """Turn SIGTERM and SIGHUP into an interrupt, so that fixtures still clean up.

    The servers run in their own process groups and would otherwise survive.
    """

    def interrupt(signum, frame):
        raise KeyboardInterrupt(f"received signal {signum}")

    previous = {
        signum: signal.signal(signum, interrupt)
        for signum in (signal.SIGTERM, signal.SIGHUP)
    }
    yield
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def signal_group(process, signum):
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signum)


@contextlib.contextmanager
def server(command, ready_path, port, cwd, env):
    """Run ``command`` until the block ends; yield its base URL once it answers."""
    base_url = f"http://127.0.0.1:{port}"
    clean_env = {k: v for k, v in os.environ.items() if k not in ISOLATED}
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env={**clean_env, "NEXT_TELEMETRY_DISABLED": "1", **env},
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            wait_until_ready(process, base_url + ready_path, log)
            yield base_url
        finally:
            signal_group(process, signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=10)
            # Whatever is left of the group, including any children.
            signal_group(process, signal.SIGKILL)
            process.wait()


def wait_until_ready(process, url, log):
    deadline = time.monotonic() + STARTUP_TIMEOUT
    with httpx.Client(trust_env=False, timeout=5) as client:
        while process.poll() is None and time.monotonic() < deadline:
            with contextlib.suppress(httpx.HTTPError):
                if client.get(url).status_code == 200:
                    return
            time.sleep(0.2)
    log.seek(0)
    output = log.read().decode(errors="replace")
    state = "timed out" if process.poll() is None else f"exited {process.returncode}"
    pytest.fail(
        f"Server did not become ready at {url} ({state}): {process.args}\n{output}",
        pytrace=False,
    )


@contextlib.contextmanager
def stack(backend_env):
    """Start a backend and a frontend that uses it; yield an HTTP client."""
    backend_port = free_port()
    backend_command = [
        sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
        "--app-dir", str(ROOT / "backend"),
        "--host", "127.0.0.1", "--port", str(backend_port),
    ]  # fmt: skip
    env = {"DOCS_DIR": str(FIXTURE_DOCS), **backend_env}
    with server(backend_command, "/health", backend_port, ROOT, env) as backend_url:
        frontend_port = free_port()
        frontend_command = [
            str(FRONTEND / "node_modules" / ".bin" / "next"), "start",
            "--hostname", "127.0.0.1", "--port", str(frontend_port),
        ]  # fmt: skip
        env = {"BACKEND_URL": backend_url}
        with server(frontend_command, "/", frontend_port, FRONTEND, env) as url:
            with httpx.Client(base_url=url, trust_env=False, timeout=60) as client:
                yield client


@pytest.fixture(scope="module")
def stub_app():
    with stack({}) as client:
        yield client


@pytest.fixture(scope="module")
def mock_llm():
    with MockLLM() as mock:
        yield mock


@pytest.fixture(scope="module")
def proxy_app(mock_llm):
    env = {
        "LLM_PROXY_URL": f"{mock_llm.url}/v1",
        "LLM_PROXY_API_KEY": DUMMY_KEY,
        "LLM_PROXY_MODEL": "e2e-model",
    }
    with stack(env) as client:
        yield client


@pytest.fixture(autouse=True)
def reset_mock(request):
    if "mock_llm" in request.fixturenames:
        mock = request.getfixturevalue("mock_llm")
        mock.requests.clear()
        mock.fail = False


def ask(client, question):
    return client.post("/api/ask", json={"question": question})


def test_page_is_served(stub_app):
    response = stub_app.get("/")

    assert response.status_code == 200
    assert "Ask the docs" in response.text


def test_stub_answers_a_matching_question(stub_app):
    response = ask(stub_app, "Where do penguins live?")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "stub"
    assert body["answer"].startswith("[stub]")
    assert body["sources"][0]["file"] == "penguins.md"


def test_stub_falls_back_for_an_unrelated_question(stub_app):
    response = ask(stub_app, "zeppelin maintenance schedule")

    assert response.status_code == 200
    assert response.json() == {"answer": FALLBACK, "sources": [], "mode": "stub"}


def test_proxy_returns_the_llm_answer(proxy_app, mock_llm):
    response = ask(proxy_app, "Why does a volcano erupt?")

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == CANNED_ANSWER
    assert body["mode"] == "proxy"
    (sent,) = mock_llm.requests
    assert sent["path"] == "/v1/chat/completions"
    assert sent["authorization"] == f"Bearer {DUMMY_KEY}"
    assert sent["body"]["model"] == "e2e-model"
    assert "molten rock rises" in sent["body"]["messages"][-1]["content"]


def test_proxy_failure_gives_502(proxy_app, mock_llm):
    mock_llm.fail = True

    response = ask(proxy_app, "Why does a volcano erupt?")

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert len(mock_llm.requests) == 1
