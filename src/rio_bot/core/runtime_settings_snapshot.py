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


def runtime_config_audit_snapshot(
    settings: Settings, *, limit: int = 50
) -> list[dict[str, str | None]]:
    """Return recent content-free runtime configuration audit events read-only."""
    safe_limit = max(1, min(limit, 100))
    try:
        with sqlite3.connect(_database_uri(settings.db_path), uri=True) as connection:
            rows = connection.execute(
                """SELECT id,occurred_at,actor_kind,actor_id,action,target,outcome,request_id
                   FROM runtime_config_audit
                   ORDER BY occurred_at DESC, rowid DESC
                   LIMIT ?""",
                (safe_limit,),
            ).fetchall()
    except sqlite3.Error:
        return []
    return [
        {
            "id": str(row[0]),
            "occurred_at": str(row[1]),
            "actor_kind": str(row[2]),
            "actor_id": str(row[3]),
            "action": str(row[4]),
            "target": str(row[5]),
            "outcome": str(row[6]),
            "request_id": str(row[7]) if row[7] is not None else None,
        }
        for row in rows
    ]


def policy_snapshot(settings: Settings) -> list[dict[str, str]]:
    """Return content-free policy overrides with their effective inherited values."""
    try:
        with sqlite3.connect(_database_uri(settings.db_path), uri=True) as connection:
            memory = dict(connection.execute("SELECT scope,mode FROM memory_modes").fetchall())
            chatlog = dict(connection.execute("SELECT scope,mode FROM chat_log_modes").fetchall())
            notes = dict(connection.execute("SELECT scope,text FROM notes").fetchall())
            manual = dict(connection.execute("SELECT scope,text FROM manual_notes").fetchall())
    except sqlite3.Error:
        return []
    capture_prefix = "config:chatlog_capture:"
    capture = {
        str(key)[len(capture_prefix):]: str(value)
        for key, value in {**notes, **manual}.items()
        if str(key).startswith(capture_prefix) and str(value) in {"all", "direct"}
    }
    scopes = sorted({"global", *memory, *chatlog, *capture})

    def inherited(values: dict[str, str], scope: str, default: str) -> tuple[str, str]:
        if scope in values:
            return values[scope], "override"
        if ":channel:" in scope:
            parent = scope.rsplit(":channel:", 1)[0]
            if parent.startswith("guild:") and parent in values:
                return values[parent], "server"
        if "global" in values:
            return values["global"], "global"
        return default, "default"

    rows = []
    for scope in scopes:
        memory_value, memory_source = inherited(memory, scope, "normal")
        chatlog_value, chatlog_source = inherited(chatlog, scope, "on")
        capture_value, capture_source = inherited(capture, scope, "all")
        rows.append({
            "scope": scope,
            "memory_override": str(memory.get(scope, "inherit")),
            "memory_effective": memory_value,
            "memory_source": memory_source,
            "chatlog_override": str(chatlog.get(scope, "inherit")),
            "chatlog_effective": chatlog_value,
            "chatlog_source": chatlog_source,
            "capture_override": str(capture.get(scope, "inherit")),
            "capture_effective": capture_value,
            "capture_source": capture_source,
        })
    return rows
