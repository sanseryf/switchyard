"""The dashboard: shell, static assets and the read-only UI endpoints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import make_config, sink_response, transport
from switchyard.api import create_app
from switchyard.config import Route
from switchyard.delivery import Dispatcher
from switchyard.store import Store


@pytest.fixture()
def ui(tmp_path: Path):
    route = Route(
        id="validated",
        event="push",
        destination="http://sink.test/hook",
        assay={"url": "http://assay.test/assay", "schema": {"type": "object"}},
    )
    config = make_config(tmp_path, routes=[route])
    store = Store(config.database)
    event_id, _ = store.add_event("d-1", "push", {"repository": {"full_name": "a/b"}})
    delivery_id = store.add_delivery(
        event_id, "validated", "http://sink.test/hook", "{}", {"a": 1}, now=1000.0
    )
    store.mark_delivered(delivery_id)
    dead_event, _ = store.add_event("d-2", "member", {"members": []})
    dead_delivery = store.add_delivery(
        dead_event, "validated", "http://sink.test/hook", "{}", {"members": []}, now=1000.0
    )
    dead = store.delivery(dead_delivery)
    assert dead is not None
    store.mark_dead(
        dead,
        reason="validation",
        error="assay rejected",
        report={"errors": 1, "groups": [{"path": "members[*].zip"}]},
        now=1000.0,
    )
    dispatcher = Dispatcher(config, store, transport=transport(lambda request: sink_response()))
    client = TestClient(create_app(config, store=store, dispatcher=dispatcher, secret="s"))
    return client, store


def test_dashboard_shell(ui) -> None:
    client, _ = ui
    page = client.get("/").text
    assert "Switchyard" in page
    assert 'id="stat-events"' in page
    assert 'id="dlq-body"' in page
    assert "/static/app.js" in page


def test_static_assets(ui) -> None:
    client, _ = ui
    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert "--accent" in css.text
    script = client.get("/static/app.js")
    assert script.status_code == 200
    assert "/ui/stats" in script.text


def test_stats_endpoint(ui) -> None:
    client, _ = ui
    stats = client.get("/ui/stats").json()
    assert stats["events"] == 2
    assert stats["deliveries"]["delivered"] == 1
    assert stats["dead_letters"] == 1


def test_recent_events(ui) -> None:
    client, _ = ui
    events = client.get("/ui/events").json()["events"]
    assert len(events) == 2
    assert events[0]["delivery"] == "d-2"
    assert events[0]["dead"] == 1
    assert events[1]["delivered"] == 1


def test_deliveries_filter(ui) -> None:
    client, _ = ui
    delivered = client.get("/ui/deliveries?status=delivered").json()["deliveries"]
    assert len(delivered) == 1
    assert delivered[0]["status"] == "delivered"
    assert client.get("/ui/deliveries?status=nope").status_code == 400


def test_routes_are_exposed_without_secrets(ui) -> None:
    client, _ = ui
    routes = client.get("/ui/routes").json()["routes"]
    assert routes[0]["id"] == "validated"
    assert routes[0]["validates_with_assay"] is True
    assert "secret" not in json.dumps(routes).lower()


def test_dlq_detail_still_returns_the_report(ui) -> None:
    client, _ = ui
    dead = client.get("/dlq").json()["dead_letters"][0]
    detail = client.get(f"/dlq/{dead['id']}").json()
    assert detail["report"]["groups"][0]["path"] == "members[*].zip"
