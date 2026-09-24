import pytest

from rio_bot.core.routing import Scope
from rio_bot.core.store import Store


def make_item(message_id, disclosure="channel", content="간결한 답변을 선호한다."):
    return {"kind": "preference", "content": content, "disclosure": disclosure,
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
    assert row["source_turn_ids"] == ""
    assert row["status"] == "active"
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


def test_sensitive_candidates_are_not_stored_but_advance_the_shadow_cursor():
    scope = Scope(1, 10, 20, public_at_capture=True)
    store = make_store(scope)
    result = store.save_structured_memory(
        scope, through_id=2, items=[make_item(101, content="내 비밀번호는 오래된 비밀번호다.")])
    assert result == {"candidates": 1, "written": 0, "rejected": {"credential": 1}}
    assert store.structured_memory_cursor(scope) == 2
    assert store.db.execute("SELECT COUNT(*) FROM structured_memory_items").fetchone()[0] == 0


def test_turn_provenance_status_and_deletion_are_scoped_to_the_owner():
    scope = Scope(1, 10, 20, public_at_capture=True)
    other = Scope(1, 10, 21, public_at_capture=True)
    store = Store(":memory:")
    store.add(scope, 101, "첫 발화", "첫 답변", turn_id="turn-101")
    store.add(scope, 102, "둘째 발화", "둘째 답변", turn_id="turn-102")
    store.save_structured_memory(scope, through_id=2, items=[make_item(101)])
    item_id = store.db.execute("SELECT id FROM structured_memory_items").fetchone()[0]
    row = store.active_structured_memory(scope)[0]
    assert row["source_turn_ids"] == "turn-101"
    assert store.delete_structured_memory(other, item_id) is False
    assert store.delete_structured_memory(scope, item_id) is True
    assert store.active_structured_memory(scope) == []


def test_supersession_requires_an_active_replacement_owned_by_the_same_user():
    scope = Scope(1, 10, 20, public_at_capture=True)
    store = make_store(scope)
    store.save_structured_memory(scope, through_id=2, items=[make_item(101), make_item(102)])
    first, replacement = [row["id"] for row in store.active_structured_memory(scope)]
    assert store.supersede_structured_memory(scope, first, replacement) is True
    row = store.db.execute("SELECT status, superseded_by FROM structured_memory_items WHERE id=?", (first,)).fetchone()
    assert tuple(row) == ("superseded", replacement)
    assert [row["id"] for row in store.active_structured_memory(scope)] == [replacement]


def test_forget_removes_structured_items_and_cursors():
    scope = Scope(1, 10, 20, public_at_capture=True)
    store = make_store(scope)
    store.save_structured_memory(scope, through_id=2, items=[make_item(101)])
    store.forget(scope)
    assert store.db.execute("SELECT COUNT(*) FROM structured_memory_items").fetchone()[0] == 0
    assert store.db.execute("SELECT COUNT(*) FROM structured_memory_cursors").fetchone()[0] == 0
