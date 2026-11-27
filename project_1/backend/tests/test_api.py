from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.retrieval import DocumentError

KEY = "test-secret-key"
SECRET_BODY = "upstream-secret-body"
NO_MATCH = "I couldn't find anything relevant in the documents."
ENV_NAMES = ("DOCS_DIR", "LLM_PROXY_URL", "LLM_PROXY_API_KEY", "LLM_PROXY_MODEL")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def docs(tmp_path, monkeypatch):
    (tmp_path / "bees.md").write_text("# Bees\n\nBees make honey.", encoding="utf-8")
    (tmp_path / "bikes.txt").write_text("Bikes have wheels.", encoding="utf-8")
    monkeypatch.setenv("DOCS_DIR", str(tmp_path))
    return tmp_path


def client_for(transport=None):
    return TestClient(main.create_app(transport=transport))


def use_proxy(monkeypatch, handler):
    monkeypatch.setenv("LLM_PROXY_URL", "http://proxy.test/v1")
    monkeypatch.setenv("LLM_PROXY_API_KEY", KEY)
    monkeypatch.setenv("LLM_PROXY_MODEL", "test-model")
    return client_for(httpx.MockTransport(handler))


def ask(client, question):
    return client.post("/ask", json={"question": question})


def test_health_reports_the_chunk_count(docs):
    response = client_for().get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "chunks": 2}


def test_successful_ask_returns_answer_sources_and_mode(docs):
    response = ask(client_for(), "Who makes honey?")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "sources", "mode"}
    assert body["answer"] == "[stub] Based on bees.md: # Bees\nBees make honey."
    assert body["mode"] == "stub"
    [source] = body["sources"]
    assert set(source) == {"file", "text", "score"}
    assert source["file"] == "bees.md"
    assert source["text"] == "# Bees\nBees make honey."
    assert source["score"] > 0


def test_proxy_mode_returns_the_proxy_answer(docs, monkeypatch):
    reply = {"choices": [{"message": {"content": "From the proxy"}}]}
    client = use_proxy(monkeypatch, lambda request: httpx.Response(200, json=reply))

    body = ask(client, "honey").json()

    assert body["answer"] == "From the proxy"
    assert body["mode"] == "proxy"
    assert body["sources"][0]["file"] == "bees.md"


@pytest.mark.parametrize("question", ["", "   \n\t ", "x" * 1001])
def test_blank_and_over_long_questions_are_rejected(docs, question):
    assert ask(client_for(), question).status_code == 422


@pytest.mark.parametrize("body", [{}, {"question": 5}, {"question": None}])
def test_missing_or_non_string_question_is_rejected(docs, body):
    assert client_for().post("/ask", json=body).status_code == 422


def test_question_is_trimmed_before_the_length_check(docs):
    question = "  " + "honey".ljust(1000, "!") + "  "

    assert ask(client_for(), question).status_code == 200


def test_the_llm_receives_the_trimmed_question(docs, monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.content.decode())
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    ask(use_proxy(monkeypatch, handler), "   honey?   ")

    assert "Question: honey?\"" in seen[0]


def test_no_match_gives_the_fallback_without_calling_the_llm(docs, monkeypatch):
    def handler(request):
        raise AssertionError("the LLM must not be called")

    response = ask(use_proxy(monkeypatch, handler), "zebra")

    assert response.status_code == 200
    assert response.json() == {"answer": NO_MATCH, "sources": [], "mode": "proxy"}


def test_empty_documents_folder_gives_the_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_DIR", str(tmp_path))

    body = ask(client_for(), "honey").json()

    assert body == {"answer": NO_MATCH, "sources": [], "mode": "stub"}


def test_llm_error_gives_502_without_leaking_anything(docs, monkeypatch, caplog):
    caplog.set_level("DEBUG")
    client = use_proxy(
        monkeypatch, lambda request: httpx.Response(500, text=SECRET_BODY)
    )

    response = ask(client, "honey")

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    for text in (response.text, str(response.headers), caplog.text):
        assert KEY not in text
        assert SECRET_BODY not in text


def test_documents_are_read_once_at_startup(docs):
    client = client_for()
    (docs / "zebra.md").write_text("Zebras have stripes.", encoding="utf-8")

    assert ask(client, "zebras").json()["sources"] == []


@pytest.mark.parametrize("value", [None, ""])
def test_default_documents_folder_is_data_at_the_project_root(
    value, tmp_path, monkeypatch
):
    if value is not None:
        monkeypatch.setenv("DOCS_DIR", value)
    monkeypatch.chdir(tmp_path)

    assert main.DEFAULT_DOCS_DIR == Path(__file__).resolve().parents[2] / "data"
    assert client_for().get("/health").json()["chunks"] > 0


def test_missing_documents_folder_stops_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_DIR", str(tmp_path / "nope"))

    with pytest.raises(DocumentError, match="nope"):
        main.create_app()


def test_proxy_mode_without_a_model_stops_startup(docs, monkeypatch):
    monkeypatch.setenv("LLM_PROXY_URL", "http://proxy.test/v1")

    with pytest.raises(ValueError, match="LLM_PROXY_MODEL"):
        main.create_app()
