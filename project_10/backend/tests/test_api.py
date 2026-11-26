import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.llm import LLMConfigError
from app.main import create_app
from app.retrieval import DocumentError

NO_MATCH = "I couldn't find anything relevant in the documents."


@pytest.fixture
def docs(tmp_path):
    (tmp_path / "space.md").write_text(
        "# Rockets\n\nRockets reach orbit using fuel.\n\nMoons circle planets.",
        encoding="utf-8",
    )
    (tmp_path / "tea.txt").write_text("Tea is steeped in water.", encoding="utf-8")
    return tmp_path


def proxy_env(docs):
    return {
        "DOCS_DIR": str(docs),
        "LLM_PROXY_URL": "http://127.0.0.1:9/v1",
        "LLM_PROXY_MODEL": "test-model",
        "LLM_PROXY_API_KEY": "dummy-key",
    }


def client_for(env, handler=None):
    transport = httpx.MockTransport(handler) if handler else None
    return TestClient(create_app(env, transport=transport))


def test_health_reports_the_chunk_count(docs):
    response = client_for({"DOCS_DIR": str(docs)}).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "chunks": 3}


def test_factory_reads_the_process_environment(docs, monkeypatch):
    monkeypatch.setenv("DOCS_DIR", str(docs))
    monkeypatch.delenv("LLM_PROXY_URL", raising=False)

    response = TestClient(create_app()).get("/health")

    assert response.json() == {"status": "ok", "chunks": 3}


@pytest.mark.parametrize("env", [{}, {"DOCS_DIR": ""}])
def test_default_documents_folder_is_data_at_the_project_root(
    env, tmp_path, monkeypatch
):
    root = main.Path(__file__).resolve().parents[2]
    seen = []
    monkeypatch.setattr(main, "load_chunks", lambda folder: seen.append(folder) or [])
    monkeypatch.chdir(tmp_path)

    create_app(env)

    assert seen == [root / "data"]


def test_documents_are_read_once_at_startup(docs):
    client = client_for({"DOCS_DIR": str(docs)})
    (docs / "late.md").write_text("zebra", encoding="utf-8")

    assert client.get("/health").json()["chunks"] == 3
    assert client.post("/ask", json={"question": "zebra"}).json()["sources"] == []


def test_missing_documents_folder_fails_startup(tmp_path):
    with pytest.raises(DocumentError):
        create_app({"DOCS_DIR": str(tmp_path / "missing")})


def test_missing_model_fails_startup(docs):
    env = proxy_env(docs)
    del env["LLM_PROXY_MODEL"]

    with pytest.raises(LLMConfigError):
        create_app(env)


def test_successful_ask_returns_answer_sources_and_mode(docs):
    client = client_for({"DOCS_DIR": str(docs)})

    response = client.post("/ask", json={"question": "  rockets orbit  "})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "sources", "mode"}
    assert body["mode"] == "stub"
    text = "# Rockets\nRockets reach orbit using fuel."
    assert body["answer"] == f"[stub] Based on space.md: {text}"
    assert len(body["sources"]) == 1
    source = body["sources"][0]
    assert set(source) == {"file", "text", "score"}
    assert source["file"] == "space.md"
    assert source["text"] == text
    assert source["score"] > 0


def test_question_is_trimmed_before_it_reaches_the_llm(docs):
    seen = []

    def handler(request):
        seen.append(request.content.decode())
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = client_for(proxy_env(docs), handler)

    response = client.post("/ask", json={"question": "  \n rockets?  \t "})

    assert response.json()["answer"] == "ok"
    assert response.json()["mode"] == "proxy"
    assert "rockets?" in seen[0]
    assert "rockets?  " not in seen[0]


@pytest.mark.parametrize(
    "body",
    [
        {"question": ""},
        {"question": "   \n\t "},
        {"question": "a" * 1001},
        {"question": 5},
        {"question": None},
        {},
        [],
    ],
)
def test_invalid_questions_return_422(docs, body):
    client = client_for({"DOCS_DIR": str(docs)})

    assert client.post("/ask", json=body).status_code == 422


@pytest.mark.parametrize("question", ["a", "a" * 1000, "  " + "a" * 1000 + "  "])
def test_length_limits_apply_to_the_trimmed_question(docs, question):
    client = client_for({"DOCS_DIR": str(docs)})

    assert client.post("/ask", json={"question": question}).status_code == 200


@pytest.mark.parametrize("question", ["zebra", "what is the"])
def test_no_match_returns_fallback_without_calling_the_llm(docs, question):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    client = client_for(proxy_env(docs), handler)

    response = client.post("/ask", json={"question": question})

    assert response.status_code == 200
    assert response.json() == {"answer": NO_MATCH, "sources": [], "mode": "proxy"}
    assert calls == []


def test_llm_error_returns_502_without_leaking_key_or_body(docs, caplog):
    def handler(request):
        return httpx.Response(500, text="upstream-secret-body dummy-key")

    client = client_for(proxy_env(docs), handler)

    with caplog.at_level(logging.DEBUG):
        response = client.post("/ask", json={"question": "rockets"})

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert "dummy-key" not in caplog.text
    assert "upstream-secret-body" not in caplog.text
