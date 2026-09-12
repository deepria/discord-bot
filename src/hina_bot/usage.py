"""Content-free JSONL telemetry for logical Responses API calls and Discord turns."""
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import perf_counter


_TOKEN_FIELDS = ("input_tokens", "output_tokens", "total_tokens", "cached_tokens", "reasoning_tokens")


class UsageLogger:
    def __init__(self, path: str):
        self.handler = self._handler(path)
        self.exchange_handler = None
        if path:
            # Keep aggregate rows separate so summing usage.jsonl never double-counts tokens.
            exchange_path = Path(path).with_name("discord-usage.jsonl")
            self.exchange_handler = self._handler(str(exchange_path))
        self._exchange = ContextVar(f"hina_usage_exchange_{id(self)}", default=None)

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
    def _emit(handler, row: dict):
        if not handler:
            return
        try:
            handler.handle(logging.LogRecord(
                "hina.usage", logging.INFO, "", 0,
                json.dumps(row, ensure_ascii=False), (), None))
        except OSError:
            logging.getLogger("hina").warning("Usage log write failed")

    def close(self):
        if self.handler:
            self.handler.close()
        if self.exchange_handler:
            self.exchange_handler.close()

    @staticmethod
    def _bucket() -> dict:
        return {
            "calls": 0,
            "failed_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "usage_complete": True,
        }

    @staticmethod
    def _add_usage(bucket: dict, row: dict):
        bucket["calls"] += 1
        if row.get("status") == "error":
            bucket["failed_calls"] += 1
        if not isinstance(row.get("total_tokens"), int):
            bucket["usage_complete"] = False
        for field in _TOKEN_FIELDS:
            value = row.get(field)
            if isinstance(value, int):
                bucket[field] += value

    def _accumulate(self, row: dict):
        state = self._exchange.get()
        if state is None:
            return
        self._add_usage(state, row)
        operation = row["operation"]
        bucket = state["operations"].setdefault(operation, self._bucket())
        self._add_usage(bucket, row)
        state["models"].add(row["model"])

    @contextmanager
    def exchange(self, scope: str):
        """Aggregate all API calls caused by one Discord conversational response."""
        if not self.exchange_handler:
            yield
            return
        state = self._bucket()
        state.update({
            "at": datetime.now(UTC).isoformat(),
            "scope": scope,
            "operations": {},
            "models": set(),
        })
        started = perf_counter()
        token = self._exchange.set(state)
        error_type = None
        try:
            yield
        except BaseException as exc:
            error_type = type(exc).__name__
            raise
        finally:
            self._exchange.reset(token)
            state["elapsed_ms"] = round((perf_counter() - started) * 1000)
            if error_type:
                state["status"] = "error"
                state["error_type"] = error_type
            elif state["failed_calls"]:
                state["status"] = "completed_with_api_errors"
            else:
                state["status"] = "completed"
            state["models"] = sorted(state["models"])
            self._emit(self.exchange_handler, state)

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
            self._accumulate(row)
            self._emit(self.handler, row)
