from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app, resolve_docs_dir
from app.retrieval import DocumentError

KEY = "dummy-test-key"
FALLBACK = "I couldn't find anything relevant in the documents."


@pytest.fixture
def docs(tmp_path):
    (tmp_path / "penguins.md").write_text(
        "# Penguins\n\nPenguins eat fish.\n\nPenguins cannot fly.", encoding="utf-8"
    )
    (tmp_path / "volcano.txt").write_text(
        "Volcanoes erupt molten lava.", encoding="utf-8"
    )
    return tmp_path


def client_for(docs, handler=None, **env):
    transport = httpx.MockTransport(handler) if handler else None
    return TestClient(create_app({"DOCS_DIR": str(docs), **env}, transport))


def proxy_client(docs, handler):
    return client_for(
        docs,
        handler,
        LLM_PROXY_URL="http://llm.test/v1",
        LLM_PROXY_MODEL="test-model",
        LLM_PROXY_API_KEY=KEY,
    )


def test_health_reports_chunk_count(docs):
    response = client_for(docs).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "chunks": 3}


def test_successful_ask_response_shape(docs):
    response = client_for(docs).post("/ask", json={"question": "What erupts lava?"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "sources", "mode"}
    assert body["mode"] == "stub"
    assert body["answer"] == (
        "[stub] Based on volcano.txt: Volcanoes erupt molten lava."
    )
    [source] = body["sources"]
    assert set(source) == {"file", "text", "score"}
    assert source["file"] == "volcano.txt"
    assert source["text"] == "Volcanoes erupt molten lava."
    assert isinstance(source["score"], float) and source["score"] > 0


def test_proxy_answer_and_mode(docs):
    def handler(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "From the proxy"}}]}
        )

    body = proxy_client(docs, handler).post("/ask", json={"question": "lava"}).json()

    assert body["answer"] == "From the proxy"
    assert body["mode"] == "proxy"
    assert body["sources"][0]["file"] == "volcano.txt"


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
)
def test_invalid_questions_get_422(docs, payload):
    assert client_for(docs).post("/ask", json=payload).status_code == 422


@pytest.mark.parametrize("question", ["x", "x" * 1000, "  " + "x" * 1000 + "  "])
def test_length_limits_apply_after_trimming(docs, question):
    response = client_for(docs).post("/ask", json={"question": question})

    assert response.status_code == 200


def test_no_match_does_not_call_the_llm(docs):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    response = proxy_client(docs, handler).post("/ask", json={"question": "zebra"})

    assert response.status_code == 200
    assert response.json() == {"answer": FALLBACK, "sources": [], "mode": "proxy"}
    assert calls == []


def test_empty_folder_gives_fallback(tmp_path):
    client = client_for(tmp_path)

    assert client.get("/health").json() == {"status": "ok", "chunks": 0}
    assert client.post("/ask", json={"question": "lava"}).json() == {
        "answer": FALLBACK,
        "sources": [],
        "mode": "stub",
    }


def test_llm_error_is_502_without_key_or_upstream_body(docs, caplog):
    caplog.set_level("DEBUG")

    def handler(request):
        return httpx.Response(500, text="UPSTREAM-SECRET")

    response = proxy_client(docs, handler).post("/ask", json={"question": "lava"})

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    for text in (response.text, str(response.headers), caplog.text):
        assert KEY not in text
        assert "UPSTREAM-SECRET" not in text


def test_documents_are_read_once_at_startup(docs):
    client = client_for(docs)
    (docs / "late.md").write_text("zebra", encoding="utf-8")

    assert client.get("/health").json()["chunks"] == 3
    assert client.post("/ask", json={"question": "zebra"}).json()["sources"] == []


def test_factory_reads_the_process_environment(docs, monkeypatch):
    monkeypatch.setenv("DOCS_DIR", str(docs))
    monkeypatch.delenv("LLM_PROXY_URL", raising=False)

    client = TestClient(create_app())

    assert client.get("/health").json()["chunks"] == 3


def test_missing_docs_folder_stops_startup(tmp_path):
    with pytest.raises(DocumentError):
        create_app({"DOCS_DIR": str(tmp_path / "missing")})


@pytest.mark.parametrize("env", [{}, {"DOCS_DIR": ""}])
def test_default_docs_dir_is_data_at_project_root(env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert resolve_docs_dir(env) == Path(__file__).resolve().parents[2] / "data"
