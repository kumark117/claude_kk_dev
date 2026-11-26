import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app

NO_MATCH = "I couldn't find anything relevant in the documents."


@pytest.fixture
def docs(tmp_path):
    (tmp_path / "fruit.md").write_text(
        "# Fruit\n\nKiwi is green inside.\n\nBananas are yellow.", encoding="utf-8"
    )
    (tmp_path / "boats.txt").write_text("Sailing boats use wind.", encoding="utf-8")
    return tmp_path


def proxy_env(docs):
    return {
        "DOCS_DIR": str(docs),
        "LLM_PROXY_URL": "http://127.0.0.1:9/v1",
        "LLM_PROXY_MODEL": "m",
        "LLM_PROXY_API_KEY": "secret-key",
    }


def test_health_reports_chunk_count(docs):
    client = TestClient(create_app({"DOCS_DIR": str(docs)}))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "chunks": 3}


def test_factory_reads_the_process_environment(docs, monkeypatch):
    monkeypatch.delenv("LLM_PROXY_URL", raising=False)
    monkeypatch.setenv("DOCS_DIR", str(docs))

    client = TestClient(create_app())

    assert client.get("/health").json()["chunks"] == 3


def test_documents_are_read_once_at_startup(docs):
    client = TestClient(create_app({"DOCS_DIR": str(docs)}))
    (docs / "later.md").write_text("Added later.", encoding="utf-8")

    assert client.get("/health").json()["chunks"] == 3


def test_successful_ask_has_the_expected_shape(docs):
    client = TestClient(create_app({"DOCS_DIR": str(docs)}))

    response = client.post("/ask", json={"question": "  What colour is kiwi?  "})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "sources", "mode"}
    assert body["mode"] == "stub"
    assert body["answer"] == "[stub] Based on fruit.md: # Fruit\nKiwi is green inside."
    (source,) = body["sources"]
    assert set(source) == {"file", "text", "score"}
    assert source["file"] == "fruit.md"
    assert source["text"] == "# Fruit\nKiwi is green inside."
    assert source["score"] > 0


@pytest.mark.parametrize(
    "payload",
    [
        {"question": ""},
        {"question": "   \n\t "},
        {"question": "x" * 1001},
        {"question": 5},
        {},
    ],
)
def test_invalid_questions_get_422(docs, payload):
    client = TestClient(create_app({"DOCS_DIR": str(docs)}))

    assert client.post("/ask", json=payload).status_code == 422


@pytest.mark.parametrize("question", ["x", "x" * 1000, "  " + "x" * 1000 + "  "])
def test_length_limits_apply_after_trimming(docs, question):
    client = TestClient(create_app({"DOCS_DIR": str(docs)}))

    assert client.post("/ask", json={"question": question}).status_code == 200


def test_proxy_answer_and_mode(docs):
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "Green."}}]})

    client = TestClient(create_app(proxy_env(docs), transport=httpx.MockTransport(handler)))

    body = client.post("/ask", json={"question": "kiwi"}).json()

    assert body["answer"] == "Green."
    assert body["mode"] == "proxy"
    assert body["sources"][0]["file"] == "fruit.md"


def test_no_match_does_not_call_the_llm(docs):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    client = TestClient(create_app(proxy_env(docs), transport=httpx.MockTransport(handler)))

    response = client.post("/ask", json={"question": "zebra"})

    assert response.status_code == 200
    assert response.json() == {"answer": NO_MATCH, "sources": [], "mode": "proxy"}
    assert calls == []


def test_empty_folder_answers_with_no_match(tmp_path):
    client = TestClient(create_app({"DOCS_DIR": str(tmp_path)}))

    assert client.get("/health").json() == {"status": "ok", "chunks": 0}
    assert client.post("/ask", json={"question": "kiwi"}).json() == {
        "answer": NO_MATCH, "sources": [], "mode": "stub",
    }


def test_llm_error_returns_502_without_leaking(docs, caplog):
    caplog.set_level("DEBUG")

    def handler(request):
        return httpx.Response(500, text="upstream-secret-body")

    client = TestClient(create_app(proxy_env(docs), transport=httpx.MockTransport(handler)))

    response = client.post("/ask", json={"question": "kiwi"})

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert "secret-key" not in caplog.text
    assert "upstream-secret-body" not in caplog.text
