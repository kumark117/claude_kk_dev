from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app, resolve_docs_dir
from app.retrieval import DocumentError

FALLBACK = "I couldn't find anything relevant in the documents."
KEY = "dummy-key-123"


@pytest.fixture
def docs(tmp_path: Path) -> Path:
    (tmp_path / "volcano.md").write_text(
        "# Volcanoes\n\nLava is molten rock.\n\nAsh clouds drift.", encoding="utf-8"
    )
    (tmp_path / "ocean.txt").write_text("Tides follow the moon.", encoding="utf-8")
    return tmp_path


def proxy_env(docs: Path) -> dict[str, str]:
    return {
        "DOCS_DIR": str(docs),
        "LLM_PROXY_URL": "http://llm.test/v1",
        "LLM_PROXY_MODEL": "test-model",
        "LLM_PROXY_API_KEY": KEY,
    }


def client_for(env, transport=None) -> TestClient:
    return TestClient(create_app(env, llm_transport=transport))


def must_not_be_called(request):
    raise AssertionError("the LLM must not be called")


def test_health_reports_chunk_count(docs):
    response = client_for({"DOCS_DIR": str(docs)}).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "chunks": 3}


def test_health_with_empty_folder(tmp_path):
    response = client_for({"DOCS_DIR": str(tmp_path)}).get("/health")

    assert response.json() == {"status": "ok", "chunks": 0}


def test_factory_reads_the_process_environment(docs, monkeypatch):
    for name in ("LLM_PROXY_URL", "LLM_PROXY_API_KEY", "LLM_PROXY_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DOCS_DIR", str(docs))

    assert TestClient(create_app()).get("/health").json()["chunks"] == 3


@pytest.mark.parametrize("env", [{}, {"DOCS_DIR": ""}])
def test_default_docs_dir_is_data_beside_the_source_not_the_cwd(
    env, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    project_root = Path(__file__).resolve().parents[2]

    assert resolve_docs_dir(env) == project_root / "data"


def test_missing_docs_folder_fails_startup(tmp_path):
    with pytest.raises(DocumentError):
        create_app({"DOCS_DIR": str(tmp_path / "missing")})


def test_missing_model_fails_startup(docs):
    env = proxy_env(docs)
    del env["LLM_PROXY_MODEL"]

    with pytest.raises(ValueError, match="LLM_PROXY_MODEL"):
        create_app(env)


def test_documents_are_read_once_at_startup(docs):
    client = client_for({"DOCS_DIR": str(docs)})
    (docs / "later.md").write_text("Glaciers move slowly.", encoding="utf-8")

    assert client.get("/health").json()["chunks"] == 3
    assert client.post("/ask", json={"question": "glaciers"}).json()["sources"] == []


def test_successful_ask_returns_answer_sources_and_mode(docs):
    client = client_for({"DOCS_DIR": str(docs)})

    response = client.post("/ask", json={"question": "What is lava?"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "sources", "mode"}
    assert body["mode"] == "stub"
    text = "# Volcanoes\nLava is molten rock."
    assert body["answer"] == f"[stub] Based on volcano.md: {text}"
    [source] = body["sources"]
    assert set(source) == {"file", "text", "score"}
    assert (source["file"], source["text"]) == ("volcano.md", text)
    assert source["score"] > 0


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
def test_invalid_question_is_422(docs, payload):
    client = client_for({"DOCS_DIR": str(docs)})

    assert client.post("/ask", json=payload).status_code == 422


@pytest.mark.parametrize(
    "question", ["x", "x" * 1000, "   " + "x" * 1000 + "   "]
)
def test_question_length_is_checked_after_trimming(docs, question):
    client = client_for({"DOCS_DIR": str(docs)})

    assert client.post("/ask", json={"question": question}).status_code == 200


def test_question_is_trimmed_before_reaching_the_llm(docs):
    seen = []

    def handle(request):
        seen.append(request.content.decode())
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = client_for(proxy_env(docs), httpx.MockTransport(handle))
    client.post("/ask", json={"question": "   lava?   "})

    assert "lava?" in seen[0]
    assert "   lava?" not in seen[0]


def test_no_match_returns_fallback_without_calling_the_llm(docs):
    client = client_for(proxy_env(docs), httpx.MockTransport(must_not_be_called))

    response = client.post("/ask", json={"question": "zebra"})

    assert response.status_code == 200
    assert response.json() == {"answer": FALLBACK, "sources": [], "mode": "proxy"}


def test_no_match_in_stub_mode(docs):
    response = client_for({"DOCS_DIR": str(docs)}).post(
        "/ask", json={"question": "zebra"}
    )

    assert response.json() == {"answer": FALLBACK, "sources": [], "mode": "stub"}


def test_proxy_answer_is_returned(docs):
    def handle(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "From the proxy."}}]}
        )

    client = client_for(proxy_env(docs), httpx.MockTransport(handle))

    body = client.post("/ask", json={"question": "lava"}).json()

    assert (body["answer"], body["mode"]) == ("From the proxy.", "proxy")
    assert body["sources"][0]["file"] == "volcano.md"


def test_llm_error_is_502_without_leaking_key_or_upstream_body(docs, caplog):
    caplog.set_level("DEBUG")

    def handle(request):
        return httpx.Response(500, text=f"upstream-secret {KEY}")

    client = client_for(proxy_env(docs), httpx.MockTransport(handle))

    response = client.post("/ask", json={"question": "lava"})

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    for secret in (KEY, "upstream-secret"):
        assert secret not in response.text
        assert secret not in str(response.headers)
        assert secret not in caplog.text
