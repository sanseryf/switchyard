"""Route files fail loudly."""

from __future__ import annotations

from pathlib import Path

import pytest

from switchyard.config import ConfigError, load_config


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "routes.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_routes_load_and_paths_resolve(tmp_path: Path) -> None:
    (tmp_path / "schema.json").write_text('{"type": "object"}', encoding="utf-8")
    config = load_config(
        write(
            tmp_path,
            """
            database: state/switchyard.db
            secret_env: MY_SECRET
            routes:
              - id: push-to-sink
                event: push
                destination: http://sink.test/hook
                transform:
                  repo: payload.repository.full_name
                assay:
                  url: http://assay.test/assay
                  schema_path: schema.json
                  fail_on: error
                retries: 2
            """,
        )
    )
    route = config.routes[0]
    assert config.database == tmp_path / "state" / "switchyard.db"
    assert config.secret_env == "MY_SECRET"
    assert route.assay is not None and route.assay.schema_ == {"type": "object"}
    assert route.retries == 2


def test_duplicate_route_ids_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unique"):
        load_config(
            write(
                tmp_path,
                """
                routes:
                  - id: same
                    destination: http://a.test/
                  - id: same
                    destination: http://b.test/
                """,
            )
        )


def test_bad_retry_count_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="retries"):
        load_config(
            write(
                tmp_path,
                """
                routes:
                  - id: r
                    destination: http://a.test/
                    retries: 99
                """,
            )
        )


def test_unknown_keys_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="destinaton"):
        load_config(
            write(
                tmp_path,
                """
                routes:
                  - id: r
                    destinaton: http://a.test/
                """,
            )
        )


def test_assay_without_a_schema_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="schema"):
        load_config(
            write(
                tmp_path,
                """
                routes:
                  - id: r
                    destination: http://a.test/
                    assay:
                      url: http://assay.test/assay
                """,
            )
        )


def test_missing_schema_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read schema"):
        load_config(
            write(
                tmp_path,
                """
                routes:
                  - id: r
                    destination: http://a.test/
                    assay:
                      url: http://assay.test/assay
                      schema_path: nope.json
                """,
            )
        )


def test_invalid_yaml_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(write(tmp_path, "routes: [unclosed"))


def test_inline_schema_is_kept(tmp_path: Path) -> None:
    config = load_config(
        write(
            tmp_path,
            """
            routes:
              - id: r
                destination: http://a.test/
                assay:
                  url: http://assay.test/assay
                  schema:
                    type: object
            """,
        )
    )
    assert config.routes[0].assay is not None
    assert config.routes[0].assay.schema_ == {"type": "object"}
