"""Command line: serve the webhook, run the retry worker, inspect and replay the DLQ."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import NoReturn

import typer

from . import __version__
from .config import Config, ConfigError, load_config
from .delivery import Dispatcher
from .store import Store

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Signed webhook ingest, routing, reliable delivery and a self-explaining DLQ.",
)


def _load(path: Path) -> Config:
    try:
        return load_config(path)
    except ConfigError as exc:
        _fail(str(exc))


def _fail(message: str) -> NoReturn:
    typer.secho(f"switchyard: {message}", fg="red", err=True)
    raise typer.Exit(3)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"switchyard {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True),
) -> None:
    """Webhook routing with a dead-letter queue that carries why."""


@app.command()
def serve(
    config: Path = typer.Option(..., "--config", "-c", exists=True, dir_okay=False),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
) -> None:
    """Serve the webhook endpoint and DLQ API."""
    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(_load(config)), host=host, port=port)


@app.command()
def worker(
    config: Path = typer.Option(..., "--config", "-c", exists=True, dir_okay=False),
    once: bool = typer.Option(False, "--once", help="Drain due deliveries and exit."),
) -> None:
    """Retry pending deliveries; run it continuously or from cron with --once."""
    settings = _load(config)
    with Store(settings.database) as store:
        dispatcher = Dispatcher(settings, store)
        while True:
            counts = dispatcher.deliver_pending()
            if any(counts.values()):
                typer.echo(json.dumps(counts, sort_keys=True))
            if once:
                break
            time.sleep(settings.poll_interval_seconds)


@app.command("dlq")
def dlq(
    config: Path = typer.Option(..., "--config", "-c", exists=True, dir_okay=False),
    limit: int = typer.Option(20, min=1, max=500),
) -> None:
    """List dead letters; the report column says why a payload never shipped."""
    settings = _load(config)
    with Store(settings.database) as store:
        letters = store.dead_letters(limit)
    if not letters:
        typer.echo("no dead letters")
        return
    for letter in letters:
        report = letter["report"] or {}
        groups = len(report.get("groups", []))
        errors = report.get("errors", 0)
        suffix = f" · assay: {groups} groups, {errors} errors" if report else ""
        typer.echo(
            f"#{letter['id']}  {letter['reason']:<12} {letter['route_id']:<20} "
            f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(letter['created_at']))}{suffix}"
        )


@app.command()
def replay(
    dead_id: int = typer.Argument(..., help="Dead letter id"),
    config: Path = typer.Option(..., "--config", "-c", exists=True, dir_okay=False),
) -> None:
    """Queue a dead letter's payload again and attempt it immediately."""
    settings = _load(config)
    with Store(settings.database) as store:
        dead = store.dead_letter(dead_id)
        if dead is None:
            _fail(f"dead letter {dead_id} not found")
        delivery_id = store.replay(dead_id, now=time.time())
        dispatcher = Dispatcher(settings, store)
        counts = dispatcher.deliver_event(dead["event_id"])
    typer.echo(json.dumps({"delivery_id": delivery_id, **counts}, sort_keys=True))


@app.command()
def routes(
    config: Path = typer.Option(..., "--config", "-c", exists=True, dir_okay=False),
) -> None:
    """Show the loaded routes and where they deliver."""
    for route in _load(config).routes:
        validate = "assay" if route.assay else "-"
        typer.echo(
            f"{route.id:<20} {route.event:<12} {route.action or '*':<12} "
            f"{route.destination}  [{validate}]"
        )


def main() -> None:
    app(prog_name="switchyard")


if __name__ == "__main__":  # pragma: no cover
    main()
