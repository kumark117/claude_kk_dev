import json
import logging
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
    (tmp_path / "rockets.md").write_text(
        "# Rockets\n\nRockets burn fuel to reach orbit.\n\nBoosters fall away.",
        encoding="utf-8",
    )
    (tmp_path / "bread.txt").write_text("Dough needs yeast.", encoding="utf-8")
    return tmp_path


def client_for(docs, extra=None, handler=None):
    transport = httpx.MockTransport(handler) if handler else None
    env = {"DOCS_DIR": str(docs), **(extra or {})}
    return TestClient(create_app(env, transport=transport))


PROXY_ENV = {
    "LLM_PROXY_URL": "http://llm.test/v1",
    "LLM_PROXY_API_KEY": "dummy-secret-key",
    "LLM_PROXY_MODEL": "dummy-model",
}


def test_health_reports_the_chunk_count(docs):
    response = client_for(docs).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "chunks": 3}


def test_empty_folder_starts_and_answers_with_the_fallback(tmp_path):
    client = client_for(tmp_path)

    assert client.get("/health").json() == {"status": "ok", "chunks": 0}
    assert client.post("/ask", json={"question": "rockets"}).json()["sources"] == []


def test_successful_stub_ask_has_the_expected_shape(docs):
    response = client_for(docs).post("/ask", json={"question": "How do rockets fly?"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "sources", "mode"}
    assert body["mode"] == "stub"
    assert body["answer"] == (
        "[stub] Based on rockets.md: # Rockets\nRockets burn fuel to reach orbit."
    )
    [source] = body["sources"]
    assert set(source) == {"file", "text", "score"}
    assert source["file"] == "rockets.md"
    assert source["text"] == "# Rockets\nRockets burn fuel to reach orbit."
    assert isinstance(source["score"], float) and source["score"] > 0


def test_successful_proxy_ask(docs):
    def handler(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "from proxy"}}]}
        )

    response = client_for(docs, PROXY_ENV, handler).post(
        "/ask", json={"question": "yeast"}
    )

    assert response.status_code == 200
    assert response.json()["answer"] == "from proxy"
    assert response.json()["mode"] == "proxy"
    assert response.json()["sources"][0]["file"] == "bread.txt"


def test_question_is_trimmed_before_use(docs):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content)["messages"][1]["content"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client_for(docs, PROXY_ENV, handler).post(
        "/ask", json={"question": "  \n yeast \t  "}
    )

    assert seen[0].endswith("yeast")
    assert "yeast \t" not in seen[0]


@pytest.mark.parametrize(
    "body",
    [
        {"question": ""},
        {"question": "   \n\t "},
        {"question": "a" * 1001},
        {"question": 42},
        {"question": None},
        {},
        [],
    ],
    ids=["empty", "blank", "too long", "number", "null", "missing", "array"],
)
def test_invalid_questions_get_422(docs, body):
    assert client_for(docs).post("/ask", json=body).status_code == 422


@pytest.mark.parametrize(
    "question",
    ["a", "a" * 1000, "  " + "a" * 1000 + "  "],
    ids=["one", "thousand", "thousand after trimming"],
)
def test_boundary_question_lengths_are_accepted(docs, question):
    response = client_for(docs).post("/ask", json={"question": question})

    assert response.status_code == 200


@pytest.mark.parametrize("extra", [{}, PROXY_ENV], ids=["stub", "proxy"])
def test_no_match_returns_fallback_without_calling_the_llm(docs, extra):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    response = client_for(docs, extra, handler).post(
        "/ask", json={"question": "zeppelin"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "answer": FALLBACK,
        "sources": [],
        "mode": "proxy" if extra else "stub",
    }
    assert calls == []


def test_llm_error_gives_502_without_leaking_key_or_upstream_body(docs, caplog):
    caplog.set_level(logging.DEBUG)

    def handler(request):
        return httpx.Response(500, text="upstream-private-body")

    response = client_for(docs, PROXY_ENV, handler).post(
        "/ask", json={"question": "yeast"}
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    for private in ("dummy-secret-key", "upstream-private-body"):
        assert private not in response.text
        assert private not in str(response.headers)
        assert private not in caplog.text


def test_factory_reads_the_process_environment(docs, monkeypatch):
    monkeypatch.setenv("DOCS_DIR", str(docs))
    monkeypatch.delenv("LLM_PROXY_URL", raising=False)

    client = TestClient(create_app())

    assert client.get("/health").json()["chunks"] == 3


def test_documents_are_read_once_at_startup(docs):
    client = client_for(docs)
    (docs / "late.txt").write_text("zeppelin", encoding="utf-8")

    assert client.get("/health").json()["chunks"] == 3
    assert client.post("/ask", json={"question": "zeppelin"}).json()["sources"] == []


@pytest.mark.parametrize("env", [{}, {"DOCS_DIR": ""}], ids=["unset", "empty"])
def test_default_docs_folder_is_data_at_the_project_root(env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert docs_dir(env) == Path(__file__).resolve().parents[2] / "data"


def test_startup_fails_for_a_missing_docs_folder(tmp_path):
    with pytest.raises(DocumentError):
        create_app({"DOCS_DIR": str(tmp_path / "missing")})


def test_startup_fails_in_proxy_mode_without_a_model(docs):
    with pytest.raises(ConfigError):
        create_app({"DOCS_DIR": str(docs), "LLM_PROXY_URL": "http://llm.test/v1"})
