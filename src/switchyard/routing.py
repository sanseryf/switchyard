"""Route matching and payload shaping."""

from __future__ import annotations

import json
from typing import Any

from .config import Config, Route


class RoutingError(Exception):
    """A transform expression does not resolve; the route is misconfigured."""


def matching_routes(config: Config, event: str, payload: dict[str, Any]) -> list[Route]:
    """Every route that accepts this event and action, in file order."""
    return [route for route in config.routes if route.matches(event, payload)]


def build_body(route: Route, event: str, delivery: str, payload: dict[str, Any]) -> str:
    """The JSON body to deliver: the full envelope, or the route's transform."""
    context = {"event": event, "delivery": delivery, "payload": payload}
    if route.transform is None:
        document: dict[str, Any] = {
            "event": event,
            "delivery": delivery,
            "payload": payload,
        }
    else:
        document = {
            key: resolve(expression, context) for key, expression in route.transform.items()
        }
    return json.dumps(document, ensure_ascii=False)


def resolve(expression: str, context: dict[str, Any]) -> Any:
    """Resolve ``payload.repository.full_name`` against the event context."""
    value: Any = context
    for part in expression.split("."):
        if not isinstance(value, dict) or part not in value:
            raise RoutingError(f"transform expression {expression!r} does not resolve")
        value = value[part]
    return value
