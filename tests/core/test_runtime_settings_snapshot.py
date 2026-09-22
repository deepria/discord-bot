import sqlite3
from pathlib import Path

from rio_bot.core.config import Settings
from rio_bot.core.runtime_config import RuntimeSettings
from rio_bot.core.runtime_settings_snapshot import runtime_settings_snapshot
from rio_bot.core.store import Store


def _base(database_path: str) -> Settings:
    return Settings(api_key="key", discord_token="token", db_path=database_path)


def test_snapshot_reports_startup_values_without_creating_a_database(tmp_path: Path):
    database_path = tmp_path / "missing.sqlite3"

    rows = runtime_settings_snapshot(_base(str(database_path)))

    assert not database_path.exists()
    assert next(row for row in rows if row["key"] == "chat_web_search") == {
        "key": "chat_web_search",
        "env_name": "CHAT_WEB_SEARCH",
        "value": True,
        "display_value": "on",
        "source": "startup",
        "kind": "bool",
        "minimum": None,
        "maximum": None,
        "empty_allowed": False,
    }


def test_snapshot_reads_valid_overrides_without_modifying_the_database(tmp_path: Path):
    database_path = tmp_path / "runtime.sqlite3"
    store = Store(str(database_path))
    try:
        runtime = RuntimeSettings(_base(str(database_path)), store)
        runtime.set_text("CHANNEL_CONTEXT_CHARS", "8000")
    finally:
        store.close()

    before = database_path.stat().st_mtime_ns
    rows = runtime_settings_snapshot(_base(str(database_path)))
    after = database_path.stat().st_mtime_ns

    context = next(row for row in rows if row["key"] == "channel_context_chars")
    assert context["value"] == 8000
    assert context["source"] == "db"
    assert before == after


def test_snapshot_ignores_malformed_overrides(tmp_path: Path):
    database_path = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE runtime_config (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO runtime_config(key, value) VALUES (?, ?)",
            ("chat_web_search", '"not-a-boolean"'),
        )

    rows = runtime_settings_snapshot(_base(str(database_path)))

    value = next(row for row in rows if row["key"] == "chat_web_search")
    assert value["value"] is True
    assert value["source"] == "startup"
