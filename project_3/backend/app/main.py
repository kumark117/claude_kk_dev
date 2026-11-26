"""FastAPI app factory, settings and endpoints."""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, StringConstraints

from app.llm import LLMError, create_llm
from app.retrieval import DEFAULT_DOCS_DIR, Index, load_chunks

NO_MATCH_ANSWER = "I couldn't find anything relevant in the documents."
MAX_QUESTION_CHARS = 1000


class AskRequest(BaseModel):
    question: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_CHARS),
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
    """Build the app; documents are read and indexed once, here."""
    env = os.environ if env is None else env
    llm = create_llm(env, transport)
    docs_dir = Path(env.get("DOCS_DIR") or DEFAULT_DOCS_DIR)
    index = Index(load_chunks(docs_dir))
    app = FastAPI(title="Ask the docs")

    @app.get("/health")
    def health():
        return {"status": "ok", "chunks": len(index.chunks)}

    @app.post("/ask")
    def ask(request: AskRequest) -> AskResponse:
        chunks = index.search(request.question)
        if not chunks:
            return AskResponse(answer=NO_MATCH_ANSWER, sources=[], mode=llm.mode)
        try:
            answer = llm.answer(request.question, chunks)
        except LLMError:
            raise HTTPException(status_code=502, detail="LLM request failed") from None
        sources = [Source(file=c.file, text=c.text, score=c.score) for c in chunks]
        return AskResponse(answer=answer, sources=sources, mode=llm.mode)

    return app
