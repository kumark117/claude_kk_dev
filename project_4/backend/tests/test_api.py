from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.llm import ConfigError
from app.main import create_app
from app.retrieval import DocumentError

NO_MATCH = "I couldn't find anything relevant in the documents."
KEY = "dummy-test-key"


@pytest.fixture
def docs(tmp_path):
    (tmp_path / "fruit.md").write_text(
        "# Fruit\n\nMangoes ripen in summer.\n\nBananas are yellow.",
        encoding="utf-8",
    )
    return tmp_path


def stub_client(docs):
    return TestClient(create_app({"DOCS_DIR": str(docs)}))


def proxy_client(docs, handler):
    env = {
        "DOCS_DIR": str(docs),
        "LLM_PROXY_URL": "http://llm.test/v1",
        "LLM_PROXY_API_KEY": KEY,
        "LLM_PROXY_MODEL": "test-model",
    }
    return TestClient(create_app(env, transport=httpx.MockTransport(handler)))


def test_health_reports_the_chunk_count(docs):
    response = stub_client(docs).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "chunks": 2}


def test_successful_ask_has_the_expected_shape(docs):
    response = stub_client(docs).post("/ask", json={"question": "When do mangoes ripen?"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "sources", "mode"}
    assert body["mode"] == "stub"
    assert body["answer"] == (
        "[stub] Based on fruit.md: # Fruit\nMangoes ripen in summer."
    )
    assert len(body["sources"]) == 1
    source = body["sources"][0]
    assert set(source) == {"file", "text", "score"}
    assert source["file"] == "fruit.md"
    assert source["text"] == "# Fruit\nMangoes ripen in summer."
    assert isinstance(source["score"], float) and source["score"] > 0


@pytest.mark.parametrize(
    "payload",
    [
        {"question": ""},
        {"question": "   \n\t "},
        {"question": "x" * 1001},
        {"question": 5},
        {"question": None},
        {},
    ],
    ids=["empty", "blank", "too-long", "number", "null", "missing"],
)
def test_invalid_questions_get_422(docs, payload):
    assert stub_client(docs).post("/ask", json=payload).status_code == 422


@pytest.mark.parametrize(
    "question",
    ["x" * 1000, "  " + "x" * 1000 + "  ", "x"],
    ids=["max", "max-after-trim", "min"],
)
def test_question_length_is_checked_after_trimming(docs, question):
    assert stub_client(docs).post("/ask", json={"question": question}).status_code == 200


def test_question_is_trimmed_before_reaching_the_llm(docs, monkeypatch):
    seen = []

    def fake(question, chunks, config, transport=None):
        seen.append(question)
        return "ok"

    monkeypatch.setattr(main, "generate_answer", fake)

    stub_client(docs).post("/ask", json={"question": "  mangoes  "})

    assert seen == ["mangoes"]


def test_no_match_answers_without_calling_the_llm(docs):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    response = proxy_client(docs, handler).post("/ask", json={"question": "zeppelin"})

    assert response.status_code == 200
    assert response.json() == {"answer": NO_MATCH, "sources": [], "mode": "proxy"}
    assert calls == []


def test_proxy_answer_is_returned(docs):
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "Hi"}}]})

    response = proxy_client(docs, handler).post("/ask", json={"question": "bananas"})

    assert response.status_code == 200
    assert response.json()["answer"] == "Hi"
    assert response.json()["mode"] == "proxy"


def test_llm_error_gives_502_without_leaking_key_or_upstream_body(docs, caplog):
    def handler(request):
        return httpx.Response(500, text=f"upstream secret body {KEY}")

    with caplog.at_level("DEBUG"):
        response = proxy_client(docs, handler).post(
            "/ask", json={"question": "bananas"}
        )

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert KEY not in caplog.text
    assert "upstream secret body" not in caplog.text


def test_documents_are_read_once_at_startup(docs):
    client = stub_client(docs)
    (docs / "later.md").write_text("Zeppelins float.", encoding="utf-8")

    assert client.get("/health").json()["chunks"] == 2
    assert client.post("/ask", json={"question": "zeppelins"}).json()["sources"] == []


def test_factory_reads_the_process_environment(docs, monkeypatch):
    monkeypatch.setenv("DOCS_DIR", str(docs))
    monkeypatch.delenv("LLM_PROXY_URL", raising=False)

    assert TestClient(create_app()).get("/health").json()["chunks"] == 2


@pytest.mark.parametrize("env", [{}, {"DOCS_DIR": ""}], ids=["unset", "empty"])
def test_default_docs_folder_is_data_at_the_project_root(env, tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    seen = []

    def fake_load(folder):
        seen.append(Path(folder))
        return []

    monkeypatch.setattr(main, "load_chunks", fake_load)
    monkeypatch.chdir(tmp_path)

    create_app(env)

    assert seen == [root / "data"]


def test_missing_docs_folder_fails_startup(tmp_path):
    with pytest.raises(DocumentError):
        create_app({"DOCS_DIR": str(tmp_path / "missing")})


def test_missing_proxy_model_fails_startup(docs):
    with pytest.raises(ConfigError):
        create_app({"DOCS_DIR": str(docs), "LLM_PROXY_URL": "http://llm.test/v1"})
