"""FastAPI application factory, settings and endpoints."""

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, StringConstraints

from app.llm import LLMConfig, LLMError, config_from_env, generate_answer
from app.retrieval import Index, load_chunks

DEFAULT_DOCS_DIR = Path(__file__).resolve().parents[2] / "data"
NO_MATCH_ANSWER = "I couldn't find anything relevant in the documents."

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    docs_dir: Path
    llm: LLMConfig


def settings_from_env(env: Mapping[str, str]) -> Settings:
    docs_dir = env.get("DOCS_DIR", "")
    return Settings(
        docs_dir=Path(docs_dir) if docs_dir else DEFAULT_DOCS_DIR,
        llm=config_from_env(env),
    )


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


def create_app(transport: httpx.BaseTransport | None = None) -> FastAPI:
    """Build the app from the environment. Tests may inject the LLM transport."""
    settings = settings_from_env(os.environ)
    index = Index(load_chunks(settings.docs_dir))
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health() -> dict[str, str | int]:
        return {"status": "ok", "chunks": len(index.chunks)}

    @app.post("/ask")
    def ask(request: AskRequest) -> AskResponse:
        matches = index.search(request.question)
        if matches:
            try:
                answer = generate_answer(
                    request.question, matches, settings.llm, transport
                )
            except LLMError as error:
                # LLMError messages never contain the key or the upstream body.
                logger.warning("%s", error)
                raise HTTPException(502, "LLM request failed") from None
        else:
            answer = NO_MATCH_ANSWER
        sources = [Source(file=m.file, text=m.text, score=m.score) for m in matches]
        return AskResponse(answer=answer, sources=sources, mode=settings.llm.mode)

    return app
