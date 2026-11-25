"""HTTP surface: ingest, dedupe, DLQ inspection and replay."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from conftest import make_config, push_payload, sink_response, transport
from switchyard.api import create_app
from switchyard.config import Route
from switchyard.delivery import Dispatcher
from switchyard.security import sign
from switchyard.store import Store

SECRET = "s3cret"


def make_client(tmp_path: Path, handler, *, secret: str = SECRET, routes=None):
    config = make_config(tmp_path, routes=routes)
    store = Store(config.database)
    dispatcher = Dispatcher(config, store, transport=transport(handler), clock=lambda: 1000.0)
    return TestClient(create_app(config, store=store, dispatcher=dispatcher, secret=secret)), store


def post(
    client: TestClient,
    payload: dict,
    *,
    delivery: str = "d-1",
    event: str = "push",
    secret: str = SECRET,
):
    body = json.dumps(payload).encode()
    headers = {
        "X-Hub-Signature-256": sign(secret, body),
        "X-GitHub-Delivery": delivery,
        "X-GitHub-Event": event,
        "content-type": "application/json",
    }
    return client.post("/webhooks/github", content=body, headers=headers)


def test_health(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, lambda request: sink_response())
    assert client.get("/health").json()["status"] == "ok"


def test_valid_delivery_is_accepted_and_sent(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return sink_response()

    client, store = make_client(tmp_path, handler)
    response = post(client, push_payload())
    assert response.status_code == 202
    assert response.json()["deliveries"] == 1
    assert len(seen) == 1
    assert json.loads(seen[0].content)["payload"]["repository"]["full_name"] == "sanseryf/assay"
    assert store.due_deliveries(2000.0) == []  # delivered, not pending


def test_duplicate_delivery_id_is_not_processed_twice(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return sink_response()

    client, _ = make_client(tmp_path, handler)
    assert post(client, push_payload()).status_code == 202
    second = post(client, push_payload(), delivery="d-1")
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert calls["n"] == 1


def test_bad_signature_is_rejected(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, lambda request: sink_response())
    body = json.dumps(push_payload()).encode()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": "sha256=" + "0" * 64,
            "X-GitHub-Delivery": "d-1",
            "X-GitHub-Event": "push",
        },
    )
    assert response.status_code == 401


def test_missing_delivery_header_is_rejected(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, lambda request: sink_response())
    body = json.dumps(push_payload()).encode()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": sign(SECRET, body),
            "X-GitHub-Event": "push",
        },
    )
    assert response.status_code == 400


def test_missing_secret_is_a_503(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, lambda request: sink_response(), secret="")
    body = json.dumps(push_payload()).encode()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": sign(SECRET, body),
            "X-GitHub-Delivery": "d-1",
            "X-GitHub-Event": "push",
        },
    )
    assert response.status_code == 503


def test_action_routing_sends_nothing_when_it_does_not_match(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return sink_response()

    route = Route(
        id="opened-only",
        event="pull_request",
        action="opened",
        destination="http://sink.test/hook",
    )
    client, _ = make_client(tmp_path, handler, routes=[route])
    response = post(client, {"action": "closed"}, event="pull_request")
    assert response.status_code == 202
    assert response.json()["deliveries"] == 0
    assert calls["n"] == 0


def test_event_is_readable_after_ingest(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, lambda request: sink_response())
    event_id = post(client, push_payload()).json()["event_id"]
    event = client.get(f"/events/{event_id}").json()
    assert event["event"] == "push"
    assert event["payload"]["repository"]["full_name"] == "sanseryf/assay"


def test_validation_failure_lands_in_the_dlq_with_the_report(tmp_path: Path) -> None:
    report = {
        "payloads_scanned": 1,
        "errors": 2,
        "warnings": 0,
        "groups": [{"fingerprint": "schema:type", "path": "members[*].zip"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "assay.test":
            return httpx.Response(200, json=report)
        return sink_response()

    route = Route(
        id="validated",
        event="push",
        destination="http://sink.test/hook",
        assay={"url": "http://assay.test/assay", "schema": {"type": "object"}},
    )
    client, _ = make_client(tmp_path, handler, routes=[route])
    assert post(client, push_payload()).status_code == 202

    letters = client.get("/dlq").json()["dead_letters"]
    assert len(letters) == 1
    assert letters[0]["reason"] == "validation"
    assert letters[0]["report"] == report

    replay = client.post(f"/dlq/{letters[0]['id']}/replay")
    assert replay.status_code == 202
    assert replay.json()["replayed"] is True


def test_dlq_404s(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, lambda request: sink_response())
    assert client.get("/dlq/999").status_code == 404
    assert client.post("/dlq/999/replay").status_code == 404
