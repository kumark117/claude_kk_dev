"""App factory, settings and endpoints.

Run with: uvicorn app.main:create_app --factory
"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, StringConstraints

from app.llm import LLMError, generate_answer, load_settings
from app.retrieval import BM25Index, load_chunks

# <project root>/data, found from this file and not from the working directory.
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


def create_app(
    env: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    """Build the app from ``env`` (default: the process environment).

    ``transport`` replaces the network for LLM requests in tests.
    """
    if env is None:
        env = os.environ
    llm_settings = load_settings(env)
    docs_dir = env.get("DOCS_DIR")
    index = BM25Index(load_chunks(Path(docs_dir) if docs_dir else DEFAULT_DOCS_DIR))

    app = FastAPI(title="Minimal RAG")

    @app.get("/health")
    def health():
        return {"status": "ok", "chunks": len(index.chunks)}

    @app.post("/ask")
    def ask(request: AskRequest) -> AskResponse:
        results = index.search(request.question)
        answer = NO_MATCH_ANSWER
        if results:
            try:
                answer = generate_answer(
                    request.question,
                    [result.chunk for result in results],
                    llm_settings,
                    transport,
                )
            except LLMError:
                raise HTTPException(502, "LLM request failed") from None
        sources = [
            Source(file=r.chunk.file, text=r.chunk.text, score=r.score)
            for r in results
        ]
        return AskResponse(answer=answer, sources=sources, mode=llm_settings.mode)

    return app
