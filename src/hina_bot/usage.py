"""Content-free JSONL telemetry for logical Responses API calls."""
import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import perf_counter


class UsageLogger:
    def __init__(self, path: str):
        self.handler = None
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.handler = RotatingFileHandler(
                path, maxBytes=5_000_000, backupCount=3, encoding="utf-8", delay=True)
            self.handler.setFormatter(logging.Formatter("%(message)s"))

    def close(self):
        if self.handler:
            self.handler.close()

    async def request(self, client, operation: str, **kwargs):
        started = perf_counter()
        row = {"at": datetime.now(UTC).isoformat(), "operation": operation,
               "model": kwargs["model"]}
        try:
            response = await client.responses.create(**kwargs)
            usage = getattr(response, "usage", None)
            row.update(status=response.status,
                       input_tokens=getattr(usage, "input_tokens", None),
                       output_tokens=getattr(usage, "output_tokens", None),
                       total_tokens=getattr(usage, "total_tokens", None),
                       cached_tokens=getattr(getattr(usage, "input_tokens_details", None),
                                             "cached_tokens", None),
                       reasoning_tokens=getattr(getattr(usage, "output_tokens_details", None),
                                                "reasoning_tokens", None))
            return response
        except BaseException as exc:
            row.update(status="error", error_type=type(exc).__name__)
            raise
        finally:
            row["elapsed_ms"] = round((perf_counter() - started) * 1000)
            if self.handler:
                try:
                    self.handler.handle(logging.LogRecord(
                        "hina.usage", logging.INFO, "", 0,
                        json.dumps(row, ensure_ascii=False), (), None))
                except OSError:
                    logging.getLogger("hina").warning("Usage log write failed")
