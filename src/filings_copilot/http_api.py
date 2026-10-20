"""FastAPI service: JSON answers on POST /ask, live agent events over SSE on POST /ask/stream."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from filings_copilot.app_factory import Runtime, build_runtime
from filings_copilot.config import get_settings


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


def create_app(runtime: Runtime | None = None) -> FastAPI:
    runtime = runtime or build_runtime(get_settings())
    app = FastAPI(title="filings-copilot", version="0.1.0")
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )

    @app.get("/health")
    def health() -> dict[str, object]:
        row = runtime.store.cursor().execute(
            "SELECT (SELECT count(*) FROM filings), (SELECT count(*) FROM chunks)"
        ).fetchone()
        filings, chunks = row if row else (0, 0)
        return {"status": "ok", "filings": filings, "chunks": chunks}

    @app.get("/companies")
    def companies() -> object:
        return json.loads(runtime.agent.tools.run("list_companies", {}).content)

    # Sync handlers: FastAPI runs them in a threadpool, so the blocking SDK call is fine.
    @app.post("/ask")
    def ask(body: AskRequest) -> dict[str, object]:
        result = runtime.agent.ask(body.question)
        payload = asdict(result)
        payload.pop("events")
        return payload

    @app.post("/ask/stream")
    def ask_stream(body: AskRequest) -> EventSourceResponse:
        def events() -> Iterator[dict[str, str]]:
            for event in runtime.agent.stream(body.question):
                yield {"event": event.type, "data": json.dumps(event.data, default=str)}

        return EventSourceResponse(events())

    return app
