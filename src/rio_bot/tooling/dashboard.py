"""Localhost-only, read-only JSON dashboard for retained bot telemetry."""

from __future__ import annotations

import argparse
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def overview(db_path: str, usage_path: str) -> dict:
    """Read only bounded aggregate metadata; never construct the production Store."""
    database = Path(db_path)
    result = {"db_available": database.exists(), "turns": None, "structured_memory_items": None,
              "usage_rows": 0, "usage_errors": 0}
    if database.exists():
        connection = sqlite3.connect(f"file:{database.absolute()}?mode=ro", uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            for table, key in (("turns", "turns"), ("structured_memory_items", "structured_memory_items")):
                try:
                    result[key] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                except sqlite3.OperationalError:
                    result[key] = None
        finally:
            connection.close()
    usage = Path(usage_path)
    if usage.exists():
        for line in usage.read_text(encoding="utf-8").splitlines()[-1000:]:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            result["usage_rows"] += 1
            result["usage_errors"] += int(row.get("status") == "error")
    return result


def serve(db_path: str, usage_path: str, host: str, port: int) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("dashboard host must be localhost")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/overview":
                self.send_error(404)
                return
            payload = json.dumps(overview(db_path, usage_path)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return

    ThreadingHTTPServer((host, port), Handler).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve Rio retained telemetry locally in read-only mode.")
    parser.add_argument("--db", default="data/rio.sqlite3")
    parser.add_argument("--usage-log", default="data/logs/usage.jsonl")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    try:
        serve(args.db, args.usage_log, args.host, args.port)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
