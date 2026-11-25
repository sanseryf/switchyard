"""Delivery: at-least-once with capped exponential backoff, and a DLQ that explains why.

A delivery fails in two very different ways, and the dead letter records which:

* **validation** — Assay rejected the payload. Retrying cannot help, so it is dead on
  the first attempt and the grouped report is attached. This is the M8 contract.
* **delivery / assay-error** — the destination or Assay was unreachable or returned an
  error. The delivery is retried, then dead-lettered with the last error.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable
from typing import Any

import httpx

from .config import Config, Route
from .routing import RoutingError, build_body
from .store import Store
from .validator import AssayError, AssayValidator

MAX_BACKOFF_SECONDS = 300.0

Counts = dict[str, int]


class Dispatcher:
    def __init__(
        self,
        config: Config,
        store: Store,
        *,
        validator: AssayValidator | None = None,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.store = store
        self.validator = validator or AssayValidator(transport=transport)
        self._client = httpx.Client(transport=transport)
        self._clock = clock

    # ------------------------------------------------------------------ public

    def deliver_pending(self, limit: int = 50) -> Counts:
        counts: Counter[str] = Counter()
        for delivery in self.store.due_deliveries(self._clock(), limit):
            counts[self._deliver(delivery)] += 1
        return dict(counts)

    def deliver_event(self, event_id: int) -> Counts:
        counts: Counter[str] = Counter()
        for delivery in self.store.pending_for_event(event_id):
            counts[self._deliver(delivery)] += 1
        return dict(counts)

    # ------------------------------------------------------------------ internals

    def _deliver(self, delivery: dict[str, Any]) -> str:
        route = self._route(delivery["route_id"])
        if route is None:
            self._dead(delivery, "route", "route is no longer configured")
            return "dead"

        payload = json.loads(delivery["payload"])
        if route.assay is not None:
            try:
                report = self.validator.validate(route.assay, payload)
            except AssayError as exc:
                return self._retry(delivery, route, str(exc), reason="assay-error")
            if self.validator.should_fail(route.assay, report):
                self._dead(
                    delivery,
                    "validation",
                    "assay rejected the payload",
                    report=report,
                )
                return "dead"

        try:
            response = self._client.post(
                route.destination,
                content=delivery["body"],
                headers={"content-type": "application/json"},
                timeout=route.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            return self._retry(
                delivery, route, f"destination unreachable: {exc}", reason="delivery"
            )

        if 200 <= response.status_code < 300:
            self.store.mark_delivered(delivery["id"])
            return "delivered"
        return self._retry(
            delivery, route, f"destination returned HTTP {response.status_code}", reason="delivery"
        )

    def _retry(
        self,
        delivery: dict[str, Any],
        route: Route,
        error: str,
        *,
        reason: str,
    ) -> str:
        attempts = int(delivery["attempts"]) + 1
        if attempts > route.retries:
            self._dead(delivery, reason, error)
            return "dead"
        delay = min(MAX_BACKOFF_SECONDS, self.config.backoff_seconds * 2 ** (attempts - 1))
        self.store.reschedule(
            delivery["id"],
            attempts=attempts,
            next_attempt_at=self._clock() + delay,
            error=error,
        )
        return "retried"

    def _dead(
        self,
        delivery: dict[str, Any],
        reason: str,
        error: str,
        *,
        report: dict[str, Any] | None = None,
    ) -> None:
        self.store.mark_dead(delivery, reason=reason, error=error, report=report, now=self._clock())

    def _route(self, route_id: str) -> Route | None:
        return next((route for route in self.config.routes if route.id == route_id), None)


def delivery_body(route: Route, event: str, delivery: str, payload: dict[str, Any]) -> str:
    """Exposed for the ingest API and tests."""
    try:
        return build_body(route, event, delivery, payload)
    except RoutingError as exc:
        raise ValueError(str(exc)) from exc
