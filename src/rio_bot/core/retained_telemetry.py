"""Bounded-field telemetry retained independently of operational JSONL rotation."""

import json
import logging
import os
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

APPLICATION_ID = 0x52494F54
SCHEMA_VERSION = 1
TRACE_DAYS = 30
USAGE_DAYS = 90


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        at = datetime.fromisoformat(value)
        return at.astimezone(UTC) if at.tzinfo else None
    except (ValueError, OverflowError):
        return None


def number(value):
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def label(value):
    return value if isinstance(value, str) and re.fullmatch(r"[\w./:+-]{1,128}", value) else None


def trace_record(row):
    if row.get("event") not in {"turn.started", "turn.completed", "turn.failed", "turn.usage"}:
        return None
    try:
        turn_id = str(UUID(row.get("turn_id", "")))
    except (ValueError, TypeError, AttributeError):
        return None
    at = timestamp(row.get("at"))
    if at is None:
        return None
    result = {"at": at.isoformat(), "turn_id": turn_id, "event": row["event"],
              "schema_version": 1}
    for key in ("operation", "status", "provider", "model", "memory_lifecycle", "error_type"):
        result[key] = label(row.get(key))
    for key in ("latency_ms", "web_search_calls"):
        result[key] = number(row.get(key))
    routing = row.get("routing")
    if isinstance(routing, dict):
        result["routing"] = {"web": routing.get("web") if type(routing.get("web")) is bool
                             else None, "tier": label(routing.get("tier"))}
    tokens = row.get("tokens")
    if isinstance(tokens, dict):
        result["tokens"] = {key: number(tokens.get(key)) for key in ("input", "output", "total")}
    return result


def verify_database(connection):
    if (connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
            or connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION):
        raise ValueError("Unsupported telemetry database")


class RetainedTelemetry:
    def __init__(self, path: str):
        self.path = Path(path) if path else None

    def connect(self):
        if self.path.is_symlink():
            raise ValueError("Telemetry path must not be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        connection = sqlite3.connect(self.path, timeout=0.05)
        try:
            app_id = connection.execute("PRAGMA application_id").fetchone()[0]
            tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if app_id == 0 and not tables:
                connection.executescript(f"""
                    PRAGMA application_id={APPLICATION_ID};
                    PRAGMA user_version={SCHEMA_VERSION};
                    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE traces (at TEXT NOT NULL, turn_id TEXT NOT NULL,
                        event TEXT NOT NULL, payload TEXT NOT NULL,
                        PRIMARY KEY (at, turn_id, event));
                    CREATE INDEX traces_turn ON traces(turn_id, at);
                    CREATE TABLE usage (id INTEGER PRIMARY KEY, at TEXT NOT NULL,
                        provider TEXT, model TEXT, status TEXT, total_tokens INTEGER,
                        elapsed_ms INTEGER);
                    CREATE INDEX usage_at ON usage(at);
                """)
                connection.execute("INSERT INTO meta VALUES ('started_at', ?)",
                                   (datetime.now(UTC).isoformat(),))
                connection.commit()
            verify_database(connection)
            connection.execute("PRAGMA secure_delete=ON")
            return connection
        except BaseException:
            connection.close()
            raise

    def _write(self, action, *, now=None):
        if self.path is None:
            return
        now = now or datetime.now(UTC)
        try:
            connection = self.connect()
            try:
                with connection:
                    action(connection, now)
                    connection.execute("INSERT OR REPLACE INTO meta VALUES ('last_write_at', ?)",
                                       (now.isoformat(),))
            finally:
                connection.close()
        except (OSError, sqlite3.Error, ValueError):
            # Fixed text only: no exception, path, row, or provider response.
            logging.getLogger("rio").warning("Retained telemetry write failed")

    @staticmethod
    def _trace(connection, row, now):
        record = trace_record(row)
        if record is None:
            return
        at = timestamp(record["at"])
        if not now - timedelta(days=TRACE_DAYS) <= at <= now:
            return
        connection.execute("INSERT OR IGNORE INTO traces VALUES (?, ?, ?, ?)",
                           (record["at"], record["turn_id"], record["event"],
                            json.dumps(record, ensure_ascii=False)))

    def event(self, row, *, now=None):
        if row.get("event") in {"turn.started", "turn.completed", "turn.failed"}:
            self._write(lambda conn, clock: self._trace(conn, row, clock), now=now)

    def usage(self, row, *, now=None):
        if row.get("operation") != "answer" or timestamp(row.get("at")) is None:
            return

        def write(connection, clock):
            at = timestamp(row["at"])
            if not clock - timedelta(days=USAGE_DAYS) <= at <= clock:
                return
            connection.execute("""INSERT INTO usage
                (at, provider, model, status, total_tokens, elapsed_ms) VALUES (?, ?, ?, ?, ?, ?)""",
                (at.isoformat(), label(row.get("provider")), label(row.get("model")),
                 label(row.get("status")), number(row.get("total_tokens")),
                 number(row.get("elapsed_ms"))))
            self._trace(connection, {
                "at": at.isoformat(), "turn_id": row.get("turn_id"), "event": "turn.usage",
                "operation": "answer", "status": row.get("status"),
                "provider": row.get("provider"), "model": row.get("model"),
                "latency_ms": row.get("elapsed_ms"), "error_type": row.get("error_type"),
                "tokens": {key: row.get(key + "_tokens") for key in ("input", "output", "total")},
                "web_search_calls": row.get("web_search_calls"),
            }, clock)

        self._write(write, now=now)

    def maintenance(self, *, apply=False, now=None):
        """Default is a read-only preview; apply touches only this dedicated store."""
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError("Reference time must include a timezone")
        now = now.astimezone(UTC)
        if self.path is None or self.path.is_symlink():
            raise ValueError("Telemetry store unavailable")
        connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw" if apply else
                                     self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.05)
        try:
            verify_database(connection)
            connection.execute("PRAGMA secure_delete=ON" if apply else "PRAGMA query_only=ON")
            result = {"apply": apply, "expired": {}}
            with connection:
                for table, days in (("traces", TRACE_DAYS), ("usage", USAGE_DAYS)):
                    cutoff = (now - timedelta(days=days)).isoformat()
                    result["expired"][table] = connection.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE at < ?", (cutoff,)).fetchone()[0]
                    if apply:
                        connection.execute(f"DELETE FROM {table} WHERE at < ?", (cutoff,))
            return result
        finally:
            connection.close()
