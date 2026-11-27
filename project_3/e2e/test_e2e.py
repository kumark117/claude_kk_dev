"""End to end: real Uvicorn and `next start` processes talking over loopback.

Needs an existing frontend build (test.sh makes it).
"""

import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager, suppress
from pathlib import Path

import httpx
import pytest

from mock_llm import CANNED_ANSWER, MockLLM

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
FIXTURE_DOCS = ROOT / "e2e" / "fixtures" / "docs"
STARTUP_TIMEOUT = 60
APP_VARIABLES = {
    "DOCS_DIR",
    "BACKEND_URL",
    "LLM_PROXY_URL",
    "LLM_PROXY_API_KEY",
    "LLM_PROXY_MODEL",
}
DUMMY_KEY = "dummy-e2e-key"
MODEL = "mock-model"


@pytest.fixture(scope="session", autouse=True)
def stop_servers_on_signals():
    """Turn SIGTERM and SIGHUP into an interrupt, so teardown still runs."""

    def interrupt(signum, frame):
        raise KeyboardInterrupt(f"received signal {signum}")

    previous = {
        s: signal.signal(s, interrupt) for s in (signal.SIGTERM, signal.SIGHUP)
    }
    yield
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def clean_env(**extra):
    """The current environment without app settings or HTTP proxies."""
    env = {
        name: value
        for name, value in os.environ.items()
        if name not in APP_VARIABLES
        and name.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}
    }
    return {**env, "NEXT_TELEMETRY_DISABLED": "1", **extra}


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def stop(process):
    """Stop the process and everything else in its process group."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        pass
    finally:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def wait_until_ready(process, url, log):
    deadline = time.monotonic() + STARTUP_TIMEOUT
    with httpx.Client(trust_env=False, timeout=5) as client:
        while time.monotonic() < deadline and process.poll() is None:
            with suppress(httpx.HTTPError):
                ready = client.get(url).status_code == 200
                # If our process has died, something else answered on that port.
                if ready and process.poll() is None:
                    return
            time.sleep(0.2)
    pytest.fail(
        f"Server not ready at {url} (exit code {process.poll()}).\n"
        f"Output:\n{log.read_text(errors='replace')}"
    )


@contextmanager
def server(command, port, ready_path, cwd, env, log):
    url = f"http://127.0.0.1:{port}"
    with log.open("wb") as output:
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
        wait_until_ready(process, url + ready_path, log)
        yield url
    finally:
        stop(process)


@contextmanager
def application(logs, **backend_settings):
    """Run the backend and the frontend; yield an HTTP client for the frontend."""
    port = free_port()
    backend_command = [
        sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
        "--host", "127.0.0.1", "--port", str(port),
    ]  # fmt: skip
    backend_env = clean_env(DOCS_DIR=str(FIXTURE_DOCS), **backend_settings)
    with server(
        backend_command, port, "/health", BACKEND, backend_env, logs / "backend.log"
    ) as backend_url:
        port = free_port()
        frontend_command = [
            "node", "node_modules/next/dist/bin/next", "start",
            "--hostname", "127.0.0.1", "--port", str(port),
        ]  # fmt: skip
        frontend_env = clean_env(BACKEND_URL=backend_url)
        with server(
            frontend_command, port, "/", FRONTEND, frontend_env, logs / "frontend.log"
        ) as frontend_url:
            with httpx.Client(
                base_url=frontend_url, trust_env=False, timeout=40
            ) as client:
                yield client


@pytest.fixture(scope="module")
def stub_app(tmp_path_factory):
    with application(tmp_path_factory.mktemp("stub")) as client:
        yield client


@pytest.fixture(scope="module")
def mock_server():
    with MockLLM() as mock:
        yield mock


@pytest.fixture(scope="module")
def proxy_app(tmp_path_factory, mock_server):
    with application(
        tmp_path_factory.mktemp("proxy"),
        LLM_PROXY_URL=f"{mock_server.url}/v1",
        LLM_PROXY_API_KEY=DUMMY_KEY,
        LLM_PROXY_MODEL=MODEL,
    ) as client:
        yield client


@pytest.fixture
def mock_llm(mock_server):
    mock_server.requests.clear()
    mock_server.fail = False
    return mock_server


def ask(client, question):
    return client.post("/api/ask", json={"question": question})


def test_home_page(stub_app):
    response = stub_app.get("/")

    assert response.status_code == 200
    assert "Ask the docs" in response.text


def test_stub_answers_a_matching_question(stub_app):
    response = ask(stub_app, "How do beekeepers calm a hive?")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "stub"
    assert body["answer"].startswith("[stub]")
    assert body["sources"][0]["file"] == "beekeeping.txt"


def test_stub_falls_back_for_an_unrelated_question(stub_app):
    response = ask(stub_app, "Quantum chromodynamics?")

    assert response.status_code == 200
    assert response.json() == {
        "answer": "I couldn't find anything relevant in the documents.",
        "sources": [],
        "mode": "stub",
    }


def test_proxy_returns_the_llm_answer(proxy_app, mock_llm):
    response = ask(proxy_app, "How is clay fired in a kiln?")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "proxy"
    assert body["answer"] == CANNED_ANSWER
    [request] = mock_llm.requests
    assert request["path"] == "/v1/chat/completions"
    assert request["authorization"] == f"Bearer {DUMMY_KEY}"
    assert request["body"]["model"] == MODEL
    prompt = request["body"]["messages"][-1]["content"]
    assert "pottery.md" in prompt
    assert "bisque fired in a kiln" in prompt


def test_proxy_failure_gives_502(proxy_app, mock_llm):
    mock_llm.fail = True

    response = ask(proxy_app, "How is clay fired in a kiln?")

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert len(mock_llm.requests) == 1
