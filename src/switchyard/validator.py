"""The Assay validation stage: call Assay, keep the report if the payload fails."""

from __future__ import annotations

from typing import Any

import httpx

from .config import AssayStage


class AssayError(Exception):
    """Assay could not produce a report: treat it as a transient delivery failure."""


class AssayValidator:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._client = client or httpx.Client(transport=transport)

    def validate(self, stage: AssayStage, payload: dict[str, Any]) -> dict[str, Any]:
        body = {"payload": payload, "schema": stage.schema_ or {}}
        try:
            response = self._client.post(stage.url, json=body, timeout=stage.timeout_seconds)
        except httpx.HTTPError as exc:
            raise AssayError(f"assay unavailable: {exc}") from exc
        if response.status_code != 200:
            raise AssayError(f"assay returned HTTP {response.status_code}")
        try:
            report = response.json()
        except ValueError as exc:
            raise AssayError("assay returned invalid JSON") from exc
        if not isinstance(report, dict):
            raise AssayError("assay returned an unexpected report")
        return report

    @staticmethod
    def should_fail(stage: AssayStage, report: dict[str, Any]) -> bool:
        errors = int(report.get("errors", 0) or 0)
        warnings = int(report.get("warnings", 0) or 0)
        return errors > 0 or (stage.fail_on == "warning" and warnings > 0)

    def close(self) -> None:
        self._client.close()
