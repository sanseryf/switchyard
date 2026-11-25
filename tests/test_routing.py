"""Routing: which routes accept an event, and what body they send."""

from __future__ import annotations

import json

import pytest

from conftest import push_payload
from switchyard.config import Config, Route
from switchyard.routing import RoutingError, build_body, matching_routes


def config(*routes: Route) -> Config:
    return Config(routes=routes)


def test_event_and_action_match() -> None:
    routes = (
        Route(id="all", event="*", destination="http://a.test/"),
        Route(id="pushes", event="push", destination="http://b.test/"),
        Route(id="opened", event="pull_request", action="opened", destination="http://c.test/"),
    )
    matched = matching_routes(config(*routes), "push", {"action": "created"})
    assert [route.id for route in matched] == ["all", "pushes"]


def test_action_route_matches_only_its_action() -> None:
    route = Route(id="opened", event="pull_request", action="opened", destination="http://c.test/")
    assert matching_routes(config(route), "pull_request", {"action": "opened"})
    assert not matching_routes(config(route), "pull_request", {"action": "closed"})


def test_default_body_is_the_full_envelope() -> None:
    route = Route(id="all", destination="http://a.test/")
    body = json.loads(build_body(route, "push", "delivery-1", push_payload()))
    assert body["event"] == "push"
    assert body["delivery"] == "delivery-1"
    assert body["payload"]["repository"]["full_name"] == "sanseryf/assay"


def test_transform_shapes_the_body() -> None:
    route = Route(
        id="shaped",
        destination="http://a.test/",
        transform={
            "repository": "payload.repository.full_name",
            "event": "event",
            "delivery": "delivery",
        },
    )
    body = json.loads(build_body(route, "push", "delivery-1", push_payload()))
    assert body == {
        "repository": "sanseryf/assay",
        "event": "push",
        "delivery": "delivery-1",
    }


def test_an_unresolvable_transform_is_an_error() -> None:
    route = Route(id="broken", destination="http://a.test/", transform={"x": "payload.nope"})
    with pytest.raises(RoutingError):
        build_body(route, "push", "delivery-1", push_payload())
