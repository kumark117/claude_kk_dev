"""FastAPI app factory, settings and endpoints."""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
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


def create_app(
    env: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    """Build the app. Settings and documents are read once, here.

    `env` defaults to the process environment. `transport` replaces the network
    for LLM calls in tests.
    """
    env = os.environ if env is None else env
    config = load_config(env)
    index = Index(load_chunks(Path(env.get("DOCS_DIR") or DEFAULT_DOCS_DIR)))

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health():
        return {"status": "ok", "chunks": len(index)}

    @app.post("/ask", response_model=AskResponse)
    def ask(request: AskRequest):
        results = index.search(request.question)
        if not results:
            return AskResponse(answer=NO_MATCH_ANSWER, sources=[], mode=config.mode)
        try:
            answer = generate_answer(
                request.question, results, config, transport=transport
            )
        except LLMError:
            return JSONResponse({"detail": "LLM request failed"}, status_code=502)
        sources = [Source(file=r.file, text=r.text, score=r.score) for r in results]
        return AskResponse(answer=answer, sources=sources, mode=config.mode)

    return app
