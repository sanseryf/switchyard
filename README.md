# Switchyard

![CI](https://github.com/sanseryf/switchyard/actions/workflows/ci.yml/badge.svg)

> **A webhook router that explains its failures.** Signed ingest, deduplication,
> routing, retries with backoff, and a dead-letter queue where every entry carries
> the grouped validation report that stopped it.

**Status: v0.1.0.** Milestones M1–M3 are built: signed GitHub ingest, routing,
reliable delivery and the DLQ. The Assay validation stage (M8 in the Assay
architecture) is wired through the HTTP API: a payload Assay rejects is dead-lettered
with its report attached, so the queue answers *why* instead of *what*.

```text
GitHub ──POST + HMAC──▶  ingest ──▶ events + deliveries (SQLite) ──▶ dispatcher
                          │                                             │
                    verify signature                                    ├─ Assay validation ──reject──▶ DLQ + report
                    dedupe delivery id                                  ├─ delivery 2xx ──▶ destination
                                                                        └─ retries exhausted ──▶ DLQ
                                                                                 ▲
                                                                        replay ──┘
```

## Why this exists

Webhooks fail quietly. A destination returns 500 for an hour, the sender gives up, and
the only record of the lost work is a log line nobody reads. Or the destination accepts
the payload and rejects it later, and the sender has no idea which field was wrong.

Switchyard makes three promises:

1. **Nothing is delivered twice.** The GitHub delivery id is a unique key, so a retried
   or replayed webhook does not create a second event.
2. **Nothing is lost.** Every accepted event and every delivery attempt is in SQLite;
   exhausted retries become dead letters, not silence.
3. **Every dead letter explains itself.** When Assay rejects a payload, its grouped
   report (root causes, paths, hints, samples) is stored with the dead letter and
   returned by the API and the CLI.

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). Assay is needed for the
validation demo; everything else runs without it.

```bash
uv sync --extra dev
```

Terminal 1, Assay's API (from the Assay checkout):

```bash
cd ../assay
uv sync --extra api
uv run assay serve --port 8000
```

Terminal 2, a destination that prints what it receives:

```bash
cd switchyard
uv run python examples/receiver.py
```

Terminal 3, Switchyard:

```bash
# PowerShell: $env:GITHUB_WEBHOOK_SECRET = "dev-secret"
export GITHUB_WEBHOOK_SECRET=dev-secret
uv run switchyard serve --config examples/routes.yaml --port 8080
```

Terminal 4, send a signed webhook:

```bash
# PowerShell: $env:GITHUB_WEBHOOK_SECRET = "dev-secret"
export GITHUB_WEBHOOK_SECRET=dev-secret
uv run python examples/send_webhook.py --event member --delivery d-001 \
  --payload examples/bad-member.json
```

The payload has `zip: 7030` where the schema requires a five-digit string, so Assay
rejects it and nothing reaches the receiver. The DLQ shows why:

```text
$ uv run switchyard dlq --config examples/routes.yaml
#1  validation  members-to-sink  2026-11-24 21:58 · assay: 1 groups, 1 errors
```

`GET /dlq/1` returns the full report: the fingerprint, `members[*].zip`, the hint about
leading zeros, and the sample values. In the web UI of your API client:

```bash
curl -s localhost:8080/dlq/1 | jq '.report.groups[0]'
```

Now send the good payload (new delivery id) and watch the receiver print it:

```bash
uv run python examples/send_webhook.py --event member --delivery d-002 \
  --payload examples/good-member.json
```

## How it works

### Ingest

`POST /webhooks/github` verifies `X-Hub-Signature-256` with HMAC-SHA256 over the exact
bytes, using the secret named by `secret_env` (default `GITHUB_WEBHOOK_SECRET`). A bad
or missing signature is `401`; a missing `X-GitHub-Delivery` is `400`; a repeat delivery
id returns `200 {"duplicate": true}` without creating another event.

### Routing

Routes are YAML, validated with Pydantic at startup. Unknown keys, duplicate ids and a
missing validation schema fail before the server binds.

```yaml
database: state/switchyard.db
secret_env: GITHUB_WEBHOOK_SECRET
routes:
  - id: pushes-to-sink
    event: push                  # exact event name, or "*"
    destination: http://127.0.0.1:9100/hook
    transform:                   # optional: shape the outgoing JSON
      repository: payload.repository.full_name
      event: event
    retries: 3
```

`event` matches `X-GitHub-Event`; `action` optionally matches `payload.action`. Without
`transform`, the destination receives `{event, delivery, payload}`.

### Delivery

The dispatcher makes the first attempt in a background task and retries from the worker
(`switchyard worker`, or `--once` from cron) with capped exponential backoff. A delivery
is dead after `retries + 1` attempts. Every attempt and error is persisted.

### The Assay stage (M8)

```yaml
    assay:
      url: http://127.0.0.1:8000/assay
      schema_path: member.schema.json
      fail_on: error             # or "warning" to be stricter
```

Before delivering, Switchyard posts `{payload, schema}` to Assay. A report with errors
(or warnings, when `fail_on: warning`) dead-letters the payload with `reason:
validation`, the report attached and no delivery attempt. If Assay is unreachable the
delivery is *retried*, then dead-lettered with `reason: assay-error`. A broken validator
must not silently drop traffic.

### Dead letters and replay

```bash
uv run switchyard dlq --config examples/routes.yaml        # list
uv run switchyard replay 1 --config examples/routes.yaml   # queue and attempt again
```

`POST /dlq/{id}/replay` does the same over HTTP. Replay reuses the stored body and
destination; the payload is re-validated before it ships.

## Deploying

[`deploy/`](deploy/README.md) has systemd units for the service and the single worker,
an env template, and a Dockerfile. The short version: one process serves, one worker
retries, both share one SQLite file, `/dlq` and `/events` stay private while
`/webhooks/github` is public behind TLS.

```bash
docker build -t switchyard .
docker run --rm -p 127.0.0.1:8080:8080 \
  -v "$PWD/examples/routes.yaml:/config/routes.yaml" \
  -v "$PWD/examples/state:/state" \
  -e GITHUB_WEBHOOK_SECRET=... \
  switchyard serve --config /config/routes.yaml --host 0.0.0.0
```

## Design decisions

| Decision | Why |
| --- | --- |
| Dedupe on the GitHub delivery id, not payload hash | GitHub retries the same delivery; two identical payloads can be genuinely different events |
| SQLite as the queue | One file, no broker to operate, and the DLQ is queryable with SQL; the delivery table is the queue |
| Validation failures are dead immediately | A schema mismatch will not fix itself; retrying only wastes the destination's time |
| Validator outages retry | A dead Assay must not look like bad data |
| Replay re-validates | Fixing a mapping should let old dead letters through; a still-bad payload should still fail |
| Report stored as JSON | The DLQ is consumed by the API, the CLI and (later) dashboards; no lossy summary |
| Secret from the environment | A webhook secret in a committed YAML file is a leaked secret |

## Development

```bash
uv run pytest                       # unit, delivery, API and CLI tests
uv run pytest --cov=switchyard      # 91% coverage
uv run ruff check .
uv run ruff format --check .
uv run mypy                         # strict
```

CI mirrors Assay: ruff, format check, `mypy --strict`, pytest with a coverage gate, on
Python 3.11 and 3.12.

## Limitations, stated plainly

- **At-least-once delivery.** A destination must dedupe on `delivery` if it matters; the
  retry after a timeout can genuinely repeat a request that succeeded.
- **Single worker.** SQLite and a single dispatcher process are deliberate for v0.1;
  running two workers can race on the same due rows.
- **Transform is path extraction**, not a template language.
- **GitHub signatures only.** Other providers sign differently; adding one is a function
  in `security.py` and a header name in the ingest route.
- **Replay uses the stored route destination/body**, so changing a route does not move
  old dead letters to the new destination.
- No UI yet: the JSON API and the CLI are the interfaces.

## Layout

```
src/switchyard/
  config.py      YAML routes → validated models, schema resolution
  security.py    HMAC-SHA256 signature verification
  store.py       SQLite: events, deliveries (the queue), dead letters, replay
  routing.py     matching and transform resolution
  validator.py   Assay HTTP client and the pass/fail rule
  delivery.py    dispatcher: validate, POST, backoff, dead-letter
  api.py         webhook ingest, events, DLQ list/detail/replay
  cli.py         serve, worker, dlq, replay, routes
examples/        demo routes, schema, payloads, receiver and webhook sender
tests/           security, config, routing, store, delivery, API, CLI
```

## License

MIT.
