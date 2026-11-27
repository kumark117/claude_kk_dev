"""FastAPI application. Run with: uvicorn app.main:create_app --factory"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, StringConstraints

from app.llm import LLMError, create_llm
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


def docs_dir(env: Mapping[str, str]) -> Path:
    configured = env.get("DOCS_DIR")
    return Path(configured) if configured else DEFAULT_DOCS_DIR


def create_app(transport: httpx.BaseTransport | None = None) -> FastAPI:
    """Build the app from the environment. `transport` replaces the LLM network."""
    llm = create_llm(os.environ, transport=transport)
    index = Index(load_chunks(docs_dir(os.environ)))
    app = FastAPI(title="Minimal RAG")

    @app.get("/health")
    def health():
        return {"status": "ok", "chunks": len(index.chunks)}

    @app.post("/ask")
    def ask(request: AskRequest) -> AskResponse:
        results = index.search(request.question)
        if not results:
            return AskResponse(answer=NO_MATCH_ANSWER, sources=[], mode=llm.mode)
        try:
            answer = llm.answer(request.question, results)
        except LLMError:
            raise HTTPException(502, "LLM request failed") from None
        sources = [Source(file=r.file, text=r.text, score=r.score) for r in results]
        return AskResponse(answer=answer, sources=sources, mode=llm.mode)

    return app
