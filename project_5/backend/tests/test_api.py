from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.retrieval import DocumentError

FALLBACK = "I couldn't find anything relevant in the documents."


@pytest.fixture(autouse=True)
def docs(tmp_path, monkeypatch):
    """A temporary documents folder and a clean environment."""
    (tmp_path / "bees.md").write_text(
        "# Bees\n\nBees make honey.\n\nA hive has one queen.", encoding="utf-8"
    )
    (tmp_path / "tides.txt").write_text("Tides follow the moon.", encoding="utf-8")
    for name in ("LLM_PROXY_URL", "LLM_PROXY_API_KEY", "LLM_PROXY_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DOCS_DIR", str(tmp_path))
    return tmp_path


def use_proxy(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_URL", "http://llm.test/v1")
    monkeypatch.setenv("LLM_PROXY_API_KEY", "secret-key")
    monkeypatch.setenv("LLM_PROXY_MODEL", "m-1")


def client(transport=None):
    return TestClient(main.create_app(transport=transport))


def ask(test_client, question):
    return test_client.post("/ask", json={"question": question})


def test_health_reports_chunk_count():
    response = client().get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "chunks": 3}


def test_successful_ask_has_the_expected_shape():
    response = ask(client(), "Why do tides follow the moon?")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "sources", "mode"}
    assert body["mode"] == "stub"
    assert body["answer"] == "[stub] Based on tides.txt: Tides follow the moon."
    [source] = body["sources"]
    assert set(source) == {"file", "text", "score"}
    assert source["file"] == "tides.txt"
    assert source["text"] == "Tides follow the moon."
    assert isinstance(source["score"], float)
    assert source["score"] > 0


def test_documents_are_read_only_at_startup(docs):
    test_client = client()
    (docs / "tides.txt").unlink()

    assert ask(test_client, "tides").json()["sources"][0]["file"] == "tides.txt"


@pytest.mark.parametrize("question", ["", "   \n\t ", "x" * 1001])
def test_blank_and_over_long_questions_are_rejected(question):
    assert ask(client(), question).status_code == 422


@pytest.mark.parametrize(
    "body", [{}, {"question": None}, {"question": 5}, {"q": "tides"}, []]
)
def test_wrong_body_is_rejected(body):
    assert client().post("/ask", json=body).status_code == 422


@pytest.mark.parametrize("question", ["x", "x" * 1000, "  " + "x" * 1000 + "  "])
def test_length_limit_applies_after_trimming(question):
    assert ask(client(), question).status_code == 200


def test_question_is_trimmed_before_use(monkeypatch):
    seen = []

    def fake_answer(question, chunks, config, transport=None):
        seen.append(question)
        return "ok"

    monkeypatch.setattr(main, "generate_answer", fake_answer)

    ask(client(), "  tides \n")

    assert seen == ["tides"]


@pytest.mark.parametrize("mode", ["stub", "proxy"])
def test_no_match_gives_fallback_without_calling_the_llm(monkeypatch, mode):
    if mode == "proxy":
        use_proxy(monkeypatch)

    def fail(*args, **kwargs):
        raise AssertionError("the LLM must not be called")

    monkeypatch.setattr(main, "generate_answer", fail)

    response = ask(client(), "volcano eruption")

    assert response.status_code == 200
    assert response.json() == {"answer": FALLBACK, "sources": [], "mode": mode}


def test_proxy_answer_is_returned(monkeypatch):
    use_proxy(monkeypatch)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": "From proxy."}}]}
        )
    )

    body = ask(client(transport), "tides").json()

    assert body["answer"] == "From proxy."
    assert body["mode"] == "proxy"
    assert body["sources"][0]["file"] == "tides.txt"


def test_llm_error_gives_502_without_leaking(monkeypatch, caplog):
    use_proxy(monkeypatch)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, text="upstream-private")
    )
    caplog.set_level("DEBUG")

    response = ask(client(transport), "tides")

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    for text in (response.text, str(response.headers), caplog.text):
        assert "secret-key" not in text
        assert "upstream-private" not in text


def test_missing_model_fails_startup(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_URL", "http://llm.test/v1")

    with pytest.raises(ValueError, match="LLM_PROXY_MODEL"):
        main.create_app()


def test_missing_docs_folder_fails_startup(monkeypatch, docs):
    monkeypatch.setenv("DOCS_DIR", str(docs / "nowhere"))

    with pytest.raises(DocumentError):
        main.create_app()


@pytest.mark.parametrize("value", [None, ""])
def test_docs_folder_defaults_to_project_data(monkeypatch, tmp_path, value):
    if value is None:
        monkeypatch.delenv("DOCS_DIR")
    else:
        monkeypatch.setenv("DOCS_DIR", value)
    monkeypatch.chdir(tmp_path)
    loaded = []
    monkeypatch.setattr(
        main, "load_chunks", lambda folder: loaded.append(folder) or []
    )

    main.create_app()

    assert loaded == [Path(__file__).resolve().parents[2] / "data"]


def test_instances_are_independent(monkeypatch):
    stub = client()
    use_proxy(monkeypatch)
    proxy = client()

    assert ask(stub, "volcano").json()["mode"] == "stub"
    assert ask(proxy, "volcano").json()["mode"] == "proxy"


@pytest.mark.parametrize(
    "body", [rb'{"question": "\ud800 tides"}', rb'["\ud800"]']
)
def test_unencodable_text_is_rejected_not_a_server_error(body):
    response = client().post(
        "/ask", content=body, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["msg"]
