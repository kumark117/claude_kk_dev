"""FastAPI application: BM25 retrieval followed by an LLM answer."""

import os
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, StringConstraints

from app.llm import LLMError, Mode, generate_answer, load_settings
from app.retrieval import Index, load_chunks

# backend/app/main.py -> project root / data
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
    mode: Mode


def create_app(transport: httpx.BaseTransport | None = None) -> FastAPI:
    """Build the app from the environment. transport replaces the LLM network."""
    settings = load_settings(os.environ)
    docs_dir = os.environ.get("DOCS_DIR")
    index = Index(load_chunks(Path(docs_dir) if docs_dir else DEFAULT_DOCS_DIR))
    app = FastAPI(title="Ask the docs")

    @app.get("/health")
    def health() -> dict[str, str | int]:
        return {"status": "ok", "chunks": len(index.chunks)}

    @app.post("/ask")
    def ask(request: AskRequest) -> AskResponse:
        results = index.search(request.question)
        if not results:
            return AskResponse(answer=NO_MATCH_ANSWER, sources=[], mode=settings.mode)
        try:
            answer = generate_answer(
                request.question, [r.chunk for r in results], settings, transport
            )
        except LLMError:
            raise HTTPException(502, "LLM request failed") from None
        sources = [
            Source(file=r.chunk.file, text=r.chunk.text, score=r.score) for r in results
        ]
        return AskResponse(answer=answer, sources=sources, mode=settings.mode)

    return app
