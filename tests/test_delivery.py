"""Delivery: success, retries with backoff, DLQ on exhaustion, and Assay rejection."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from conftest import make_config, push_payload, sink_response, transport
from switchyard.config import Route
from switchyard.delivery import Dispatcher
from switchyard.routing import build_body
from switchyard.store import Store


def queue(store: Store, config, *, delivery: str = "d-1") -> tuple[int, int]:
    payload = push_payload()
    event_id, _ = store.add_event(delivery, "push", payload)
    route = config.routes[0]
    body = build_body(route, "push", delivery, payload)
    delivery_id = store.add_delivery(
        event_id, route.id, route.destination, body, payload, now=1000.0
    )
    return event_id, delivery_id


def build(tmp_path: Path, handler, clock, config=None):
    cfg = config or make_config(tmp_path)
    store = Store(cfg.database)
    dispatcher = Dispatcher(cfg, store, transport=transport(handler), clock=clock)
    return cfg, store, dispatcher


def test_successful_delivery_posts_the_body(tmp_path: Path, clock) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return sink_response()

    cfg, store, dispatcher = build(tmp_path, handler, clock)
    queue(store, cfg)
    counts = dispatcher.deliver_pending()
    assert counts == {"delivered": 1}
    assert len(seen) == 1
    assert json.loads(seen[0].content)["event"] == "push"
    assert seen[0].headers["content-type"] == "application/json"


def test_a_failure_is_retried_after_backoff(tmp_path: Path, clock) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return sink_response() if calls["n"] >= 2 else httpx.Response(500)

    cfg, store, dispatcher = build(tmp_path, handler, clock)
    queue(store, cfg)
    assert dispatcher.deliver_pending() == {"retried": 1}
    assert store.due_deliveries(clock()) == []  # not due yet
    clock.advance(5.0)
    assert dispatcher.deliver_pending() == {"delivered": 1}
    assert calls["n"] == 2


def test_exhausted_retries_become_a_dead_letter(tmp_path: Path, clock) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    config = make_config(
        tmp_path,
        routes=[
            Route(id="push-sink", event="push", destination="http://sink.test/hook", retries=1)
        ],
    )
    cfg, store, dispatcher = build(tmp_path, handler, clock, config)
    queue(store, cfg)
    assert dispatcher.deliver_pending() == {"retried": 1}
    clock.advance(5.0)
    assert dispatcher.deliver_pending() == {"dead": 1}
    dead = store.dead_letters()[0]
    assert dead["reason"] == "delivery"
    assert dead["report"] is None
    assert dead["payload"] == json.dumps(push_payload())


def test_assay_rejection_dead_letters_with_the_report(tmp_path: Path, clock) -> None:
    report = {
        "payloads_scanned": 1,
        "errors": 2,
        "warnings": 0,
        "groups": [{"fingerprint": "schema:type:members[*].zip", "path": "members[*].zip"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "assay.test":
            return httpx.Response(200, json=report)
        raise AssertionError("the destination must not be called after a rejection")

    route = Route(
        id="validated",
        event="push",
        destination="http://sink.test/hook",
        assay={
            "url": "http://assay.test/assay",
            "schema": {"type": "object"},
        },
    )
    config = make_config(tmp_path, routes=[route])
    cfg, store, dispatcher = build(tmp_path, handler, clock, config)
    queue(store, cfg)
    assert dispatcher.deliver_pending() == {"dead": 1}
    dead = store.dead_letters()[0]
    assert dead["reason"] == "validation"
    assert dead["report"] == report


def test_assay_outage_is_retried_then_dead_lettered(tmp_path: Path, clock) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("assay down", request=request)

    route = Route(
        id="validated",
        event="push",
        destination="http://sink.test/hook",
        assay={"url": "http://assay.test/assay", "schema": {"type": "object"}},
        retries=1,
    )
    config = make_config(tmp_path, routes=[route])
    cfg, store, dispatcher = build(tmp_path, handler, clock, config)
    queue(store, cfg)
    assert dispatcher.deliver_pending() == {"retried": 1}
    clock.advance(5.0)
    assert dispatcher.deliver_pending() == {"dead": 1}
    dead = store.dead_letters()[0]
    assert dead["reason"] == "assay-error"
