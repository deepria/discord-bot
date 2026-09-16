"""Content-free structured event logging for Discord runtime state."""

import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


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


__all__ = ["EventLogger"]
