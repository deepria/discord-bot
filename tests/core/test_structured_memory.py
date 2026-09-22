import pytest

from rio_bot.core.routing import Scope
from rio_bot.core.store import Store


def make_item(message_id, disclosure="channel"):
    return {"kind": "preference", "content": "간결한 답변을 선호한다.", "disclosure": disclosure,
            "source_message_ids": [str(message_id)], "confidence": 0.8}


def make_store(scope):
    store = Store(":memory:")
    store.add(scope, 101, "첫 발화", "첫 답변")
    store.add(scope, 102, "둘째 발화", "둘째 답변")
    return store


def test_structured_memory_saves_provenance_and_independent_cursor():
    scope = Scope(1, 10, 20, public_at_capture=True)
    store = make_store(scope)
    store.save_structured_memory(scope, through_id=2, items=[make_item(101)])
    row = store.db.execute("SELECT * FROM structured_memory_items").fetchone()
    assert store.structured_memory_cursor(scope) == 2
    assert row["owner_id"] == "20"
    assert row["source_message_ids"] == "101"
    assert store.summary(scope) == ("", 0)


def test_invalid_provenance_or_disclosure_does_not_advance_cursor():
    scope = Scope(None, 10, 20)
    store = make_store(scope)
    with pytest.raises(ValueError, match="sources"):
        store.save_structured_memory(scope, through_id=2, items=[make_item(999, "owner_private")])
    with pytest.raises(ValueError, match="disclosure"):
        store.save_structured_memory(scope, through_id=2, items=[make_item(101)])
    assert store.structured_memory_cursor(scope) == 0
    assert store.db.execute("SELECT COUNT(*) FROM structured_memory_items").fetchone()[0] == 0
