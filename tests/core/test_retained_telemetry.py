import json
import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from rio_bot.core.events import EventLogger
from rio_bot.core.retained_telemetry import RetainedTelemetry
from rio_bot.tooling.telemetry_retention import main

NOW = datetime(2026, 10, 22, tzinfo=UTC)


def event(at=NOW, **extra):
    return {"at": at.isoformat(), "event": "turn.completed", "turn_id": str(uuid4()),
            "status": "ok", "routing": {"web": False, "tier": "primary", "secret": "private"},
            "tokens": {"input": 10, "output": 3, "total": 13, "secret": "private"}, **extra}


def usage(at=NOW, **extra):
    return {"at": at.isoformat(), "operation": "answer", "provider": "gemini",
            "model": "test", "total_tokens": 13, "elapsed_ms": 17, "status": "completed", **extra}


def test_store_allowlists_trace_fields_and_drops_usage_identifiers(tmp_path):
    path = tmp_path / "telemetry.sqlite3"
    store = RetainedTelemetry(str(path))
    row = event(content="private", scope="private", api_key="private")
    store.event(row, now=NOW)
    store.usage(usage(turn_id=row["turn_id"], scope="private", provider_error_message="private"), now=NOW)
    with sqlite3.connect(path) as conn:
        traces = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM traces")]
        facts = conn.execute("SELECT * FROM usage").fetchall()
        columns = [r[1] for r in conn.execute("PRAGMA table_info(usage)")]
    assert {r["event"] for r in traces} == {"turn.completed", "turn.usage"}
    assert "private" not in json.dumps(traces)
    assert "turn_id" not in columns and "scope" not in columns
    assert facts[0][-2:] == (13, 17)
    assert b"private" not in path.read_bytes()


def test_trace_30_usage_90_exact_boundaries_future_and_old_rejected(tmp_path):
    path = tmp_path / "telemetry.sqlite3"
    store = RetainedTelemetry(str(path))
    for age in (0, 30, 31, -1):
        store.event(event(NOW - timedelta(days=age)), now=NOW)
    for age in (0, 90, 91, -1):
        store.usage(usage(NOW - timedelta(days=age)), now=NOW)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0] == 2


def test_preview_is_read_only_and_apply_only_expires_telemetry(tmp_path):
    path = tmp_path / "telemetry.sqlite3"
    store = RetainedTelemetry(str(path))
    store.event(event(), now=NOW)
    store.usage(usage(), now=NOW)
    existing_log = tmp_path / "events.jsonl"
    existing_log.write_text("existing")
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    report = store.maintenance(now=NOW + timedelta(days=31))
    assert report == {"apply": False, "expired": {"traces": 1, "usage": 0}}
    assert before == (path.read_bytes(), path.stat().st_mtime_ns)
    store.maintenance(apply=True, now=NOW + timedelta(days=31))
    assert store.maintenance(now=NOW + timedelta(days=91))["expired"] == {"traces": 0, "usage": 1}
    store.maintenance(apply=True, now=NOW + timedelta(days=91))
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0] == 0
    assert existing_log.read_text() == "existing"


def test_other_database_and_symlink_are_never_modified(tmp_path):
    path = tmp_path / "rio.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE memory (content TEXT)")
        conn.execute("INSERT INTO memory VALUES ('private')")
    before = path.read_bytes()
    store = RetainedTelemetry(str(path))
    store.event(event(), now=NOW)  # Refused without breaking the response path.
    with pytest.raises(ValueError, match="Unsupported"):
        store.maintenance(apply=True, now=NOW)
    link = tmp_path / "link.sqlite3"
    link.symlink_to(path)
    RetainedTelemetry(str(link)).event(event(), now=NOW)
    with pytest.raises(ValueError):
        RetainedTelemetry(str(link)).maintenance(apply=True)
    assert path.read_bytes() == before


def test_failed_retention_does_not_break_existing_event_log(tmp_path, caplog):
    target = tmp_path / "not-a-directory"
    target.write_text("private")
    log = EventLogger(str(tmp_path / "events.jsonl"), telemetry_path=str(target / "private"))
    log.emit("turn.failed", turn_id=str(uuid4()), status="error")
    log.close()
    assert json.loads((tmp_path / "events.jsonl").read_text())["event"] == "turn.failed"
    assert "Retained telemetry write failed" in caplog.text
    assert "private" not in caplog.text


def test_disabled_invalid_and_non_answer_rows_do_not_create_files(tmp_path):
    path = tmp_path / "telemetry.sqlite3"
    store = RetainedTelemetry(str(path))
    store.event({"event": "discord_message_received", "content": "private"})
    store.usage(usage(operation="summarize"))
    store.usage({"operation": "answer", "at": "invalid"})
    assert not path.exists()
    assert main(["--db", str(path)]) == 1
    assert not path.exists()


def test_trace_idempotence_and_restart_preserve_retention(tmp_path):
    path = tmp_path / "telemetry.sqlite3"
    row = event()
    RetainedTelemetry(str(path)).event(row, now=NOW)
    RetainedTelemetry(str(path)).event(row, now=NOW)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0] == 1
    assert RetainedTelemetry(str(path)).maintenance(now=NOW)["expired"]["traces"] == 0


@pytest.mark.asyncio
async def test_usage_logger_retains_failures_without_changing_provider_exception(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from rio_bot.ai.usage import UsageLogger

    path = tmp_path / "telemetry.sqlite3"
    logger = UsageLogger(str(tmp_path / "usage.jsonl"), telemetry_path=str(path))
    failure = ValueError("private-provider-body")
    client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(side_effect=failure)))
    turn_id = str(uuid4())
    with logger.turn(turn_id), pytest.raises(ValueError) as caught:
        await logger.request(client, "answer", model="synthetic", input="private",
                             _rio_telemetry={"provider": "gemini"})
    logger.close()
    assert caught.value is failure
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT status FROM usage").fetchone()[0] == "error"
        trace = json.loads(conn.execute("SELECT payload FROM traces").fetchone()[0])
    assert trace["turn_id"] == turn_id and trace["event"] == "turn.usage"
    assert "private" not in json.dumps(trace)
    assert b"private" not in path.read_bytes()
