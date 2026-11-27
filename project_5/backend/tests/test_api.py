from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app, docs_dir
from app.retrieval import DocsError

FALLBACK = "I couldn't find anything relevant in the documents."
KEY = "dummy-test-key"


@pytest.fixture(autouse=True)
def environment(monkeypatch, tmp_path):
    """Isolate every test from the caller's environment and from data/."""
    for name in (
        "LLM_PROXY_URL",
        "LLM_PROXY_API_KEY",
        "LLM_PROXY_MODEL",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "fruit.md").write_text(
        "# Fruit\n\nBananas ripen quickly in warm rooms.", encoding="utf-8"
    )
    (tmp_path / "space.txt").write_text(
        "Jupiter is the largest planet.\n\nMars looks red.", encoding="utf-8"
    )
    monkeypatch.setenv("DOCS_DIR", str(tmp_path))


def use_proxy(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_URL", "http://llm.test/v1")
    monkeypatch.setenv("LLM_PROXY_MODEL", "test-model")
    monkeypatch.setenv("LLM_PROXY_API_KEY", KEY)


def ask(client, question):
    return client.post("/ask", json={"question": question})


def test_health_reports_the_chunk_count():
    response = TestClient(create_app()).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "chunks": 3}


def test_empty_folder_starts_with_no_chunks(monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("DOCS_DIR", str(empty))
    client = TestClient(create_app())

    assert client.get("/health").json() == {"status": "ok", "chunks": 0}
    assert ask(client, "Jupiter").json()["sources"] == []


def test_documents_are_read_once_at_startup(tmp_path):
    client = TestClient(create_app())
    (tmp_path / "late.md").write_text("Zeppelins float.", encoding="utf-8")

    assert client.get("/health").json()["chunks"] == 3
    assert ask(client, "zeppelins").json()["sources"] == []


def test_missing_docs_folder_stops_startup(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCS_DIR", str(tmp_path / "missing"))

    with pytest.raises(DocsError, match="missing"):
        create_app()


def test_missing_model_stops_startup(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_URL", "http://llm.test/v1")

    with pytest.raises(ValueError, match="LLM_PROXY_MODEL"):
        create_app()


@pytest.mark.parametrize("value", [None, ""])
def test_default_docs_folder_is_data_at_the_project_root(monkeypatch, tmp_path, value):
    if value is None:
        monkeypatch.delenv("DOCS_DIR")
    else:
        monkeypatch.setenv("DOCS_DIR", value)
    monkeypatch.chdir(tmp_path)

    assert docs_dir() == Path(__file__).resolve().parents[2] / "data"


def test_successful_ask_returns_answer_sources_and_mode():
    response = ask(TestClient(create_app()), "Which planet is the largest?")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "sources", "mode"}
    assert body["mode"] == "stub"
    assert body["answer"] == "[stub] Based on space.txt: Jupiter is the largest planet."
    assert len(body["sources"]) == 1
    source = body["sources"][0]
    assert set(source) == {"file", "text", "score"}
    assert source["file"] == "space.txt"
    assert source["text"] == "Jupiter is the largest planet."
    assert isinstance(source["score"], float) and source["score"] > 0


@pytest.mark.parametrize(
    "payload",
    [
        {"question": ""},
        {"question": "   \n\t "},
        {"question": "x" * 1001},
        {"question": 7},
        {"question": None},
        {},
    ],
)
def test_invalid_question_is_rejected(payload):
    assert TestClient(create_app()).post("/ask", json=payload).status_code == 422


@pytest.mark.parametrize("question", ["x", "x" * 1000, "  " + "x" * 1000 + "  "])
def test_question_length_is_checked_after_trimming(question):
    assert ask(TestClient(create_app()), question).status_code == 200


def test_question_is_trimmed_before_use(monkeypatch):
    use_proxy(monkeypatch)
    seen = []

    def handler(request):
        seen.append(request.content.decode())
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    ask(
        TestClient(create_app(llm_transport=httpx.MockTransport(handler))),
        "   Jupiter?   ",
    )

    assert "Jupiter?" in seen[0]
    assert "Jupiter?   " not in seen[0]
    assert "   Jupiter?" not in seen[0]


def test_proxy_mode_returns_the_proxy_answer(monkeypatch):
    use_proxy(monkeypatch)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": "From the proxy."}}]}
        )
    )

    body = ask(TestClient(create_app(llm_transport=transport)), "Jupiter").json()

    assert body["answer"] == "From the proxy."
    assert body["mode"] == "proxy"
    assert body["sources"][0]["file"] == "space.txt"


@pytest.mark.parametrize("mode", ["stub", "proxy"])
def test_no_match_returns_the_fallback_without_calling_the_llm(monkeypatch, mode):
    if mode == "proxy":
        use_proxy(monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError("the LLM must not be called")

    monkeypatch.setattr("app.main.generate_answer", forbidden)

    response = ask(TestClient(create_app()), "zeppelin")

    assert response.status_code == 200
    assert response.json() == {"answer": FALLBACK, "sources": [], "mode": mode}


def test_llm_error_returns_502_without_leaking_details(monkeypatch, caplog):
    caplog.set_level("DEBUG")
    use_proxy(monkeypatch)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, text="upstream-secret-body")
    )

    response = ask(TestClient(create_app(llm_transport=transport)), "Jupiter")

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    for text in (response.text, str(response.headers), caplog.text):
        assert KEY not in text
        assert "upstream-secret-body" not in text
