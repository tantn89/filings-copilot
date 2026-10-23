"""FastAPI service: JSON answers on POST /ask, live agent events over SSE on POST /ask/stream."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterator
from dataclasses import asdict

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from filings_copilot.app_factory import Runtime, build_runtime
from filings_copilot.config import Settings, get_settings
from filings_copilot.usage_limiter import UsageLimiter, UsageLimitExceeded

log = logging.getLogger("filings_copilot.api")


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


def _client_id(request: Request, trusted_hops: int) -> str:
    """The caller's IP as seen by the platform's own proxy.

    Proxies append the address they received a connection from, so entries on the left
    of X-Forwarded-For are client-supplied and spoofable. The entry `trusted_hops` from
    the right was written by the platform (1 = the last proxy, e.g. Google's front end on
    Cloud Run). 0 ignores the header and uses the socket peer.
    """
    parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if trusted_hops and len(parts) >= trusted_hops:
        return parts[-trusted_hops]
    return request.client.host if request.client else "unknown"


def _configure_logging() -> None:
    """uvicorn only configures its own loggers; without this, app INFO lines are dropped."""
    app_logger = logging.getLogger("filings_copilot")
    if not app_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        app_logger.addHandler(handler)
        app_logger.setLevel(logging.INFO)


def create_app(runtime: Runtime | None = None, settings: Settings | None = None) -> FastAPI:
    _configure_logging()
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
        client = _client_id(request, settings.trusted_proxy_hops)
        forwarded = request.headers.get("x-forwarded-for", "")
        log.info("question client=%s forwarded_entries=%d", client,
                 len([p for p in forwarded.split(",") if p.strip()]))
        try:
            limiter.check_and_record(client)
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
            except Exception:
                # Details (provider bodies, SQL) stay in server logs; the client gets a reference.
                ref = uuid.uuid4().hex[:8]
                log.exception("agent failed ref=%s", ref)
                message = f"The research agent hit an internal error (ref {ref}). Please retry."
                yield {"event": "error", "data": json.dumps({"message": message})}

        return EventSourceResponse(events())

    return app
