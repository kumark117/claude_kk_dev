"""FastAPI app factory. Run with: uvicorn app.main:create_app --factory"""

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, StringConstraints

from app.llm import LLMError, create_llm
from app.retrieval import Index, load_chunks

DEFAULT_DOCS_DIR = Path(__file__).resolve().parents[2] / "data"
NO_MATCH_ANSWER = "I couldn't find anything relevant in the documents."

logger = logging.getLogger(__name__)


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
    value = env.get("DOCS_DIR", "")
    return Path(value) if value else DEFAULT_DOCS_DIR


def create_app(
    env: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    """Build an app from env (default: the process environment).

    transport replaces the network for LLM requests in tests.
    """
    env = os.environ if env is None else env
    llm = create_llm(env, transport)
    index = Index(load_chunks(resolve_docs_dir(env)))
    app = FastAPI(title="Minimal RAG")

    @app.get("/health")
    def health() -> dict[str, str | int]:
        return {"status": "ok", "chunks": len(index.chunks)}

    # A plain "def" runs in a thread pool, so a slow LLM does not block the server.
    @app.post("/ask")
    def ask(request: AskRequest) -> AskResponse:
        hits = index.search(request.question)
        if not hits:
            return AskResponse(answer=NO_MATCH_ANSWER, sources=[], mode=llm.mode)
        try:
            answer = llm.answer(request.question, hits)
        except LLMError as error:
            logger.warning("%s", error)
            raise HTTPException(502, detail="LLM request failed") from None
        sources = [Source(file=h.file, text=h.text, score=h.score) for h in hits]
        return AskResponse(answer=answer, sources=sources, mode=llm.mode)

    return app
