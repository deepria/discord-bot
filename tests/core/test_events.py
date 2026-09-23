import json

from rio_bot.core.events import EVENT_SCHEMA_VERSION, EventLogger, turn_event_fields


def test_event_logger_adds_schema_version_and_filters_unsafe_values(tmp_path):
    path = tmp_path / "events.jsonl"
    logger = EventLogger(str(path))
    logger.emit(
        "turn.completed",
        turn_id="9f5f7fad-70d1-4e69-8b2b-5d7b5ebf6978",
        routing={"web": False, "tier": "fixed"},
        unsafe=object(),
    )
    logger.close()

    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["schema_version"] == EVENT_SCHEMA_VERSION
    assert row["event"] == "turn.completed"
    assert row["routing"] == {"web": False, "tier": "fixed"}
    assert "unsafe" not in row


def test_turn_event_schema_is_complete_and_content_free():
    fields = turn_event_fields(
        "9f5f7fad-70d1-4e69-8b2b-5d7b5ebf6978",
        {
            "answer": {
                "provider": "gemini",
                "model": "gemini-test",
                "model_tier": "fixed",
                "web_search_used": False,
                "web_search_calls": 0,
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "input": "secret user message",
            }
        },
        status="ok",
        latency_ms=42,
        memory_lifecycle="written",
    )

    assert fields == {
        "turn_id": "9f5f7fad-70d1-4e69-8b2b-5d7b5ebf6978",
        "operation": "answer",
        "status": "ok",
        "provider": "gemini",
        "model": "gemini-test",
        "routing": {"web": False, "tier": "fixed"},
        "latency_ms": 42,
        "tokens": {"input": 10, "output": 5, "total": 15},
        "web_search_calls": 0,
        "memory_lifecycle": "written",
        "error_type": None,
    }
    assert "secret" not in json.dumps(fields)
