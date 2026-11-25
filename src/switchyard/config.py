"""Route configuration: YAML in, validated models out.

A typo in a route file must fail at startup, not produce a route that silently
never matches. Unknown keys are rejected, ids are unique, and a validation schema
referenced by path is read and checked while the config loads.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ConfigError(Exception):
    """The route file is unusable."""


class AssayStage(BaseModel):
    """Call Assay before delivering; a failing report becomes the DLQ attachment."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    url: str
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    schema_path: Path | None = None
    fail_on: Literal["error", "warning"] = "error"
    timeout_seconds: float = Field(default=10.0, gt=0)


class Route(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    event: str = "*"
    action: str | None = None
    destination: str
    transform: dict[str, str] | None = None
    assay: AssayStage | None = None
    retries: int = Field(default=3, ge=0, le=10)
    timeout_seconds: float = Field(default=10.0, gt=0)

    def matches(self, event: str, payload: dict[str, Any]) -> bool:
        if self.event not in {"*", event}:
            return False
        return self.action is None or str(payload.get("action", "")) == self.action


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    database: Path = Path("switchyard.db")
    secret_env: str = "GITHUB_WEBHOOK_SECRET"
    poll_interval_seconds: float = Field(default=2.0, gt=0)
    backoff_seconds: float = Field(default=5.0, gt=0)
    routes: tuple[Route, ...] = ()

    @model_validator(mode="after")
    def _unique_ids(self) -> Config:
        ids = [route.id for route in self.routes]
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        if duplicates:
            raise ValueError(f"route ids must be unique; duplicated: {duplicates}")
        return self


def load_config(path: Path) -> Config:
    """Read, validate and resolve a route file, raising :class:`ConfigError`."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror or exc}") from exc
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    if document is None:
        document = {}
    if not isinstance(document, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    try:
        config = Config.model_validate(document)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise ConfigError(f"invalid routes in {path}: {details}") from exc

    resolved: list[Route] = []
    for route in config.routes:
        if route.assay is not None:
            route = _resolve_schema(route, path)
        resolved.append(route)

    database = config.database if config.database.is_absolute() else path.parent / config.database
    return config.model_copy(update={"routes": tuple(resolved), "database": database})


def _resolve_schema(route: Route, config_path: Path) -> Route:
    stage = route.assay
    assert stage is not None
    if stage.schema_ is not None:
        return route
    if stage.schema_path is None:
        raise ConfigError(f"route {route.id!r}: assay needs either 'schema' or 'schema_path'")
    schema_path = stage.schema_path
    if not schema_path.is_absolute():
        schema_path = config_path.parent / schema_path
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read schema {schema_path}: {exc.strerror or exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"schema {schema_path} is not valid JSON: {exc}") from exc
    if not isinstance(schema, dict):
        raise ConfigError(f"schema {schema_path} must be a JSON object")
    return route.model_copy(update={"assay": stage.model_copy(update={"schema_": schema})})
