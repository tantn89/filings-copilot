"""FastAPI service: JSON answers on POST /ask, live agent events over SSE on POST /ask/stream."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from filings_copilot.app_factory import Runtime, build_runtime
from filings_copilot.config import Settings, get_settings
from filings_copilot.usage_limiter import UsageLimiter, UsageLimitExceeded


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


def _client_id(request: Request) -> str:
    # Behind Cloud Run / Vercel / Render the caller's IP is the first X-Forwarded-For entry.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def create_app(runtime: Runtime | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    runtime = runtime or build_runtime(settings)
    limiter = UsageLimiter(settings.demo_questions_per_hour, settings.demo_daily_question_cap)

    app = FastAPI(title="filings-copilot", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    def guard(request: Request) -> None:
        try:
            limiter.check_and_record(_client_id(request))
        except UsageLimitExceeded as e:
            raise HTTPException(status_code=429, detail=str(e)) from None

    @app.get("/health")
    def health() -> dict[str, object]:
        row = runtime.store.cursor().execute(
            "SELECT (SELECT count(*) FROM filings), (SELECT count(*) FROM chunks)"
        ).fetchone()
        filings, chunks = row if row else (0, 0)
        return {
            "status": "ok", "filings": filings, "chunks": chunks, "model": runtime.agent.model,
            # Published so anyone can check the demo's spend protection is active.
            "limits": {
                "questions_per_hour_per_client": settings.demo_questions_per_hour,
                "questions_per_day": settings.demo_daily_question_cap,
            },
        }

    @app.get("/companies")
    def companies() -> object:
        return json.loads(runtime.agent.tools.run("list_companies", {}).content)

    # Sync handlers: FastAPI runs them in a threadpool, so the blocking SDK call is fine.
    @app.post("/ask")
    def ask(body: AskRequest, request: Request) -> dict[str, object]:
        guard(request)
        result = runtime.agent.ask(body.question)
        payload = asdict(result)
        payload.pop("events")
        return payload

    @app.post("/ask/stream")
    def ask_stream(body: AskRequest, request: Request) -> EventSourceResponse:
        guard(request)

        def events() -> Iterator[dict[str, str]]:
            try:
                for event in runtime.agent.stream(body.question):
                    yield {"event": event.type, "data": json.dumps(event.data, default=str)}
            except Exception as e:  # surface provider failures to the UI instead of a dead stream
                yield {"event": "error", "data": json.dumps({"message": f"Agent failed: {e}"})}

        return EventSourceResponse(events())

    return app
