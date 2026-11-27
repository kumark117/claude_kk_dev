"""The FastAPI application factory, its settings and endpoints."""

import os
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, StringConstraints

from app.llm import LLMError, generate_answer, load_config
from app.retrieval import Index, load_chunks

DEFAULT_DOCS_DIR = Path(__file__).resolve().parents[2] / "data"
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


def docs_dir() -> Path:
    configured = os.environ.get("DOCS_DIR")
    return Path(configured) if configured else DEFAULT_DOCS_DIR


def create_app(llm_transport: httpx.BaseTransport | None = None) -> FastAPI:
    """Build an app from the environment. Documents are indexed here, once."""
    config = load_config(os.environ)
    index = Index(load_chunks(docs_dir()))
    app = FastAPI(title="Minimal RAG")

    @app.get("/health")
    def health() -> dict[str, str | int]:
        return {"status": "ok", "chunks": len(index.chunks)}

    # A plain "def" runs in a worker thread, so the blocking LLM call is safe.
    @app.post("/ask")
    def ask(request: AskRequest) -> AskResponse:
        matches = index.search(request.question)
        if not matches:
            return AskResponse(answer=NO_MATCH_ANSWER, sources=[], mode=config.mode)
        try:
            answer = generate_answer(
                request.question, matches, config, transport=llm_transport
            )
        except LLMError:
            raise HTTPException(status_code=502, detail="LLM request failed") from None
        sources = [Source(file=m.file, text=m.text, score=m.score) for m in matches]
        return AskResponse(answer=answer, sources=sources, mode=config.mode)

    return app
