import json
import sqlite3

import pytest

from rio_bot.tooling.dashboard import overview, serve


def test_dashboard_overview_uses_read_only_database(tmp_path):
    db = tmp_path / "rio.sqlite3"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE turns (id INTEGER)")
    connection.execute("INSERT INTO turns VALUES (1)")
    connection.commit()
    connection.close()
    usage = tmp_path / "usage.jsonl"
    usage.write_text(json.dumps({"status": "error"}) + "\nnot-json\n", encoding="utf-8")

    result = overview(str(db), str(usage))

    assert result == {"db_available": True, "turns": 1, "structured_memory_items": None,
                      "usage_rows": 1, "usage_errors": 1}
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1


def test_dashboard_rejects_external_bind(tmp_path):
    with pytest.raises(ValueError, match="localhost"):
        serve(str(tmp_path / "missing"), str(tmp_path / "usage"), "0.0.0.0", 8765)
