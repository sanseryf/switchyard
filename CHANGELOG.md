# Changelog

All notable changes to Switchyard are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [0.3.0] - 2026-11-26

### Changed

- **New visual system.** The dashboard now matches the rest of the tooling: near-black
  forest night and warm parchment day, bronze as the only functional accent, a green
  aurora atmosphere, and serif display type.
- Masthead rebuilt with a monogram, an eyebrow label and a sun/moon toggle. The choice is
  applied before paint by an inline script, persisted in `localStorage`, and reflected in
  `<meta name="theme-color">`.
- Stats, panels, tables, pills, the filter and the dead-letter drawer restyled. Panels and
  stats rise in with a short stagger; `prefers-reduced-motion` disables it.

### Added

- Self-hosted variable fonts: Fraunces, Inter and JetBrains Mono, with their SIL Open
  Font License files, in `static/fonts`. No external asset or CDN.
- A `?theme=day` preview parameter and a `?still=1` capture mode that disables motion.

## [0.2.0] - 2026-11-26

### Added

- **Dashboard** at `/`, served by the same FastAPI process:
  - totals for events, delivered, pending and dead letters
  - loaded routes, recent events and latest deliveries with a status filter
  - dead-letter detail with the stored Assay report, delivery body and original payload
  - replay from the drawer, auto-refresh every five seconds
  - responsive dark and light themes, no build step and no external assets
- **Read-only UI endpoints:** `/ui/stats`, `/ui/events`, `/ui/deliveries`, `/ui/routes`.
  Page sizes are clamped, and the routes response never includes the webhook secret.
- Jinja2 templates and static assets ship in the package; the Docker image serves them
  without changes.

### Changed

- The service now mounts `/static` and renders `/` from a template.
- `jinja2` is a runtime dependency.

## [0.1.0] - 2026-11-24

First release: signed GitHub ingest with dedupe, YAML routing and transforms, SQLite
delivery queue with retries and backoff, dead letters, the Assay validation stage, CLI,
systemd units and Docker.
