"""The FastAPI app. Run with: uvicorn --factory app.main:create_app"""

import os
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, StringConstraints

from app.llm import LLMError, LLMSettings, generate_answer
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


def resolve_docs_dir(env: Mapping[str, str]) -> Path:
    configured = env.get("DOCS_DIR", "")
    return Path(configured) if configured else DEFAULT_DOCS_DIR


def create_app(
    env: Mapping[str, str] | None = None,
    llm_transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    """Read settings, index the documents once and build the app."""
    env = os.environ if env is None else env
    settings = LLMSettings.from_env(env)
    index = Index(load_chunks(resolve_docs_dir(env)))
    app = FastAPI(title="Minimal RAG")

    @app.get("/health")
    def health() -> dict[str, str | int]:
        return {"status": "ok", "chunks": len(index.chunks)}

    @app.post("/ask")
    def ask(request: AskRequest) -> AskResponse:
        hits = index.search(request.question)
        if not hits:
            return AskResponse(answer=NO_MATCH_ANSWER, sources=[], mode=settings.mode)
        try:
            answer = generate_answer(request.question, hits, settings, llm_transport)
        except LLMError:
            raise HTTPException(status_code=502, detail="LLM request failed") from None
        return AskResponse(
            answer=answer,
            sources=[Source(**asdict(hit)) for hit in hits],
            mode=settings.mode,
        )

    return app
