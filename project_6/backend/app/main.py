"""FastAPI app factory. Run with: uvicorn app.main:create_app --factory"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints

from app.llm import LLMError, create_llm
from app.retrieval import Index, load_chunks

NO_MATCH_ANSWER = "I couldn't find anything relevant in the documents."
DEFAULT_DOCS_DIR = Path(__file__).resolve().parents[2] / "data"


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


def docs_dir(env: Mapping[str, str]) -> Path:
    configured = env.get("DOCS_DIR", "")
    return Path(configured) if configured else DEFAULT_DOCS_DIR


def create_app(
    env: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    """Build the app. Documents are read and indexed here, once."""
    env = os.environ if env is None else env
    index = Index(load_chunks(docs_dir(env)))
    llm = create_llm(env, transport)
    app = FastAPI(title="Minimal RAG")

    @app.exception_handler(RequestValidationError)
    def invalid_request(request: Request, error: RequestValidationError):
        # The default reply echoes the input, which fails with a 500 when the
        # input holds an unpaired surrogate.
        detail = [{"type": e["type"], "msg": e["msg"]} for e in error.errors()]
        return JSONResponse({"detail": detail}, status_code=422)

    @app.get("/health")
    def health() -> dict[str, str | int]:
        return {"status": "ok", "chunks": len(index.chunks)}

    @app.post("/ask")
    def ask(request: AskRequest) -> AskResponse:
        matches = index.search(request.question)
        if not matches:
            return AskResponse(answer=NO_MATCH_ANSWER, sources=[], mode=llm.mode)
        try:
            answer = llm.answer(request.question, matches)
        except LLMError:
            raise HTTPException(status_code=502, detail="LLM request failed") from None
        sources = [Source(file=m.file, text=m.text, score=m.score) for m in matches]
        return AskResponse(answer=answer, sources=sources, mode=llm.mode)

    return app
