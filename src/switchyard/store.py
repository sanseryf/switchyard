"""SQLite persistence: events, deliveries and dead letters.

The delivery table is the queue. A row is due when ``next_attempt_at`` has passed and
its status is ``pending``; the dispatcher moves it to ``delivered`` or to
``dead_letters`` when attempts are exhausted. Nothing is deleted: replaying a dead
letter creates a new delivery for the same event.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery TEXT NOT NULL UNIQUE,
    event TEXT NOT NULL,
    payload TEXT NOT NULL,
    received_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    route_id TEXT NOT NULL,
    destination TEXT NOT NULL,
    body TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_deliveries_due ON deliveries(status, next_attempt_at);
CREATE TABLE IF NOT EXISTS dead_letters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,
    route_id TEXT NOT NULL,
    destination TEXT NOT NULL,
    reason TEXT NOT NULL,
    body TEXT NOT NULL,
    payload TEXT NOT NULL,
    report TEXT,
    created_at REAL NOT NULL
);
"""


class DuplicateEvent(Exception):
    """The delivery id has been seen before; the payload was not stored twice."""


class Store:
    """A thin, explicit data layer; no ORM, no hidden queries."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(SCHEMA)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------ events

    def add_event(self, delivery: str, event: str, payload: dict[str, Any]) -> tuple[int, bool]:
        """Insert once; ``(event_id, duplicate)`` on repeat delivery ids."""
        try:
            cursor = self._connection.execute(
                "INSERT INTO events (delivery, event, payload, received_at) VALUES (?, ?, ?, ?)",
                (delivery, event, json.dumps(payload, ensure_ascii=False), time.time()),
            )
            self._connection.commit()
            return int(cursor.lastrowid or 0), False
        except sqlite3.IntegrityError:
            row = self._connection.execute(
                "SELECT id FROM events WHERE delivery = ?", (delivery,)
            ).fetchone()
            if row is None:  # pragma: no cover - the unique index guarantees one
                raise
            return int(row["id"]), True

    def event(self, event_id: int) -> dict[str, Any] | None:
        row = self._connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ deliveries

    def add_delivery(
        self,
        event_id: int,
        route_id: str,
        destination: str,
        body: str,
        payload: dict[str, Any],
        *,
        now: float,
    ) -> int:
        cursor = self._connection.execute(
            "INSERT INTO deliveries "
            "(event_id, route_id, destination, body, payload, status, attempts, next_attempt_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', 0, ?)",
            (event_id, route_id, destination, body, json.dumps(payload), now),
        )
        self._connection.commit()
        return int(cursor.lastrowid or 0)

    def due_deliveries(self, now: float, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM deliveries WHERE status = 'pending' AND next_attempt_at <= ? "
            "ORDER BY next_attempt_at LIMIT ?",
            (now, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def pending_for_event(self, event_id: int) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM deliveries WHERE event_id = ? AND status = 'pending'", (event_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def delivery(self, delivery_id: int) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM deliveries WHERE id = ?", (delivery_id,)
        ).fetchone()
        return dict(row) if row else None

    def mark_delivered(self, delivery_id: int) -> None:
        self._connection.execute(
            "UPDATE deliveries SET status = 'delivered', last_error = NULL WHERE id = ?",
            (delivery_id,),
        )
        self._connection.commit()

    def reschedule(
        self, delivery_id: int, *, attempts: int, next_attempt_at: float, error: str
    ) -> None:
        self._connection.execute(
            "UPDATE deliveries SET attempts = ?, next_attempt_at = ?, last_error = ? WHERE id = ?",
            (attempts, next_attempt_at, error, delivery_id),
        )
        self._connection.commit()

    # ------------------------------------------------------------------ dead letters

    def mark_dead(
        self,
        delivery: dict[str, Any],
        *,
        reason: str,
        error: str,
        report: dict[str, Any] | None = None,
        now: float,
    ) -> int:
        self._connection.execute(
            "UPDATE deliveries SET status = 'dead', last_error = ? WHERE id = ?",
            (error, delivery["id"]),
        )
        cursor = self._connection.execute(
            "INSERT INTO dead_letters "
            "(delivery_id, event_id, route_id, destination, reason, body, payload, report, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                delivery["id"],
                delivery["event_id"],
                delivery["route_id"],
                delivery["destination"],
                reason,
                delivery["body"],
                delivery["payload"],
                json.dumps(report, ensure_ascii=False) if report is not None else None,
                now,
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid or 0)

    def dead_letters(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM dead_letters ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_with_report(row) for row in rows]

    def dead_letter(self, dead_id: int) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM dead_letters WHERE id = ?", (dead_id,)
        ).fetchone()
        return _with_report(row) if row else None

    def replay(self, dead_id: int, *, now: float) -> int | None:
        """Queue the dead letter's payload again under the current route config."""
        dead = self.dead_letter(dead_id)
        if dead is None:
            return None
        cursor = self._connection.execute(
            "INSERT INTO deliveries "
            "(event_id, route_id, destination, body, payload, status, attempts, next_attempt_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', 0, ?)",
            (
                dead["event_id"],
                dead["route_id"],
                dead["destination"],
                dead["body"],
                dead["payload"],
                now,
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid or 0)


def _with_report(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["report"] = json.loads(item["report"]) if item.get("report") else None
    return item
