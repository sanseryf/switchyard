"""HTTP surface: the GitHub webhook, DLQ inspection and replay.

Ingest is synchronous about acceptance (signature, dedupe, routing, persistence) and
asynchronous about delivery: the background task makes the first attempt, the worker
handles retries.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from . import __version__
from .config import Config
from .delivery import Dispatcher
from .routing import RoutingError, build_body, matching_routes
from .security import verify_signature
from .store import Store


def create_app(
    config: Config,
    *,
    store: Store | None = None,
    dispatcher: Dispatcher | None = None,
    secret: str | None = None,
) -> FastAPI:
    database = store or Store(config.database)
    dispatch = dispatcher or Dispatcher(config, database)
    webhook_secret = secret if secret is not None else os.environ.get(config.secret_env, "")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        database.close()

    app = FastAPI(title="Switchyard", version=__version__, lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post("/webhooks/github", status_code=202)
    async def github_webhook(request: Request, background: BackgroundTasks) -> JSONResponse:
        if not webhook_secret:
            raise HTTPException(
                status_code=503,
                detail=f"webhook secret is not configured (set {config.secret_env})",
            )
        body = await request.body()
        signature = request.headers.get("X-Hub-Signature-256")
        if not verify_signature(webhook_secret, body, signature):
            raise HTTPException(status_code=401, detail="invalid signature")
        delivery = request.headers.get("X-GitHub-Delivery")
        if not delivery:
            raise HTTPException(status_code=400, detail="missing X-GitHub-Delivery")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="payload is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="payload must be a JSON object")

        event = request.headers.get("X-GitHub-Event", "unknown")
        event_id, duplicate = database.add_event(delivery, event, payload)
        if duplicate:
            return JSONResponse(
                status_code=200,
                content={"duplicate": True, "event_id": event_id, "deliveries": 0},
            )

        routes = matching_routes(config, event, payload)
        for route in routes:
            try:
                body_text = build_body(route, event, delivery, payload)
            except RoutingError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            database.add_delivery(
                event_id,
                route.id,
                route.destination,
                body_text,
                payload,
                now=time.time(),
            )
        if routes:
            background.add_task(dispatch.deliver_event, event_id)
        return JSONResponse(
            status_code=202,
            content={"duplicate": False, "event_id": event_id, "deliveries": len(routes)},
        )

    @app.get("/events/{event_id}")
    def show_event(event_id: int) -> dict[str, Any]:
        event = database.event(event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="event not found")
        event["payload"] = json.loads(event["payload"])
        return event

    @app.get("/dlq")
    def list_dead_letters(limit: int = 50) -> dict[str, Any]:
        return {"dead_letters": database.dead_letters(limit)}

    @app.get("/dlq/{dead_id}")
    def show_dead_letter(dead_id: int) -> dict[str, Any]:
        dead = database.dead_letter(dead_id)
        if dead is None:
            raise HTTPException(status_code=404, detail="dead letter not found")
        return dead

    @app.post("/dlq/{dead_id}/replay", status_code=202)
    def replay(dead_id: int, background: BackgroundTasks) -> dict[str, Any]:
        dead = database.dead_letter(dead_id)
        if dead is None:
            raise HTTPException(status_code=404, detail="dead letter not found")
        new_delivery = database.replay(dead_id, now=time.time())
        background.add_task(dispatch.deliver_event, dead["event_id"])
        return {"replayed": True, "event_id": dead["event_id"], "delivery_id": new_delivery}

    return app
