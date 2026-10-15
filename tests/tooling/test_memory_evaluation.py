import json
import sqlite3
from datetime import UTC, datetime

import pytest

from rio_bot.core.routing import Scope
from rio_bot.core.store import Store
from rio_bot.tooling.memory_evaluation import evaluate_shadow_memory, main

NOW = datetime(2026, 10, 15, tzinfo=UTC)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "shadow ?# metadata.sqlite3"
    scope = Scope(1, 10, 20, public_at_capture=True)
    store = Store(str(path))
    for message_id in (101, 102):
        store.add(scope, message_id, "private source", "reply", turn_id=f"turn-{message_id}")
    store.save_structured_memory(scope, through_id=2, items=[{
        "kind": "preference", "content": "간결한 답변을 선호한다.", "disclosure": "channel",
        "source_message_ids": [str(message_id)], "confidence": 0.8,
    } for message_id in (101, 102)])
    store.db.execute("UPDATE structured_memory_items SET created_at='2026-10-01 00:00:00'")
    store.db.commit()
    store.close()
    return path


def report(database, **kwargs):
    return evaluate_shadow_memory(str(database), min_items=2, now=NOW, **kwargs)


def update(database, sql):
    connection = sqlite3.connect(database)
    with connection:
        connection.execute(sql)
    connection.close()


def test_readiness_uses_real_store_schema_without_reading_content(database, monkeypatch):
    before = database.read_bytes()
    original_connect = sqlite3.connect

    def readonly_connect(database_uri, **kwargs):
        assert database_uri.endswith("?mode=ro")
        connection = original_connect(database_uri, **kwargs)

        def authorize(action, table, column, *_):
            if action == sqlite3.SQLITE_READ:
                assert column not in {"content", "owner_id", "origin_channel_id"}
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)
        return connection

    monkeypatch.setattr(sqlite3, "connect", readonly_connect)
    result = report(database)
    assert result["source_status"] == "HEALTHY"
    assert result["items"] == {
        "total": 2, "active": 2, "superseded": 0, "deleted": 0, "other_status": 0}
    assert result["provenance"] == {"active_message_linked": 2, "active_turn_linked": 2}
    assert result["observation"]["elapsed_days"] == 14
    assert result["readiness"]["ready_for_metadata_review"] is True
    serialized = json.dumps(result)
    for private in ["private source", "간결한", "turn-101", "101", str(database)]:
        assert private not in serialized
    assert database.read_bytes() == before


@pytest.mark.parametrize("state", ["deleted", "superseded"])
def test_inactive_items_do_not_satisfy_sample_threshold(database, state):
    update(database, f"UPDATE structured_memory_items SET status='{state}' WHERE id=1")
    result = report(database)
    assert result["items"]["total"] == 2
    assert result["readiness"]["reasons"] == ["insufficient_active_items"]
    assert result["readiness"]["ready_for_metadata_review"] is False


@pytest.mark.parametrize("messages,turns", [("", ""), ("101,102", "turn-101,"),
                                          ("101,102", "turn-101"), ("101, ", "one,two")])
def test_empty_partial_and_mismatched_source_links_block_readiness(database, messages, turns):
    connection = sqlite3.connect(database)
    with connection:
        connection.execute("UPDATE structured_memory_items SET source_message_ids=?, "
                           "source_turn_ids=? WHERE id=1", (messages, turns))
    connection.close()
    result = report(database)
    assert "missing_turn_provenance" in result["readiness"]["reasons"]
    assert result["readiness"]["ready_for_metadata_review"] is False


@pytest.mark.parametrize("value,reason", [
    ("2026-10-14 00:00:00", "insufficient_observation_days"),
    ("invalid", "invalid_item_timestamp"),
    ("2027-01-01 00:00:00", "invalid_item_timestamp"),
])
def test_observation_window_and_bad_dates(database, value, reason):
    update(database, f"UPDATE structured_memory_items SET created_at='{value}'")
    assert reason in report(database)["readiness"]["reasons"]


def test_unknown_lifecycle_blocks_review(database):
    update(database, "UPDATE structured_memory_items SET status='unexpected' WHERE id=1")
    assert "unknown_lifecycle_status" in report(database)["readiness"]["reasons"]


def test_missing_incomplete_or_corrupt_database_is_never_created_or_migrated(tmp_path):
    database = tmp_path / "missing.sqlite3"
    assert report(database)["readiness"]["reasons"] == ["database_unavailable"]
    assert not database.exists()
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE structured_memory_items (id INTEGER)")
    connection.close()
    before = database.read_bytes()
    assert report(database)["readiness"]["reasons"] == ["shadow_schema_unavailable"]
    assert database.read_bytes() == before
    database.write_bytes(b"not sqlite")
    result = report(database)
    assert result["source_status"] == "UNAVAILABLE"
    assert result["readiness"]["reasons"] == ["database_read_failed"]


@pytest.mark.parametrize("kwargs", [{"min_items": 0}, {"min_days": 0}, {"now": datetime(2026, 10, 15)}])
def test_invalid_evaluation_parameters(tmp_path, kwargs):
    with pytest.raises(ValueError):
        evaluate_shadow_memory(str(tmp_path / "absent"), **kwargs)


def test_cli_returns_json_without_loading_runtime_settings(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["rio-memory-eval", "--db", str(tmp_path / "absent")])
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["source_status"] == "UNAVAILABLE"
    assert result["readiness"]["ready_for_metadata_review"] is False
