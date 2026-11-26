"""FastAPI app factory, settings and endpoints."""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, StringConstraints

from app.llm import LLMError, config_from_env, generate_answer
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
    mode: str


def docs_dir_from_env(env: Mapping[str, str]) -> Path:
    return Path(env.get("DOCS_DIR") or DEFAULT_DOCS_DIR)


def create_app(transport: httpx.BaseTransport | None = None) -> FastAPI:
    """Build the app from the environment. `transport` replaces the proxy's HTTP layer."""
    config = config_from_env(os.environ)
    index = Index(load_chunks(docs_dir_from_env(os.environ)))
    app = FastAPI(title="Minimal RAG")

    @app.get("/health")
    def health():
        return {"status": "ok", "chunks": len(index.chunks)}

    @app.post("/ask")
    def ask(request: AskRequest) -> AskResponse:
        results = index.search(request.question)
        if not results:
            return AskResponse(answer=NO_MATCH_ANSWER, sources=[], mode=config.mode)
        try:
            answer = generate_answer(request.question, results, config, transport)
        except LLMError:
            raise HTTPException(502, "LLM request failed") from None
        sources = [Source(file=r.file, text=r.text, score=r.score) for r in results]
        return AskResponse(answer=answer, sources=sources, mode=config.mode)

    return app
