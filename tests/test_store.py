"""Persistence: dedupe, queue queries and replay."""

from __future__ import annotations

import json
from pathlib import Path

from switchyard.store import Store


def test_delivery_ids_are_deduplicated(tmp_path: Path) -> None:
    with Store(tmp_path / "db.sqlite") as store:
        first, duplicate_first = store.add_event("d-1", "push", {"a": 1})
        second, duplicate_second = store.add_event("d-1", "push", {"a": 2})
        stored = store.event(first)
    assert first == second
    assert duplicate_first is False
    assert duplicate_second is True
    assert stored is not None
    assert json.loads(stored["payload"]) == {"a": 1}


def test_due_deliveries_respect_the_clock(tmp_path: Path) -> None:
    with Store(tmp_path / "db.sqlite") as store:
        event_id, _ = store.add_event("d-1", "push", {})
        store.add_delivery(event_id, "r", "http://a.test/", "{}", {}, now=100.0)
        assert store.due_deliveries(99.0) == []
        assert len(store.due_deliveries(100.0)) == 1


def test_a_dead_letter_keeps_the_report_and_replays(tmp_path: Path) -> None:
    with Store(tmp_path / "db.sqlite") as store:
        event_id, _ = store.add_event("d-1", "push", {"repository": {"full_name": "a/b"}})
        delivery_id = store.add_delivery(
            event_id, "r", "http://a.test/", '{"event": "push"}', {"a": 1}, now=100.0
        )
        delivery = store.due_deliveries(100.0)[0]
        report = {"errors": 1, "warnings": 0, "groups": [{"path": "members[*].zip"}]}
        dead_id = store.mark_dead(
            delivery, reason="validation", error="assay rejected", report=report, now=100.0
        )
        dead = store.dead_letter(dead_id)
        assert dead is not None
        assert dead["reason"] == "validation"
        assert dead["report"] == report
        assert dead["payload"] == json.dumps({"a": 1})

        replayed = store.replay(dead_id, now=200.0)
        assert replayed is not None
        pending = store.due_deliveries(200.0)
        assert len(pending) == 1
        assert pending[0]["id"] == replayed
        assert pending[0]["payload"] == json.dumps({"a": 1})

        assert store.delivery(delivery_id) is not None
        assert store.delivery(delivery_id)["status"] == "dead"


def test_replay_of_a_missing_dead_letter_is_none(tmp_path: Path) -> None:
    with Store(tmp_path / "db.sqlite") as store:
        assert store.replay(999, now=1.0) is None
