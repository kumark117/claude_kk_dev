"""FastAPI app. Run with: uvicorn app.main:create_app --factory"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, StringConstraints

from app.llm import LLMError, generate_answer, load_settings
from app.retrieval import Index, load_chunks

PROJECT_ROOT = Path(__file__).resolve().parents[2]
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


def docs_dir(env: Mapping[str, str]) -> Path:
    return Path(env.get("DOCS_DIR") or PROJECT_ROOT / "data")


def create_app(
    env: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    """Build the app. Settings and documents are read once, here."""
    env = os.environ if env is None else env
    settings = load_settings(env)
    index = Index(load_chunks(docs_dir(env)))
    app = FastAPI(title="Minimal RAG")

    @app.get("/health")
    def health():
        return {"status": "ok", "chunks": len(index.chunks)}

    @app.post("/ask")
    def ask(request: AskRequest) -> AskResponse:
        hits = index.search(request.question)
        if not hits:
            return AskResponse(answer=NO_MATCH_ANSWER, sources=[], mode=settings.mode)
        try:
            answer = generate_answer(settings, request.question, hits, transport)
        except LLMError:
            raise HTTPException(502, "LLM request failed") from None
        return AskResponse(
            answer=answer,
            sources=[Source(file=h.file, text=h.text, score=h.score) for h in hits],
            mode=settings.mode,
        )

    return app
