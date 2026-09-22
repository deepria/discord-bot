"""Read-only runtime-settings snapshot for trusted operational tooling.

The Discord process owns writes through :class:`RuntimeSettings`.  This module
intentionally opens the SQLite database read-only so a control-plane query
cannot create tables, migrate data, or modify an override as a side effect.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .config import Settings
from .runtime_config import (
    RUNTIME_SETTING_SPECS,
    decode_runtime_value,
    format_runtime_value,
)


def _database_uri(database_path: str) -> str:
    path = Path(database_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    return f"file:{path.resolve().as_posix()}?mode=ro"


def _stored_overrides(database_path: str, settings: Settings) -> dict[str, Any]:
    try:
        with sqlite3.connect(_database_uri(database_path), uri=True) as connection:
            rows = connection.execute("SELECT key, value FROM runtime_config").fetchall()
    except sqlite3.Error:
        return {}

    values: dict[str, Any] = {}
    for raw_key, encoded in rows:
        key = str(raw_key)
        spec = RUNTIME_SETTING_SPECS.get(key)
        if spec is None:
            continue
        try:
            values[key] = decode_runtime_value(spec, str(encoded), settings=settings)
        except (TypeError, ValueError):
            continue
    return values


def runtime_settings_snapshot(settings: Settings) -> list[dict[str, Any]]:
    """Return display-safe effective values and constraints without writing state."""
    overrides = _stored_overrides(settings.db_path, settings)
    rows: list[dict[str, Any]] = []
    for key, spec in RUNTIME_SETTING_SPECS.items():
        value = overrides.get(key, getattr(settings, key))
        rows.append(
            {
                "key": key,
                "env_name": spec.env_name,
                "value": value,
                "display_value": format_runtime_value(value),
                "source": "db" if key in overrides else "startup",
                "kind": spec.kind,
                "minimum": spec.minimum,
                "maximum": spec.maximum,
                "empty_allowed": spec.empty_allowed,
            }
        )
    return rows
