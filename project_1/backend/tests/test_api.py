import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.llm import ConfigError
from app.main import create_app
from app.retrieval import DocumentError

KEY = "test-dummy-key"
FALLBACK = "I couldn't find anything relevant in the documents."


@pytest.fixture
def docs(tmp_path):
    (tmp_path / "cats.md").write_text("# Cats\n\nCats purr.", encoding="utf-8")
    (tmp_path / "dogs.txt").write_text("Dogs bark.\n\nDogs dig.", encoding="utf-8")
    return tmp_path


def client_for(docs, transport=None, **env):
    return TestClient(create_app({"DOCS_DIR": str(docs), **env}, transport=transport))


def proxy_client(docs, handler):
    return client_for(
        docs,
        transport=httpx.MockTransport(handler),
        LLM_PROXY_URL="http://llm.test/v1",
        LLM_PROXY_MODEL="test-model",
        LLM_PROXY_API_KEY=KEY,
    )


def test_health_reports_the_chunk_count(docs):
    response = client_for(docs).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "chunks": 3}


def test_ask_returns_answer_sources_and_mode(docs):
    response = client_for(docs).post("/ask", json={"question": "Do cats purr?"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "sources", "mode"}
    assert body["mode"] == "stub"
    assert body["answer"] == "[stub] Based on cats.md: # Cats\nCats purr."
    (source,) = body["sources"]
    assert set(source) == {"file", "text", "score"}
    assert source["file"] == "cats.md"
    assert source["text"] == "# Cats\nCats purr."
    assert source["score"] > 0


def test_ask_uses_the_proxy_answer(docs):
    client = proxy_client(
        docs,
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": "From the proxy."}}]}
        ),
    )

    body = client.post("/ask", json={"question": "dogs"}).json()

    assert body["answer"] == "From the proxy."
    assert body["mode"] == "proxy"
    assert [source["file"] for source in body["sources"]] == ["dogs.txt"] * 2


@pytest.mark.parametrize(
    "payload",
    [
        {"question": ""},
        {"question": "  \n\t "},
        {"question": "x" * 1001},
        {"question": 42},
        {"question": None},
        {},
    ],
)
def test_ask_rejects_bad_questions(docs, payload):
    assert client_for(docs).post("/ask", json=payload).status_code == 422


@pytest.mark.parametrize("question", ["x", "x" * 1000, "  " + "x" * 1000 + "  "])
def test_ask_accepts_trimmed_length_within_limits(docs, question):
    response = client_for(docs).post("/ask", json={"question": question})

    assert response.status_code == 200


def test_ask_trims_the_question_before_using_it(docs):
    seen = []

    def handler(request):
        seen.append(request.content.decode())
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    proxy_client(docs, handler).post("/ask", json={"question": "  cats purr?  \n"})

    assert "cats purr?" in seen[0]
    assert "cats purr?  " not in seen[0]


@pytest.mark.parametrize("mode", ["stub", "proxy"])
def test_no_match_gives_the_fallback_without_calling_the_llm(docs, mode):
    def handler(request):
        raise AssertionError("the LLM must not be called")

    if mode == "proxy":
        client = proxy_client(docs, handler)
    else:
        client = client_for(docs)

    response = client.post("/ask", json={"question": "zebra"})

    assert response.status_code == 200
    assert response.json() == {"answer": FALLBACK, "sources": [], "mode": mode}


def test_no_match_does_not_call_the_answer_function(docs, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("the LLM must not be called")

    monkeypatch.setattr(main, "generate_answer", fail)

    response = client_for(docs).post("/ask", json={"question": "zebra"})

    assert response.json()["answer"] == FALLBACK


def test_llm_error_gives_502_without_leaking_details(docs, caplog):
    client = proxy_client(
        docs, lambda request: httpx.Response(500, text="upstream-secret " + KEY)
    )

    response = client.post("/ask", json={"question": "cats"})

    assert response.status_code == 502
    assert response.json() == {"detail": "LLM request failed"}
    assert KEY not in caplog.text
    assert "upstream-secret" not in caplog.text


def test_documents_are_read_once_at_startup(docs):
    client = client_for(docs)
    (docs / "birds.md").write_text("Birds sing.", encoding="utf-8")

    assert client.get("/health").json()["chunks"] == 3


def test_factory_reads_the_process_environment(docs, monkeypatch):
    monkeypatch.setenv("DOCS_DIR", str(docs))
    monkeypatch.delenv("LLM_PROXY_URL", raising=False)

    client = TestClient(create_app())

    assert client.get("/health").json()["chunks"] == 3


@pytest.mark.parametrize("env", [{}, {"DOCS_DIR": ""}])
def test_default_docs_folder_is_data_at_the_project_root(
    env, docs, monkeypatch, tmp_path
):
    # Stand in for data/ so that the test does not depend on its contents.
    root = main.Path(main.__file__).resolve().parents[2]
    assert main.DEFAULT_DOCS_DIR == root / "data"
    monkeypatch.setattr(main, "DEFAULT_DOCS_DIR", docs)
    monkeypatch.chdir(tmp_path)

    client = TestClient(create_app(env))

    assert client.get("/health").json()["chunks"] == 3


def test_missing_docs_folder_fails_startup(tmp_path):
    with pytest.raises(DocumentError):
        create_app({"DOCS_DIR": str(tmp_path / "nowhere")})


def test_missing_model_fails_startup(docs):
    with pytest.raises(ConfigError, match="LLM_PROXY_MODEL"):
        create_app({"DOCS_DIR": str(docs), "LLM_PROXY_URL": "http://llm.test"})
