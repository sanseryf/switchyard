"""Shared fixtures: configs, a stub destination and a fixed clock."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from switchyard.config import Config, Route


def make_config(
    tmp_path: Path,
    *,
    routes: list[Route] | None = None,
    backoff_seconds: float = 5.0,
) -> Config:
    return Config(
        database=tmp_path / "switchyard.db",
        backoff_seconds=backoff_seconds,
        routes=tuple(
            routes
            if routes is not None
            else [
                Route(
                    id="push-sink",
                    event="push",
                    destination="http://sink.test/hook",
                )
            ]
        ),
    )


def transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def sink_response(status: int = 200, body: dict[str, Any] | None = None) -> httpx.Response:
    return httpx.Response(status, json=body or {"ok": True})


def push_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ref": "refs/heads/main",
        "repository": {"full_name": "sanseryf/assay"},
        "sender": {"login": "sanseryf"},
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def clock():
    now = [1_000.0]

    def read() -> float:
        return now[0]

    def advance(seconds: float) -> None:
        now[0] += seconds

    read.advance = advance  # type: ignore[attr-defined]
    return read
