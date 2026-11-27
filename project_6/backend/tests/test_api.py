import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.llm import ConfigError
from app.main import create_app
from app.retrieval import DocumentError

KEY = "dummy-key-123"
FALLBACK = "I couldn't find anything relevant in the documents."


@pytest.fixture
def docs(tmp_path):
    (tmp_path / "kettle.md").write_text(
        "# Kettle\n\nThe kettle boils water.", encoding="utf-8"
    )
    (tmp_path / "garden.txt").write_text(
        "Tomatoes grow in the garden.\n\nRoses need pruning.", encoding="utf-8"
    )
    return tmp_path


def stub_client(docs) -> TestClient:
    return TestClient(create_app({"DOCS_DIR": str(docs)}))


def proxy_client(docs, handler) -> TestClient:
    env = {
        "DOCS_DIR": str(docs),
        "LLM_PROXY_URL": "http://127.0.0.1:9/v1",
        "LLM_PROXY_MODEL": "test-model",
        "LLM_PROXY_API_KEY": KEY,
    }
    return TestClient(create_app(env, transport=httpx.MockTransport(handler)))


def test_health_reports_the_chunk_count(docs):
    response = stub_client(docs).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "chunks": 3}


def test_empty_folder_starts_with_no_chunks(tmp_path):
    client = stub_client(tmp_path)

    assert client.get("/health").json() == {"status": "ok", "chunks": 0}
    assert client.post("/ask", json={"question": "kettle"}).json()["sources"] == []


def test_factory_reads_the_process_environment(docs, monkeypatch):
    monkeypatch.setenv("DOCS_DIR", str(docs))
    monkeypatch.delenv("LLM_PROXY_URL", raising=False)

    assert TestClient(create_app()).get("/health").json()["chunks"] == 3


@pytest.mark.parametrize("env", [{}, {"DOCS_DIR": ""}])
def test_default_docs_dir_is_data_at_the_project_root(
    env, docs, tmp_path, monkeypatch
):
    root = tmp_path / "project"
    (root / "data").mkdir(parents=True)
    (root / "data" / "only.md").write_text("one chunk", encoding="utf-8")
    source = root / "backend" / "app" / "main.py"
    monkeypatch.setattr(main, "__file__", str(source))
    monkeypatch.chdir(docs)

    assert TestClient(create_app(env)).get("/health").json()["chunks"] == 1


def test_documents_are_read_once_at_startup(docs):
    client = stub_client(docs)
    (docs / "late.md").write_text("zeppelin", encoding="utf-8")

    assert client.get("/health").json()["chunks"] == 3
    assert client.post("/ask", json={"question": "zeppelin"}).json()["sources"] == []


def test_missing_docs_dir_stops_startup(tmp_path):
    with pytest.raises(DocumentError):
        create_app({"DOCS_DIR": str(tmp_path / "missing")})


def test_missing_model_stops_startup(docs):
    with pytest.raises(ConfigError):
        create_app({"DOCS_DIR": str(docs), "LLM_PROXY_URL": "http://127.0.0.1:9"})


def test_successful_ask_has_the_expected_shape(docs):
    response = stub_client(docs).post(
        "/ask", json={"question": "  What boils water?  "}
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "sources", "mode"}
    assert body["mode"] == "stub"
    assert body["answer"] == (
        "[stub] Based on kettle.md: # Kettle\nThe kettle boils water."
    )
    (source,) = body["sources"]
    assert set(source) == {"file", "text", "score"}
    assert source["file"] == "kettle.md"
    assert source["text"] == "# Kettle\nThe kettle boils water."
    assert isinstance(source["score"], float) and source["score"] > 0


def test_successful_proxy_ask(docs):
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "yes"}}]})

    body = proxy_client(docs, handler).post("/ask", json={"question": "kettle"}).json()

    assert body["answer"] == "yes"
    assert body["mode"] == "proxy"
    assert body["sources"][0]["file"] == "kettle.md"


@pytest.mark.parametrize("question", ["k", "k" * 1000, "  " + "k" * 1000 + "  "])
def test_question_length_limits_are_inclusive_after_trimming(docs, question):
    response = stub_client(docs).post("/ask", json={"question": question})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        {"question": ""},
        {"question": "  \n\t "},
        {"question": "k" * 1001},
        {"question": 5},
        {"question": None},
        {},
    ],
)
def test_invalid_questions_get_422(docs, payload):
    assert stub_client(docs).post("/ask", json=payload).status_code == 422


@pytest.mark.parametrize("question", ["zebra", "what is the"])
def test_no_match_gives_the_fallback_without_calling_the_llm(docs, question):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    response = proxy_client(docs, handler).post("/ask", json={"question": question})

    assert response.status_code == 200
    assert response.json() == {"answer": FALLBACK, "sources": [], "mode": "proxy"}
    assert calls == []


def test_llm_error_gives_502_without_leaking(docs, caplog):
    def handler(request):
        return httpx.Response(500, text="upstream secret body")

    with caplog.at_level("DEBUG"):
        response = proxy_client(docs, handler).post(
            "/ask", json={"question": "kettle"}
        )

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    for text in (response.text, caplog.text):
        assert KEY not in text
        assert "upstream secret body" not in text
