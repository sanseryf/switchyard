# Deploying Switchyard

A single host is enough: one service process (ingest + API) and one worker process
(retries). The queue is the SQLite file, so there is no broker to operate, and no
second worker.

## Layout

| Path | Purpose |
| --- | --- |
| `/opt/switchyard` | the checkout or unpacked wheel, with its virtualenv |
| `/etc/switchyard/routes.yaml` | routes and secrets configuration |
| `/etc/switchyard/switchyard.env` | `GITHUB_WEBHOOK_SECRET` (mode 600, root:switchyard) |
| `/var/lib/switchyard/switchyard.db` | events, deliveries and dead letters |

## systemd

```bash
sudo useradd --system --home /opt/switchyard --shell /usr/sbin/nologin switchyard
sudo install -d -o switchyard -g switchyard /var/lib/switchyard
sudo install -d -o root -g switchyard -m 750 /etc/switchyard

sudo cp deploy/switchyard.service deploy/switchyard-worker.service /etc/systemd/system/
sudo cp deploy/switchyard.env.example /etc/switchyard/switchyard.env
sudo chmod 600 /etc/switchyard/switchyard.env
sudo editor /etc/switchyard/routes.yaml

sudo systemctl daemon-reload
sudo systemctl enable --now switchyard.service switchyard-worker.service
```

Point the route file's `database:` at `/var/lib/switchyard/switchyard.db`.

## Reverse proxy

Keep `/webhooks/*` public but the dashboard (`/`), `/ui`, `/dlq` and `/events` private.
With Nginx Proxy Manager (the reference stack) that is two proxy hosts or one host plus
an access list; with Tailscale, bind Switchyard to the tailnet interface and expose the
webhook path through the tunnel only.

GitHub must reach `https://<host>/webhooks/github` over TLS, with content type
`application/json` and the same secret as `GITHUB_WEBHOOK_SECRET`.

## Docker

```bash
docker build -t switchyard .
docker run --rm -p 127.0.0.1:8080:8080 \
  -v "$PWD/routes.yaml:/config/routes.yaml" \
  -v "$PWD/state:/state" \
  -e GITHUB_WEBHOOK_SECRET=... \
  switchyard serve --config /config/routes.yaml --host 0.0.0.0
```

Run a second container with `switchyard worker --config /config/routes.yaml`.
Both must share the same `/state` volume, and only one worker may run.

## Backup

The database is the state. A nightly `sqlite3 switchyard.db ".backup backup.db"` (or
`cp` while the services are stopped) is enough; dead letters are the part you cannot
reconstruct.

## Health

`GET /health` returns the version. The worker logs a JSON line whenever it processes a
delivery, so `journalctl -u switchyard-worker` is the retry history.
