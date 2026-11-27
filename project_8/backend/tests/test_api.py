from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app, docs_dir
from app.retrieval import DocumentError

NO_MATCH = "I couldn't find anything relevant in the documents."
SECRET = "dummy-secret-key"
ENV_NAMES = ["DOCS_DIR", "LLM_PROXY_URL", "LLM_PROXY_API_KEY", "LLM_PROXY_MODEL"]


@pytest.fixture(autouse=True)
def environment(tmp_path, monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "moon.md").write_text(
        "# Moon\n\nThe moon is grey.\n\nCraters cover the moon.", encoding="utf-8"
    )
    (tmp_path / "sun.txt").write_text("The sun is hot.", encoding="utf-8")
    monkeypatch.setenv("DOCS_DIR", str(tmp_path))


def use_proxy(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_URL", "http://llm.test/v1")
    monkeypatch.setenv("LLM_PROXY_MODEL", "test-model")
    monkeypatch.setenv("LLM_PROXY_API_KEY", SECRET)


def client_with(handler=None):
    transport = httpx.MockTransport(handler) if handler else None
    return TestClient(create_app(transport=transport))


def test_health_reports_chunk_count():
    response = client_with().get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "chunks": 3}


def test_empty_documents_folder_starts(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("DOCS_DIR", str(empty))
    client = client_with()

    assert client.get("/health").json() == {"status": "ok", "chunks": 0}
    assert client.post("/ask", json={"question": "moon"}).json()["sources"] == []


def test_missing_documents_folder_fails_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_DIR", str(tmp_path / "nowhere"))

    with pytest.raises(DocumentError):
        create_app()


@pytest.mark.parametrize("value", [None, ""])
def test_default_documents_folder_ignores_the_working_directory(
    value, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    env = {} if value is None else {"DOCS_DIR": value}

    assert docs_dir(env) == Path(__file__).resolve().parents[2] / "data"


def test_documents_are_read_only_at_startup(tmp_path):
    client = client_with()
    (tmp_path / "late.txt").write_text("zebra", encoding="utf-8")

    assert client.post("/ask", json={"question": "zebra"}).json()["sources"] == []


def test_successful_ask_returns_answer_sources_and_mode():
    response = client_with().post("/ask", json={"question": "Is the sun hot?"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "sources", "mode"}
    assert body["answer"] == "[stub] Based on sun.txt: The sun is hot."
    assert body["mode"] == "stub"
    [source] = body["sources"]
    assert set(source) == {"file", "text", "score"}
    assert source["file"] == "sun.txt"
    assert source["text"] == "The sun is hot."
    assert isinstance(source["score"], float) and source["score"] > 0


def test_question_is_trimmed_before_use(monkeypatch):
    use_proxy(monkeypatch)
    seen = []

    def handler(request):
        seen.append(request.content.decode())
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    response = client_with(handler).post("/ask", json={"question": "  \n sun \t "})

    assert response.json()["answer"] == "ok"
    assert response.json()["mode"] == "proxy"
    assert "sun \\t" not in seen[0]


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
    ids=["empty", "blank", "too long", "number", "null", "missing"],
)
def test_invalid_questions_are_rejected(payload):
    response = client_with().post("/ask", json=payload)

    assert response.status_code == 422


@pytest.mark.parametrize(
    "question",
    ["x", "x" * 1000, "   " + "x" * 1000 + "   "],
    ids=["1 char", "1000 chars", "1000 chars padded"],
)
def test_boundary_lengths_are_accepted(question):
    response = client_with().post("/ask", json={"question": question})

    assert response.status_code == 200


@pytest.mark.parametrize("proxy_mode", [False, True], ids=["stub", "proxy"])
def test_no_match_gives_fallback_without_calling_the_llm(proxy_mode, monkeypatch):
    if proxy_mode:
        use_proxy(monkeypatch)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    response = client_with(handler).post("/ask", json={"question": "zebra"})

    assert response.status_code == 200
    assert response.json() == {
        "answer": NO_MATCH,
        "sources": [],
        "mode": "proxy" if proxy_mode else "stub",
    }
    assert calls == []


def test_llm_error_returns_502_without_leaking(monkeypatch, caplog):
    use_proxy(monkeypatch)
    client = client_with(
        lambda request: httpx.Response(500, text=f"upstream-body {SECRET}")
    )

    with caplog.at_level("DEBUG"):
        response = client.post("/ask", json={"question": "sun"})

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    for text in (response.text, str(response.headers), caplog.text):
        assert SECRET not in text
        assert "upstream-body" not in text
