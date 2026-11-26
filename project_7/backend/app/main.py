"""App factory, settings and endpoints."""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, StringConstraints

from app.llm import LLMError, build_llm
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


class Health(BaseModel):
    status: Literal["ok"]
    chunks: int


def create_app(
    env: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    """Build the app from the environment, reading and indexing the documents once."""
    env = os.environ if env is None else env
    llm = build_llm(env, transport=transport)
    docs_dir = env.get("DOCS_DIR", "")
    index = Index(load_chunks(Path(docs_dir) if docs_dir else DEFAULT_DOCS_DIR))
    app = FastAPI(title="Minimal RAG")

    @app.get("/health")
    def health() -> Health:
        return Health(status="ok", chunks=len(index.chunks))

    @app.post("/ask")
    def ask(request: AskRequest) -> AskResponse:
        results = index.search(request.question)
        if not results:
            return AskResponse(answer=NO_MATCH_ANSWER, sources=[], mode=llm.mode)
        try:
            answer = llm.answer(request.question, [r.chunk for r in results])
        except LLMError:
            raise HTTPException(status_code=502, detail="LLM request failed") from None
        sources = [Source(file=r.chunk.file, text=r.chunk.text, score=r.score) for r in results]
        return AskResponse(answer=answer, sources=sources, mode=llm.mode)

    return app
