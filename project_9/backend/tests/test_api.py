import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app, docs_dir
from app.retrieval import DocumentsError

NO_MATCH = "I couldn't find anything relevant in the documents."


@pytest.fixture
def docs(tmp_path):
    (tmp_path / "birds.md").write_text(
        "# Birds\n\nPenguins huddle to keep warm.\n\nOwls hunt at night.",
        encoding="utf-8",
    )
    (tmp_path / "sea.txt").write_text("The tide turns twice a day.", encoding="utf-8")
    return tmp_path


def client_for(docs, extra_env=None, handler=None):
    env = {"DOCS_DIR": str(docs), **(extra_env or {})}
    transport = httpx.MockTransport(handler) if handler else None
    return TestClient(create_app(env, transport=transport))


PROXY_ENV = {
    "LLM_PROXY_URL": "http://127.0.0.1:9999/v1",
    "LLM_PROXY_API_KEY": "secret-key",
    "LLM_PROXY_MODEL": "test-model",
}


def test_health_reports_chunk_count(docs):
    response = client_for(docs).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "chunks": 3}


def test_empty_folder_is_allowed(tmp_path):
    client = client_for(tmp_path)

    assert client.get("/health").json() == {"status": "ok", "chunks": 0}
    assert client.post("/ask", json={"question": "penguins"}).json()["sources"] == []


def test_successful_ask_has_the_documented_shape(docs):
    response = client_for(docs).post(
        "/ask", json={"question": "How do penguins keep warm?"}
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "sources", "mode"}
    assert body["mode"] == "stub"
    assert body["answer"] == (
        "[stub] Based on birds.md: # Birds\nPenguins huddle to keep warm."
    )
    source = body["sources"][0]
    assert set(source) == {"file", "text", "score"}
    assert source["file"] == "birds.md"
    assert source["text"] == "# Birds\nPenguins huddle to keep warm."
    assert source["score"] > 0


def test_proxy_mode_returns_the_proxy_answer(docs):
    def handler(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "from proxy"}}]}
        )

    body = client_for(docs, PROXY_ENV, handler).post(
        "/ask", json={"question": "penguins"}
    ).json()

    assert body["answer"] == "from proxy"
    assert body["mode"] == "proxy"
    assert body["sources"][0]["file"] == "birds.md"


def test_question_is_trimmed(docs):
    sent = []

    def handler(request):
        sent.append(request.content.decode())
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client_for(docs, PROXY_ENV, handler).post(
        "/ask", json={"question": "  \n penguins \t "}
    )

    user_message = json.loads(sent[0])["messages"][1]["content"]
    assert user_message.endswith("Question: penguins")


@pytest.mark.parametrize(
    "question", ["a" * 1000, "  " + "a" * 1000 + "  ", "x"]
)
def test_accepts_questions_of_1_to_1000_characters_after_trimming(docs, question):
    response = client_for(docs).post("/ask", json={"question": question})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "payload",
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
def test_rejects_invalid_questions_with_422(docs, payload):
    response = client_for(docs).post("/ask", json=payload)

    assert response.status_code == 422


@pytest.mark.parametrize("question", ["zebra giraffe", "what is the"])
def test_no_match_answers_without_calling_the_llm(docs, question):
    def handler(request):
        raise AssertionError("the LLM must not be called")

    response = client_for(docs, PROXY_ENV, handler).post(
        "/ask", json={"question": question}
    )

    assert response.status_code == 200
    assert response.json() == {"answer": NO_MATCH, "sources": [], "mode": "proxy"}


def test_no_match_in_stub_mode(docs):
    response = client_for(docs).post("/ask", json={"question": "zebra"})

    assert response.json() == {"answer": NO_MATCH, "sources": [], "mode": "stub"}


def test_llm_error_is_502_without_leaking_key_or_upstream_body(docs, caplog):
    def handler(request):
        return httpx.Response(500, text="upstream-secret secret-key")

    caplog.set_level("DEBUG")
    response = client_for(docs, PROXY_ENV, handler).post(
        "/ask", json={"question": "penguins"}
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert "secret-key" not in caplog.text
    assert "upstream-secret" not in caplog.text


def test_documents_are_read_only_at_startup(docs):
    client = client_for(docs)
    (docs / "late.md").write_text("Zebras have stripes.", encoding="utf-8")

    assert client.post("/ask", json={"question": "zebras"}).json()["sources"] == []


def test_missing_documents_folder_fails_startup(tmp_path):
    with pytest.raises(DocumentsError):
        create_app({"DOCS_DIR": str(tmp_path / "missing")})


def test_proxy_without_model_fails_startup(docs):
    with pytest.raises(ValueError, match="LLM_PROXY_MODEL"):
        create_app({"DOCS_DIR": str(docs), "LLM_PROXY_URL": "http://127.0.0.1:9/v1"})


def test_factory_reads_the_process_environment(docs, monkeypatch):
    monkeypatch.setenv("DOCS_DIR", str(docs))
    monkeypatch.delenv("LLM_PROXY_URL", raising=False)

    client = TestClient(create_app())

    assert client.get("/health").json()["chunks"] == 3


@pytest.mark.parametrize("env", [{}, {"DOCS_DIR": ""}])
def test_default_docs_folder_is_data_at_project_root(env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_root = Path(__file__).resolve().parents[2]

    assert docs_dir(env) == project_root / "data"
