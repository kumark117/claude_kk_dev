from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app, docs_dir
from app.retrieval import DocumentError

FALLBACK = "I couldn't find anything relevant in the documents."
KEY = "dummy-test-key"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    (tmp_path / "lava.md").write_text(
        "# Lava\n\nLava is molten rock.\n\nAsh clouds travel far.", encoding="utf-8"
    )
    for name in ("LLM_PROXY_URL", "LLM_PROXY_API_KEY", "LLM_PROXY_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DOCS_DIR", str(tmp_path))


def use_proxy(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("LLM_PROXY_API_KEY", KEY)
    monkeypatch.setenv("LLM_PROXY_MODEL", "test-model")


def test_health_counts_chunks():
    response = TestClient(create_app()).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "chunks": 2}


def test_empty_folder_is_allowed(monkeypatch, tmp_path):
    (tmp_path / "empty").mkdir()
    monkeypatch.setenv("DOCS_DIR", str(tmp_path / "empty"))
    client = TestClient(create_app())

    assert client.get("/health").json()["chunks"] == 0
    assert client.post("/ask", json={"question": "lava"}).json()["sources"] == []


def test_documents_are_read_only_at_startup(tmp_path):
    client = TestClient(create_app())
    (tmp_path / "later.md").write_text("Penguins waddle.", encoding="utf-8")

    assert client.get("/health").json()["chunks"] == 2


def test_missing_folder_stops_startup(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCS_DIR", str(tmp_path / "missing"))

    with pytest.raises(DocumentError):
        create_app()


@pytest.mark.parametrize("value", [None, ""])
def test_default_docs_dir_is_data_at_project_root(monkeypatch, tmp_path, value):
    monkeypatch.chdir(tmp_path)
    env = {} if value is None else {"DOCS_DIR": value}

    assert docs_dir(env) == Path(__file__).resolve().parents[2] / "data"


def test_successful_ask_and_response_shape():
    response = TestClient(create_app()).post(
        "/ask", json={"question": "  What is lava?  "}
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "sources", "mode"}
    assert body["mode"] == "stub"
    assert body["answer"] == "[stub] Based on lava.md: # Lava\nLava is molten rock."
    [source] = body["sources"]
    assert set(source) == {"file", "text", "score"}
    assert source["file"] == "lava.md"
    assert source["text"] == "# Lava\nLava is molten rock."
    assert source["score"] > 0


def test_proxy_ask(monkeypatch):
    use_proxy(monkeypatch)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": "canned"}}]}
        )
    )

    response = TestClient(create_app(transport=transport)).post(
        "/ask", json={"question": "lava"}
    )

    assert response.json()["answer"] == "canned"
    assert response.json()["mode"] == "proxy"


def test_proxy_without_model_stops_startup(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_URL", "http://127.0.0.1:9/v1")

    with pytest.raises(Exception, match="LLM_PROXY_MODEL"):
        create_app()


@pytest.mark.parametrize(
    "body",
    [
        {"question": ""},
        {"question": "   \n\t "},
        {"question": "x" * 1001},
        {"question": 5},
        {"question": None},
        {},
    ],
)
def test_invalid_questions_are_422(body):
    assert TestClient(create_app()).post("/ask", json=body).status_code == 422


@pytest.mark.parametrize("question", ["x", "x" * 1000, "  " + "x" * 1000 + "  "])
def test_length_limits_apply_after_trimming(question):
    response = TestClient(create_app()).post("/ask", json={"question": question})

    assert response.status_code == 200


@pytest.mark.parametrize("proxy", [False, True])
def test_no_match_answers_without_calling_the_llm(monkeypatch, proxy):
    if proxy:
        use_proxy(monkeypatch)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    client = TestClient(create_app(transport=httpx.MockTransport(handler)))

    response = client.post("/ask", json={"question": "zebra"})

    assert response.status_code == 200
    assert response.json() == {
        "answer": FALLBACK,
        "sources": [],
        "mode": "proxy" if proxy else "stub",
    }
    assert calls == []


def test_llm_error_is_502_without_leaking(monkeypatch, caplog):
    use_proxy(monkeypatch)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, text=f"upstream secret {KEY}")
    )
    caplog.set_level("DEBUG")

    response = TestClient(create_app(transport=transport)).post(
        "/ask", json={"question": "lava"}
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert KEY not in response.text
    assert "upstream secret" not in response.text
    assert KEY not in caplog.text
    assert "upstream secret" not in caplog.text
