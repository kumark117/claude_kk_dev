from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.llm import ConfigError
from app.main import create_app, docs_dir
from app.retrieval import DocumentError

FALLBACK = "I couldn't find anything relevant in the documents."


@pytest.fixture
def docs(tmp_path):
    (tmp_path / "cats.md").write_text("# Cats\n\nCats purr when content.\n")
    (tmp_path / "volcano.txt").write_text("Volcanoes erupt molten lava.\n")
    return tmp_path


def client_for(docs, extra=None, handler=None):
    env = {"DOCS_DIR": str(docs), **(extra or {})}
    transport = httpx.MockTransport(handler) if handler else None
    return TestClient(create_app(env, transport=transport))


PROXY = {
    "LLM_PROXY_URL": "http://llm.test/v1",
    "LLM_PROXY_MODEL": "test-model",
    "LLM_PROXY_API_KEY": "secret-key",
}


def test_health_reports_chunk_count(docs):
    response = client_for(docs).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "chunks": 2}


def test_factory_reads_the_process_environment(docs, monkeypatch):
    monkeypatch.setenv("DOCS_DIR", str(docs))
    monkeypatch.delenv("LLM_PROXY_URL", raising=False)

    client = TestClient(create_app())

    assert client.get("/health").json()["chunks"] == 2


def test_separate_instances_are_independent(docs, tmp_path_factory):
    empty = tmp_path_factory.mktemp("empty")

    first, second = client_for(docs), client_for(empty)

    assert first.get("/health").json()["chunks"] == 2
    assert second.get("/health").json()["chunks"] == 0


@pytest.mark.parametrize("env", [{}, {"DOCS_DIR": ""}])
def test_default_docs_dir_is_data_at_project_root(env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert docs_dir(env) == Path(__file__).resolve().parents[2] / "data"


def test_documents_are_read_only_at_startup(docs):
    client = client_for(docs)
    (docs / "new.md").write_text("zebra")

    assert client.get("/health").json()["chunks"] == 2


def test_missing_docs_folder_stops_startup(tmp_path):
    with pytest.raises(DocumentError):
        create_app({"DOCS_DIR": str(tmp_path / "missing")})


def test_missing_model_stops_startup(docs):
    with pytest.raises(ConfigError):
        client_for(docs, {"LLM_PROXY_URL": "http://llm.test/v1"})


def test_successful_ask_shape_in_stub_mode(docs):
    response = client_for(docs).post("/ask", json={"question": "Do cats purr?"})

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


def test_successful_ask_in_proxy_mode(docs):
    def handler(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "canned"}}]}
        )

    response = client_for(docs, PROXY, handler).post(
        "/ask", json={"question": "lava"}
    )

    assert response.status_code == 200
    assert response.json()["answer"] == "canned"
    assert response.json()["mode"] == "proxy"
    assert response.json()["sources"][0]["file"] == "volcano.txt"


@pytest.mark.parametrize(
    "body",
    [
        {"question": ""},
        {"question": " \n\t "},
        {"question": "x" * 1001},
        {"question": 5},
        {"question": None},
        {},
        [],
    ],
)
def test_invalid_question_is_422(docs, body):
    assert client_for(docs).post("/ask", json=body).status_code == 422


@pytest.mark.parametrize(
    "content",
    [
        rb'{"question": "\ud800"}',  # an unpaired surrogate is not valid text
        rb'{"question": "ok", "\ud800": 1',
        rb'"\ud800"',
    ],
)
def test_unencodable_input_is_422_not_a_server_error(docs, content):
    response = client_for(docs).post(
        "/ask", content=content, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "question", ["x", "x" * 1000, "   " + "x" * 1000 + "   "]
)
def test_question_length_is_checked_after_trimming(docs, question):
    response = client_for(docs).post("/ask", json={"question": question})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "extra,mode", [(None, "stub"), (PROXY, "proxy")]
)
def test_no_match_returns_fallback_without_calling_llm(docs, extra, mode):
    def handler(request):
        raise AssertionError("LLM must not be called")

    response = client_for(docs, extra, handler).post(
        "/ask", json={"question": "giraffe"}
    )

    assert response.status_code == 200
    assert response.json() == {"answer": FALLBACK, "sources": [], "mode": mode}


def test_llm_error_is_502_without_leaking_key_or_body(docs, caplog):
    def handler(request):
        return httpx.Response(500, text="upstream-secret-body")

    caplog.set_level("DEBUG")
    response = client_for(docs, PROXY, handler).post(
        "/ask", json={"question": "lava"}
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    for leaked in ("secret-key", "upstream-secret-body"):
        assert leaked not in response.text
        assert leaked not in caplog.text
