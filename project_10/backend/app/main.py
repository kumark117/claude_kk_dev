"""FastAPI app factory, settings and endpoints."""

import os
from collections.abc import Mapping
from dataclasses import asdict
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


def create_app(
    env: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    """Build the app. Documents are read and indexed here, once.

    env defaults to the process environment; transport is for tests.
    """
    env = os.environ if env is None else env
    llm = create_llm(env, transport=transport)
    docs_dir = env.get("DOCS_DIR", "")
    index = Index(load_chunks(Path(docs_dir) if docs_dir else DEFAULT_DOCS_DIR))
    app = FastAPI(title="Minimal RAG")

    @app.get("/health")
    def health() -> dict[str, str | int]:
        return {"status": "ok", "chunks": len(index.chunks)}

    @app.post("/ask")
    def ask(request: AskRequest) -> AskResponse:
        hits = index.search(request.question)
        if not hits:
            return AskResponse(answer=NO_MATCH_ANSWER, sources=[], mode=llm.mode)
        try:
            answer = llm.answer(request.question, hits)
        except LLMError:
            raise HTTPException(status_code=502, detail="LLM request failed") from None
        return AskResponse(
            answer=answer,
            sources=[Source(**asdict(hit)) for hit in hits],
            mode=llm.mode,
        )

    return app
