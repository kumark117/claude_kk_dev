from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.llm import LLMConfigError
from app.main import create_app, docs_dir
from app.retrieval import DocumentError

FALLBACK = "I couldn't find anything relevant in the documents."
KEY = "dummy-secret-key"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "DOCS_DIR",
        "LLM_PROXY_URL",
        "LLM_PROXY_API_KEY",
        "LLM_PROXY_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def docs(tmp_path, monkeypatch) -> Path:
    (tmp_path / "moon.md").write_text(
        "# Moon\n\nThe Moon orbits the Earth.\n", encoding="utf-8"
    )
    (tmp_path / "bread.txt").write_text(
        "Bread needs flour and water.\n\nYeast makes dough rise.\n", encoding="utf-8"
    )
    monkeypatch.setenv("DOCS_DIR", str(tmp_path))
    return tmp_path


def proxy_client(monkeypatch, handler) -> TestClient:
    monkeypatch.setenv("LLM_PROXY_URL", "http://llm.test/v1")
    monkeypatch.setenv("LLM_PROXY_MODEL", "test-model")
    monkeypatch.setenv("LLM_PROXY_API_KEY", KEY)
    return TestClient(create_app(transport=httpx.MockTransport(handler)))


def test_health_reports_chunk_count(docs):
    response = TestClient(create_app()).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "chunks": 3}


def test_documents_are_read_once_at_startup(docs):
    client = TestClient(create_app())
    (docs / "new.md").write_text("Giraffes are tall.", encoding="utf-8")

    assert client.get("/health").json()["chunks"] == 3
    assert client.post("/ask", json={"question": "giraffes"}).json()["sources"] == []


def test_successful_ask_has_the_documented_shape(docs):
    response = TestClient(create_app()).post(
        "/ask", json={"question": "What orbits the Earth?"}
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "sources", "mode"}
    assert body["mode"] == "stub"
    assert body["answer"] == (
        "[stub] Based on moon.md: # Moon\nThe Moon orbits the Earth."
    )
    [source] = body["sources"]
    assert set(source) == {"file", "text", "score"}
    assert source["file"] == "moon.md"
    assert source["text"] == "# Moon\nThe Moon orbits the Earth."
    assert isinstance(source["score"], float) and source["score"] > 0


def test_proxy_mode_returns_the_upstream_answer(docs, monkeypatch):
    client = proxy_client(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": "From upstream"}}]}
        ),
    )

    body = client.post("/ask", json={"question": "yeast"}).json()

    assert body["answer"] == "From upstream"
    assert body["mode"] == "proxy"
    assert [s["file"] for s in body["sources"]] == ["bread.txt"]


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
    response = TestClient(create_app()).post("/ask", json=payload)

    assert response.status_code == 422


@pytest.mark.parametrize(
    "question", ["x", "x" * 1000, "  " + "x" * 1000 + "  \n"]
)
def test_question_length_is_checked_after_trimming(docs, question):
    response = TestClient(create_app()).post("/ask", json={"question": question})

    assert response.status_code == 200


def test_question_is_trimmed_before_it_reaches_the_llm(docs, monkeypatch):
    seen: list[bytes] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    proxy_client(monkeypatch, handle).post("/ask", json={"question": "   yeast   "})

    assert b"   yeast" not in seen[0]
    assert b"yeast   " not in seen[0]


@pytest.mark.parametrize("mode", ["stub", "proxy"])
def test_no_match_gives_fallback_without_calling_the_llm(docs, monkeypatch, mode):
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    client = (
        proxy_client(monkeypatch, handle)
        if mode == "proxy"
        else TestClient(create_app())
    )

    response = client.post("/ask", json={"question": "quantum chromodynamics"})

    assert response.status_code == 200
    assert response.json() == {"answer": FALLBACK, "sources": [], "mode": mode}
    assert calls == []


def test_llm_error_is_502_without_leaking_key_or_body(docs, monkeypatch, caplog):
    client = proxy_client(
        monkeypatch,
        lambda request: httpx.Response(500, text=f"UPSTREAM-BODY {KEY}"),
    )

    response = client.post("/ask", json={"question": "yeast"})

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    for secret in (KEY, "UPSTREAM-BODY"):
        assert secret not in response.text
        assert secret not in str(response.headers)
        assert secret not in caplog.text


def test_empty_docs_folder_starts_and_answers_with_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_DIR", str(tmp_path))
    client = TestClient(create_app())

    assert client.get("/health").json() == {"status": "ok", "chunks": 0}
    assert client.post("/ask", json={"question": "moon"}).json()["answer"] == FALLBACK


def test_missing_docs_folder_stops_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_DIR", str(tmp_path / "missing"))

    with pytest.raises(DocumentError):
        create_app()


def test_missing_model_stops_startup(docs, monkeypatch):
    monkeypatch.setenv("LLM_PROXY_URL", "http://llm.test/v1")

    with pytest.raises(LLMConfigError):
        create_app()


def test_each_app_reads_the_environment_when_created(docs, monkeypatch):
    first = TestClient(create_app())
    other = docs / "other"
    other.mkdir()
    monkeypatch.setenv("DOCS_DIR", str(other))
    second = TestClient(create_app())

    assert first.get("/health").json()["chunks"] == 3
    assert second.get("/health").json()["chunks"] == 0


@pytest.mark.parametrize("env", [{}, {"DOCS_DIR": ""}])
def test_default_docs_dir_is_data_at_the_project_root(env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert docs_dir(env) == Path(__file__).resolve().parents[2] / "data"


def test_docs_dir_comes_from_the_environment():
    assert docs_dir({"DOCS_DIR": "/somewhere/else"}) == Path("/somewhere/else")
