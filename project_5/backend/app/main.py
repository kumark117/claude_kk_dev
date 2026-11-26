"""FastAPI application. Run with: uvicorn app.main:create_app --factory"""

import os
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
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


def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
    """A 422 that does not echo the input, which may be impossible to encode."""
    fields = ("type", "loc", "msg")
    detail = [{key: item[key] for key in fields} for item in error.errors()]
    return JSONResponse({"detail": detail}, status_code=422)


def create_app(transport: httpx.BaseTransport | None = None) -> FastAPI:
    """Build the app from the environment. `transport` is for tests."""
    config = load_config(os.environ)
    chunks = load_chunks(Path(os.environ.get("DOCS_DIR") or DEFAULT_DOCS_DIR))
    index = Index(chunks)
    app = FastAPI(title="Minimal RAG")
    app.add_exception_handler(RequestValidationError, invalid_request)

    @app.get("/health")
    def health():
        return {"status": "ok", "chunks": len(chunks)}

    @app.post("/ask")
    def ask(request: AskRequest) -> AskResponse:
        results = index.search(request.question)
        if not results:
            return AskResponse(answer=NO_MATCH_ANSWER, sources=[], mode=config.mode)
        try:
            answer = generate_answer(
                request.question,
                [result.chunk for result in results],
                config,
                transport=transport,
            )
        except LLMError:
            raise HTTPException(502, "LLM request failed") from None
        sources = [
            Source(file=r.chunk.file, text=r.chunk.text, score=r.score)
            for r in results
        ]
        return AskResponse(answer=answer, sources=sources, mode=config.mode)

    return app
