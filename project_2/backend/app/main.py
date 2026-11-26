"""The FastAPI app. Run with: uvicorn --factory app.main:create_app"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, StringConstraints

from app.llm import LLMError, Mode, build_llm
from app.retrieval import Index, load_chunks

DEFAULT_DOCS_DIR = Path(__file__).resolve().parents[2] / "data"
NO_MATCH_ANSWER = "I couldn't find anything relevant in the documents."


class Question(BaseModel):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
    ]


class Source(BaseModel):
    file: str
    text: str
    score: float


class Answer(BaseModel):
    answer: str
    sources: list[Source]
    mode: Mode


def docs_dir(env: Mapping[str, str]) -> Path:
    configured = env.get("DOCS_DIR", "")
    return Path(configured) if configured else DEFAULT_DOCS_DIR


def create_app(transport: httpx.BaseTransport | None = None) -> FastAPI:
    """Build the app from the environment. `transport` replaces the LLM network."""
    llm = build_llm(os.environ, transport)
    index = Index(load_chunks(docs_dir(os.environ)))
    app = FastAPI(title="Ask the docs")

    @app.get("/health")
    def health() -> dict[str, str | int]:
        return {"status": "ok", "chunks": index.size}

    @app.post("/ask")
    def ask(request: Question) -> Answer:
        hits = index.search(request.question)
        if not hits:
            return Answer(answer=NO_MATCH_ANSWER, sources=[], mode=llm.mode)
        try:
            text = llm.answer(request.question, hits)
        except LLMError:
            raise HTTPException(502, "LLM request failed") from None
        sources = [Source(file=h.file, text=h.text, score=h.score) for h in hits]
        return Answer(answer=text, sources=sources, mode=llm.mode)

    return app
