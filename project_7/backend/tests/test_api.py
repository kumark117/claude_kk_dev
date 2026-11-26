from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.retrieval import DocumentError

FALLBACK = "I couldn't find anything relevant in the documents."
KEY = "dummy-test-key"


@pytest.fixture
def docs(tmp_path):
    (tmp_path / "cats.md").write_text("# Cats\n\nCats purr when content.", encoding="utf-8")
    (tmp_path / "rockets.txt").write_text(
        "Rockets burn fuel to reach orbit.\n\nBoosters separate early.", encoding="utf-8"
    )
    return tmp_path


def client_for(docs, handler=None, **env):
    transport = httpx.MockTransport(handler) if handler else None
    return TestClient(create_app({"DOCS_DIR": str(docs), **env}, transport=transport))


def proxy_client(docs, handler):
    return client_for(
        docs,
        handler,
        LLM_PROXY_URL="http://127.0.0.1:9/v1",
        LLM_PROXY_MODEL="test-model",
        LLM_PROXY_API_KEY=KEY,
    )


def test_health_reports_chunk_count(docs):
    response = client_for(docs).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "chunks": 3}


def test_empty_folder_starts_with_zero_chunks(tmp_path):
    client = client_for(tmp_path)

    assert client.get("/health").json() == {"status": "ok", "chunks": 0}
    assert client.post("/ask", json={"question": "cats"}).json()["answer"] == FALLBACK


def test_successful_ask_has_the_response_shape(docs):
    response = client_for(docs).post("/ask", json={"question": "Why do cats purr?"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "sources", "mode"}
    assert body["mode"] == "stub"
    assert body["answer"] == "[stub] Based on cats.md: # Cats\nCats purr when content."
    [source] = body["sources"]
    assert set(source) == {"file", "text", "score"}
    assert source["file"] == "cats.md"
    assert source["text"] == "# Cats\nCats purr when content."
    assert source["score"] > 0


def test_question_is_trimmed(docs):
    seen = []

    def handler(request):
        seen.append(request.content.decode())
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    proxy_client(docs, handler).post("/ask", json={"question": "  \n cats \t "})

    assert "cats" in seen[0]
    assert "\\t" not in seen[0]


def test_question_of_1000_characters_after_trimming_is_accepted(docs):
    question = "  " + "a" * 1000 + "  "

    assert client_for(docs).post("/ask", json={"question": question}).status_code == 200


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
def test_invalid_questions_get_422(docs, body):
    assert client_for(docs).post("/ask", json=body).status_code == 422


def test_no_match_returns_fallback_without_calling_the_llm(docs):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    response = proxy_client(docs, handler).post("/ask", json={"question": "volcano"})

    assert response.status_code == 200
    assert response.json() == {"answer": FALLBACK, "sources": [], "mode": "proxy"}
    assert calls == []


def test_proxy_answer_is_returned(docs):
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "From proxy"}}]})

    body = proxy_client(docs, handler).post("/ask", json={"question": "rockets"}).json()

    assert body["answer"] == "From proxy"
    assert body["mode"] == "proxy"
    assert body["sources"][0]["file"] == "rockets.txt"


def test_llm_error_returns_502_without_leaking(docs, caplog):
    caplog.set_level("DEBUG")

    def handler(request):
        return httpx.Response(500, text=f"upstream secret {KEY}")

    response = proxy_client(docs, handler).post("/ask", json={"question": "rockets"})

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert KEY not in caplog.text
    assert "upstream secret" not in caplog.text


def test_documents_are_read_once_at_startup(docs):
    client = client_for(docs)
    (docs / "late.md").write_text("volcano", encoding="utf-8")

    assert client.get("/health").json()["chunks"] == 3
    assert client.post("/ask", json={"question": "volcano"}).json()["sources"] == []


def test_missing_documents_folder_stops_startup(tmp_path):
    with pytest.raises(DocumentError):
        create_app({"DOCS_DIR": str(tmp_path / "missing")})


def test_factory_reads_the_process_environment(docs, monkeypatch):
    monkeypatch.setenv("DOCS_DIR", str(docs))
    monkeypatch.delenv("LLM_PROXY_URL", raising=False)

    assert TestClient(create_app()).get("/health").json()["chunks"] == 3


@pytest.mark.parametrize("env", [{}, {"DOCS_DIR": ""}])
def test_default_documents_folder_is_data_at_project_root(env, tmp_path, monkeypatch):
    # Records the folder instead of reading it: tests must not depend on data/.
    folders = []
    monkeypatch.setattr("app.main.load_chunks", lambda folder: folders.append(folder) or [])
    monkeypatch.chdir(tmp_path)

    create_app(env)

    assert folders == [Path(__file__).resolve().parents[2] / "data"]
