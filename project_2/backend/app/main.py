"""FastAPI application. Run with: uvicorn app.main:create_app --factory"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, StringConstraints

from app.llm import LLMError, generate_answer, load_settings
from app.retrieval import Index, load_chunks

DEFAULT_DOCS_DIR = Path(__file__).resolve().parents[2] / "data"
NO_MATCH_ANSWER = "I couldn't find anything relevant in the documents."


class AskRequest(BaseModel):
    question: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
    ]


class Source(BaseModel):
    file: str
    text: str
    score: float


class AskResponse(BaseModel):
    answer: str
    sources: list[Source]
    mode: Literal["stub", "proxy"]


def docs_dir(env: Mapping[str, str]) -> Path:
    return Path(env.get("DOCS_DIR") or DEFAULT_DOCS_DIR)


def create_app(
    env: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    """Build the app from `env` (default: the process environment)."""
    env = os.environ if env is None else env
    settings = load_settings(env)
    index = Index(load_chunks(docs_dir(env)))
    app = FastAPI(title="Ask the docs")

    @app.get("/health")
    def health():
        return {"status": "ok", "chunks": len(index)}

    @app.post("/ask")
    def ask(request: AskRequest) -> AskResponse:
        results = index.search(request.question)
        if not results:
            return AskResponse(answer=NO_MATCH_ANSWER, sources=[], mode=settings.mode)
        try:
            answer = generate_answer(request.question, results, settings, transport)
        except LLMError:
            raise HTTPException(status_code=502, detail="LLM request failed") from None
        sources = [Source(file=r.file, text=r.text, score=r.score) for r in results]
        return AskResponse(answer=answer, sources=sources, mode=settings.mode)

    return app
