"""Content-free structured event logging for Discord runtime state."""

import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

EVENT_SCHEMA_VERSION = 1


def turn_event_fields(turn_id: str, telemetry: dict, *, status: str, latency_ms: int,
                      memory_lifecycle: str, error_type: str | None = None,
                      provider: str | None = None, model: str | None = None) -> dict:
    """Build the versioned, content-free Phase 1 turn event payload."""
    answer = telemetry.get("answer") if isinstance(telemetry, dict) else None
    answer = answer if isinstance(answer, dict) else {}
    token_fields = ("input_tokens", "output_tokens", "total_tokens")
    tokens = {
        name.removesuffix("_tokens"): answer.get(name)
        if isinstance(answer.get(name), int) else None
        for name in token_fields
    }
    return {
        "turn_id": turn_id,
        "operation": "answer",
        "status": status,
        "provider": answer.get("provider") or provider,
        "model": answer.get("model") or model,
        "routing": {
            "web": answer.get("web_search_used"),
            "tier": answer.get("model_tier"),
        },
        "latency_ms": latency_ms,
        "tokens": tokens,
        "web_search_calls": answer.get("web_search_calls"),
        "memory_lifecycle": memory_lifecycle,
        "error_type": error_type,
    }


class EventLogger:
    def __init__(self, path: str):
        self.handler = self._handler(path)

    @staticmethod
    def _handler(path: str):
        if not path:
            return None
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=5_000_000, backupCount=3, encoding="utf-8", delay=True)
        handler.setFormatter(logging.Formatter("%(message)s"))
        return handler

    @staticmethod
    def _safe_fields(fields: dict) -> dict:
        safe = {}
        for key, value in fields.items():
            if isinstance(value, (str, int, float, bool, type(None))):
                safe[key] = value
            elif isinstance(value, (list, tuple)):
                safe[key] = [
                    item for item in value
                    if isinstance(item, (str, int, float, bool, type(None)))
                ][:20]
            elif isinstance(value, dict):
                safe[key] = {
                    str(inner_key): inner_value
                    for inner_key, inner_value in value.items()
                    if isinstance(inner_value, (str, int, float, bool, type(None)))
                }
        return safe

    def emit(self, event: str, **fields):
        if not self.handler:
            return
        row = {
            "at": datetime.now(UTC).isoformat(),
            "schema_version": EVENT_SCHEMA_VERSION,
            "event": event,
            **self._safe_fields(fields),
        }
        try:
            self.handler.handle(logging.LogRecord(
                "rio.events",
                logging.INFO,
                "",
                0,
                json.dumps(row, ensure_ascii=False),
                (),
                None,
            ))
        except OSError:
            logging.getLogger("rio").warning("Event log write failed")

    def close(self):
        if self.handler:
            self.handler.close()


class RuntimeStatusWriter:
    """Atomically publish content-free bot runtime state for the control agent."""

    def __init__(self, path: str):
        self.path = Path(path) if path else None

    def write(self, **fields):
        if self.path is None:
            return
        row = {
            "at": datetime.now(UTC).isoformat(),
            **EventLogger._safe_fields(fields),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            logging.getLogger("rio").warning("Runtime status write failed")


__all__ = ["EVENT_SCHEMA_VERSION", "EventLogger", "RuntimeStatusWriter", "turn_event_fields"]
