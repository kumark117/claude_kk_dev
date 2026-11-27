"""FastAPI app factory, settings and endpoints."""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, StringConstraints

from app.llm import LLMError, generate_answer, load_llm_settings
from app.retrieval import BM25Index, load_chunks

NO_MATCH_ANSWER = "I couldn't find anything relevant in the documents."


class AskRequest(BaseModel):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
    ]


class Source(BaseModel):
    file: str
    text: str
    score: float


class AskResponse(BaseModel):
    answer: str
    sources: list[Source]
    mode: Literal["stub", "proxy"]


def default_docs_dir() -> Path:
    """data/ at the project root, wherever the server is started from."""
    return Path(__file__).resolve().parents[2] / "data"


def create_app(
    env: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    """Build the app from env (default: the process environment).

    Documents are read and indexed here, once. transport replaces the proxy's
    network layer in tests.
    """
    env = os.environ if env is None else env
    llm = load_llm_settings(env)
    docs_dir = Path(env["DOCS_DIR"]) if env.get("DOCS_DIR") else default_docs_dir()
    index = BM25Index(load_chunks(docs_dir))
    app = FastAPI(title="Minimal RAG")

    @app.get("/health")
    def health() -> dict[str, str | int]:
        return {"status": "ok", "chunks": len(index)}

    @app.post("/ask")
    def ask(request: AskRequest) -> AskResponse:
        results = index.search(request.question)
        if not results:
            return AskResponse(answer=NO_MATCH_ANSWER, sources=[], mode=llm.mode)
        try:
            answer = generate_answer(
                request.question, [r.chunk for r in results], llm, transport
            )
        except LLMError:
            raise HTTPException(status_code=502, detail="LLM request failed") from None
        sources = [
            Source(file=r.chunk.file, text=r.chunk.text, score=r.score) for r in results
        ]
        return AskResponse(answer=answer, sources=sources, mode=llm.mode)

    return app
