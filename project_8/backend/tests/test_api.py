import httpx
import pytest
from fastapi.testclient import TestClient

from app.llm import LLMConfigError
from app.main import create_app, docs_dir_from_env
from app.retrieval import StartupError

KEY = "dummy-key-123"
NO_MATCH = "I couldn't find anything relevant in the documents."


@pytest.fixture
def docs(tmp_path, monkeypatch):
    (tmp_path / "tea.md").write_text("# Tea\n\nBoil the kettle.", encoding="utf-8")
    (tmp_path / "bikes.txt").write_text("Oil the chain.\n\nPump the tyres.", encoding="utf-8")
    for name in ("LLM_PROXY_URL", "LLM_PROXY_API_KEY", "LLM_PROXY_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DOCS_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def proxy_env(docs, monkeypatch):
    monkeypatch.setenv("LLM_PROXY_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("LLM_PROXY_MODEL", "test-model")
    monkeypatch.setenv("LLM_PROXY_API_KEY", KEY)


def client(handler=None):
    def forbidden(request):
        raise AssertionError("the LLM must not be called")

    return TestClient(create_app(transport=httpx.MockTransport(handler or forbidden)))


def ask(api, question):
    return api.post("/ask", json={"question": question})


def test_health_reports_chunk_count(docs):
    response = client().get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "chunks": 3}


def test_successful_ask_has_answer_sources_and_mode(docs):
    response = ask(client(), "How do I boil a kettle?")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "sources", "mode"}
    assert body["mode"] == "stub"
    assert body["answer"] == "[stub] Based on tea.md: # Tea\nBoil the kettle."
    [source] = body["sources"]
    assert set(source) == {"file", "text", "score"}
    assert source["file"] == "tea.md"
    assert source["text"] == "# Tea\nBoil the kettle."
    assert isinstance(source["score"], float) and source["score"] > 0


def test_documents_are_read_once_at_startup(docs):
    api = client()
    (docs / "new.md").write_text("A zebra.", encoding="utf-8")

    assert api.get("/health").json()["chunks"] == 3
    assert ask(api, "zebra").json()["sources"] == []


@pytest.mark.parametrize("question", ["", "   \n\t ", "x" * 1001])
def test_blank_and_over_long_questions_are_422(docs, question):
    assert ask(client(), question).status_code == 422


@pytest.mark.parametrize("body", [{}, {"question": 5}, {"question": None}, [], "kettle"])
def test_malformed_bodies_are_422(docs, body):
    assert client().post("/ask", json=body).status_code == 422


@pytest.mark.parametrize("question", ["x", "x" * 1000, "  " + "x" * 1000 + "  "])
def test_length_limits_apply_after_trimming(docs, question):
    assert ask(client(), question).status_code == 200


def test_no_match_gives_fallback_without_calling_the_llm(proxy_env):
    response = ask(client(), "zebra migration")

    assert response.status_code == 200
    assert response.json() == {"answer": NO_MATCH, "sources": [], "mode": "proxy"}


def test_proxy_answer_uses_the_trimmed_question(proxy_env):
    seen = []

    def handler(request):
        seen.append(request.content.decode())
        return httpx.Response(200, json={"choices": [{"message": {"content": "Canned."}}]})

    response = ask(client(handler), "   boil kettle   ")

    assert response.status_code == 200
    assert response.json()["answer"] == "Canned."
    assert response.json()["mode"] == "proxy"
    assert "Question: boil kettle\"" in seen[0]


def test_llm_error_is_502_without_leaking_key_or_upstream_body(proxy_env, caplog):
    def handler(request):
        return httpx.Response(500, text=f"upstream secret {KEY}")

    response = ask(client(handler), "boil kettle")

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert KEY not in caplog.text
    assert "upstream secret" not in caplog.text


@pytest.mark.parametrize("env", [{}, {"DOCS_DIR": ""}])
def test_default_docs_folder_is_data_at_the_project_root(env, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    folder = docs_dir_from_env(env)

    assert folder.name == "data"
    assert (folder.parent / "backend" / "app" / "main.py").is_file()


def test_docs_dir_setting_wins(tmp_path):
    assert docs_dir_from_env({"DOCS_DIR": str(tmp_path)}) == tmp_path


def test_missing_docs_folder_stops_startup(docs, monkeypatch):
    monkeypatch.setenv("DOCS_DIR", str(docs / "missing"))

    with pytest.raises(StartupError):
        create_app()


def test_proxy_without_model_stops_startup(proxy_env, monkeypatch):
    monkeypatch.delenv("LLM_PROXY_MODEL")

    with pytest.raises(LLMConfigError):
        create_app()
