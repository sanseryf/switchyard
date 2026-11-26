"""CLI: routes, worker drain, DLQ listing and replay."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from switchyard.cli import app
from switchyard.store import Store

runner = CliRunner()


def write_config(tmp_path: Path) -> Path:
    config = tmp_path / "routes.yaml"
    config.write_text(
        """
        database: state.db
        routes:
          - id: push-sink
            event: push
            destination: http://sink.test/hook
        """,
        encoding="utf-8",
    )
    return config


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "switchyard 0.3.0" in result.output


def test_routes_are_listed(tmp_path: Path) -> None:
    result = runner.invoke(app, ["routes", "--config", str(write_config(tmp_path))])
    assert result.exit_code == 0
    assert "push-sink" in result.output
    assert "http://sink.test/hook" in result.output


def test_worker_once_with_nothing_due(tmp_path: Path) -> None:
    result = runner.invoke(app, ["worker", "--config", str(write_config(tmp_path)), "--once"])
    assert result.exit_code == 0


def test_dlq_lists_and_replay_reports(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    with Store(tmp_path / "state.db") as store:
        event_id, _ = store.add_event("d-1", "push", {"a": 1})
        delivery_id = store.add_delivery(
            event_id, "push-sink", "http://sink.test/hook", "{}", {"a": 1}, now=1.0
        )
        delivery = store.delivery(delivery_id)
        assert delivery is not None
        report = {"errors": 1, "groups": [{"path": "members[*].zip"}]}
        store.mark_dead(delivery, reason="validation", error="rejected", report=report, now=1.0)

    listing = runner.invoke(app, ["dlq", "--config", str(config_path)])
    assert listing.exit_code == 0
    assert "validation" in listing.output
    assert "1 groups" in listing.output

    replay = runner.invoke(app, ["replay", "1", "--config", str(config_path)])
    # The destination is not reachable in this test, but the command must not crash:
    # it queues the replay, attempts it and reports the retry.
    assert replay.exit_code == 0
    assert "delivery_id" in replay.output


def test_replay_missing_dead_letter_exits_three(tmp_path: Path) -> None:
    result = runner.invoke(app, ["replay", "99", "--config", str(write_config(tmp_path))])
    assert result.exit_code == 3
    assert "not found" in result.output


def test_bad_config_exits_three(tmp_path: Path) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text("routes: [unclosed", encoding="utf-8")
    result = runner.invoke(app, ["routes", "--config", str(broken)])
    assert result.exit_code == 3
    assert "not valid YAML" in result.output


def test_empty_dlq_says_so(tmp_path: Path) -> None:
    result = runner.invoke(app, ["dlq", "--config", str(write_config(tmp_path))])
    assert result.exit_code == 0
    assert "no dead letters" in result.output
