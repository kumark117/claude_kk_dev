from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app, settings_from_env
from app.retrieval import DocumentError
from app.llm import ConfigError

NO_MATCH = "I couldn't find anything relevant in the documents."
LLM_VARIABLES = ("LLM_PROXY_URL", "LLM_PROXY_API_KEY", "LLM_PROXY_MODEL")


@pytest.fixture(autouse=True)
def docs(tmp_path, monkeypatch):
    for name in LLM_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "volcano.md").write_text(
        "# Volcanoes\n\nLava is molten rock.\n\nAsh clouds can ground planes.",
        encoding="utf-8",
    )
    (tmp_path / "ocean.txt").write_text("The ocean is salty.", encoding="utf-8")
    monkeypatch.setenv("DOCS_DIR", str(tmp_path))
    return tmp_path


def enable_proxy(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_URL", "http://llm.test/v1")
    monkeypatch.setenv("LLM_PROXY_API_KEY", "sk-secret")
    monkeypatch.setenv("LLM_PROXY_MODEL", "test-model")


def mock_llm(response):
    requests = []

    def handler(request):
        requests.append(request)
        return response

    return httpx.MockTransport(handler), requests


def test_health_reports_chunk_count():
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "chunks": 3}


def test_successful_ask_returns_answer_sources_and_mode():
    client = TestClient(create_app())

    response = client.post("/ask", json={"question": "  What is lava?  "})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "sources", "mode"}
    assert body["mode"] == "stub"
    assert body["answer"] == (
        "[stub] Based on volcano.md: # Volcanoes\nLava is molten rock."
    )
    [source] = body["sources"]
    assert set(source) == {"file", "text", "score"}
    assert source["file"] == "volcano.md"
    assert source["text"] == "# Volcanoes\nLava is molten rock."
    assert isinstance(source["score"], float) and source["score"] > 0


@pytest.mark.parametrize(
    "body",
    [
        {"question": ""},
        {"question": "   \n\t "},
        {"question": "x" * 1001},
        {"question": 5},
        {"question": None},
        {},
        [],
    ],
)
def test_invalid_questions_get_422(body):
    client = TestClient(create_app())

    assert client.post("/ask", json=body).status_code == 422


@pytest.mark.parametrize(
    "question", ["x", "x" * 1000, "   " + "x" * 1000 + "   "]
)
def test_length_limit_applies_after_trimming(question):
    client = TestClient(create_app())

    assert client.post("/ask", json={"question": question}).status_code == 200


def test_no_match_gives_fallback_without_calling_the_llm(monkeypatch):
    enable_proxy(monkeypatch)
    transport, requests = mock_llm(httpx.Response(500))
    client = TestClient(create_app(transport=transport))

    response = client.post("/ask", json={"question": "quantum chromodynamics"})

    assert response.status_code == 200
    assert response.json() == {"answer": NO_MATCH, "sources": [], "mode": "proxy"}
    assert requests == []


def test_empty_folder_starts_and_answers_with_fallback(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("DOCS_DIR", str(empty))
    client = TestClient(create_app())

    assert client.get("/health").json() == {"status": "ok", "chunks": 0}
    response = client.post("/ask", json={"question": "lava"})
    assert response.json() == {"answer": NO_MATCH, "sources": [], "mode": "stub"}


def test_proxy_answer_is_returned(monkeypatch):
    enable_proxy(monkeypatch)
    transport, requests = mock_llm(
        httpx.Response(200, json={"choices": [{"message": {"content": "Hot."}}]})
    )
    client = TestClient(create_app(transport=transport))

    response = client.post("/ask", json={"question": "lava"})

    assert response.status_code == 200
    assert response.json()["answer"] == "Hot."
    assert response.json()["mode"] == "proxy"
    assert response.json()["sources"][0]["file"] == "volcano.md"
    assert len(requests) == 1


def test_llm_error_gives_502_without_leaking_key_or_body(monkeypatch, caplog):
    caplog.set_level("DEBUG")
    enable_proxy(monkeypatch)
    transport, _ = mock_llm(httpx.Response(500, text="upstream-secret-body"))
    client = TestClient(create_app(transport=transport))

    response = client.post("/ask", json={"question": "lava"})

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    for secret in ("sk-secret", "upstream-secret-body"):
        assert secret not in response.text
        assert secret not in caplog.text


def test_documents_are_read_once_at_startup(docs):
    client = TestClient(create_app())
    (docs / "late.md").write_text("zebra", encoding="utf-8")

    assert client.get("/health").json()["chunks"] == 3
    assert client.post("/ask", json={"question": "zebra"}).json()["sources"] == []


def test_separate_instances_use_their_own_environment(monkeypatch):
    stub = TestClient(create_app())
    enable_proxy(monkeypatch)
    transport, _ = mock_llm(httpx.Response(500))
    proxy = TestClient(create_app(transport=transport))

    assert stub.post("/ask", json={"question": "lava"}).json()["mode"] == "stub"
    assert proxy.post("/ask", json={"question": "lava"}).status_code == 502


@pytest.mark.parametrize("env", [{}, {"DOCS_DIR": ""}])
def test_default_docs_folder_is_data_at_the_project_root(env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = Path(__file__).resolve().parents[2]

    assert settings_from_env(env).docs_dir == root / "data"


def test_missing_docs_folder_fails_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_DIR", str(tmp_path / "missing"))

    with pytest.raises(DocumentError, match="missing"):
        create_app()


def test_bad_utf8_fails_startup(docs):
    (docs / "broken.md").write_bytes(b"\xff")

    with pytest.raises(DocumentError, match="broken.md"):
        create_app()


def test_missing_model_fails_startup(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_URL", "http://llm.test/v1")

    with pytest.raises(ConfigError, match="LLM_PROXY_MODEL"):
        create_app()
