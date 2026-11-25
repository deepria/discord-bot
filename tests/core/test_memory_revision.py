from datetime import UTC, datetime, timedelta

import pytest

from rio_bot.core.routing import Scope
from rio_bot.core.store import Store

DM = Scope(None, 10, 100)
OTHER = Scope(None, 11, 100)
FOREIGN = Scope(None, 10, 200)
PUBLIC = Scope(1, 12, 100, True)
OLD = "커피가 좋아"
NEW = "이제 커피 안 마셔"


def put(store, scope, message_id, text, reply="응.", *, linked=True):
    store.add(scope, message_id, text, reply, turn_id=f"turn-{message_id}" if linked else None)
    store.add_shared_call(scope, message_id, "synthetic", text)
    return store.db.execute("SELECT id FROM turns WHERE message_id=?", (str(message_id),)).fetchone()[0]


def claim(store, scope, through, source_id, content, *, kind="preference"):
    return store.save_structured_memory(scope, through_id=through, items=[{
        "kind": kind, "content": content, "source_message_ids": [str(source_id)],
        "confidence": 0.8, "disclosure": "channel" if scope.public_at_capture else "owner_private",
    }])


@pytest.fixture
def store():
    with_store = Store(":memory:")
    try:
        yield with_store
    finally:
        with_store.close()


def test_verified_sources_require_exact_owner_channel_capture_and_retained_pairs(store):
    first = put(store, DM, 10, OLD)
    assert claim(store, DM, first, 10, OLD)["written"] == 1
    [saved] = store.verified_structured_memory(DM)
    assert not store.verified_structured_memory(OTHER)
    assert not store.verified_structured_memory(FOREIGN)

    replacements = [
        ("source_turn_ids", ""), ("source_turn_ids", "wrong-turn"),
        ("source_message_ids", "999"), ("source_message_ids", "10,10"),
        ("origin_public_at_capture", 1), ("status", "deleted"), ("owner_type", "operator"),
    ]
    for field, value in replacements:
        with store.db:
            store.db.execute(f"UPDATE structured_memory_items SET {field}=? WHERE id=?",
                             (value, saved["id"]))
        assert not store.verified_structured_memory(DM)
        with store.db:
            store.db.execute(f"UPDATE structured_memory_items SET {field}=? WHERE id=?",
                             (saved[field], saved["id"]))
    with store.db:
        store.db.execute("DELETE FROM turns WHERE message_id='10'")
    assert not store.verified_structured_memory(DM)


@pytest.mark.parametrize(("source", "kind", "reason", "linked"), [
    ("> 커피가 좋아", "preference", "quoted_or_narrated_source", True),
    ("친구가 커피를 좋아해", "preference", "reported_speech_source", True),
    ("오늘은 기분이 나빠", "preference", "temporary_mood_source", True),
    ("그만 분석해", "preference", "temporary_conversation_boundary", True),
    ("그거 먼저 꺼내지 마", "boundary", "missing_durable_boundary", True),
    (OLD, "preference", "missing_turn_provenance", False),
])
def test_writer_rejects_ineligible_user_evidence(store, source, kind, reason, linked):
    through = put(store, DM, 10, source, linked=linked)
    result = claim(store, DM, through, 10, "synthetic item", kind=kind)
    assert result == {"candidates": 1, "written": 0, "rejected": {reason: 1}}
    assert not store.active_structured_memory(DM)
    assert store.structured_memory_cursor(DM) == through


def test_durable_boundary_has_same_dm_provenance(store):
    boundary = "앞으로 그 얘기는 먼저 꺼내지 마"
    through = put(store, DM, 10, boundary)
    assert claim(store, DM, through, 10, boundary, kind="boundary")["written"] == 1
    [row] = store.verified_structured_memory(DM)
    assert (row["origin_realm"], row["origin_channel_id"], row["source_turn_ids"]) == (
        DM.realm, str(DM.channel_id), "turn-10",
    )


def test_correction_and_withdrawal_prevent_regeneration_from_prior_turns(store):
    first = put(store, DM, 10, OLD)
    assert claim(store, DM, first, 10, OLD)["written"] == 1
    [item] = store.verified_structured_memory(DM)
    store.save_summary(DM, f"이전 요약 {OLD}", first)
    newer = put(store, DM, 11, NEW, f"아까는 {OLD}라고 했지")
    foreign = put(store, OTHER, 12, NEW)
    assert not store.correct_structured_memory(DM, item["id"], "12")
    assert not store.correct_structured_memory(OTHER, item["id"], "12")
    assert not store.delete_structured_memory(FOREIGN, item["id"])
    assert store.correct_structured_memory(DM, item["id"], "11")
    assert store.summary(DM) == (NEW, newer)
    assert store.history(DM) == store.pending(DM) == store.pending_structured_memory(DM) == []
    [replacement] = store.verified_structured_memory(DM)
    assert replacement["content"] == NEW
    assert replacement["source_message_ids"] == "11"
    assert store.history(OTHER)[0]["id"] == foreign

    assert store.delete_structured_memory(DM, replacement["id"])
    assert not store.delete_structured_memory(DM, replacement["id"])
    assert store.summary(DM) == ("", 0)
    assert not store.verified_structured_memory(DM)
    assert not store.pending(DM)
    assert not store.history(DM)
    latest = put(store, DM, 13, "오늘은 차를 마실래")
    assert [row["id"] for row in store.pending(DM)] == [latest]
    assert store.structured_memory_context(DM, before_id=latest) == []


def test_revision_filters_old_reply_and_public_projections(store):
    first = put(store, PUBLIC, 10, OLD)
    claim(store, PUBLIC, first, 10, OLD)
    [item] = store.verified_structured_memory(PUBLIC)
    store.save_shared_summary(PUBLIC, "synthetic", OLD, store.pending_shared(PUBLIC)[0]["id"])
    assert store.delete_structured_memory(PUBLIC, item["id"])
    assert store.public_candidates(100) == store.pending_shared(PUBLIC) == []
    assert store.shared_summary(PUBLIC) == ("", 0)
    assert store.public_context([PUBLIC])[0]["recent_user_messages"] == []
    assert store.recent_public_turns(PUBLIC, 100, since="2000", until="9999") == []

    current = datetime.now(UTC)
    old = {"at": (current - timedelta(minutes=1)).isoformat(), "content": OLD}
    fresh = {"at": (current + timedelta(seconds=1)).isoformat(), "content": NEW}
    assert store.context_after_memory_revision(PUBLIC, [old, fresh]) == [fresh]


def test_revision_persists_across_restart_of_an_existing_database(tmp_path):
    path = str(tmp_path / "memory.db")
    store = Store(path)
    first = put(store, DM, 10, OLD)
    assert claim(store, DM, first, 10, OLD)["written"] == 1
    # Simulate an existing database without the additive revision table.
    store.db.execute("DROP TABLE memory_revisions")
    store.close()

    store = Store(path)
    [item] = store.verified_structured_memory(DM)
    put(store, DM, 11, NEW, OLD)
    assert store.correct_structured_memory(DM, item["id"], "11")
    store.close()

    store = Store(path)
    try:
        assert store.summary(DM)[0] == NEW
        assert not store.history(DM)
        assert [row["content"] for row in store.verified_structured_memory(DM)] == [NEW]
    finally:
        store.close()


@pytest.mark.parametrize("action", ["forget", "channel", "realm", "all"])
def test_clear_and_purge_remove_receipts_cursors_and_invalidate_only_affected_turns(store, action):
    first = put(store, DM, 10, OLD)
    claim(store, DM, first, 10, OLD)
    [item] = store.verified_structured_memory(DM)
    assert store.delete_structured_memory(DM, item["id"])
    put(store, FOREIGN, 20, "다른 사용자 발화")
    own_turn, foreign_turn = store.memory_turn(DM), store.memory_turn(FOREIGN)
    operations = {
        "forget": lambda: store.forget(DM), "channel": lambda: store.purge_channel_memory(DM),
        "realm": lambda: store.purge_realm_memory(DM), "all": store.purge_all_memory,
    }
    operations[action]()
    assert not own_turn.valid
    assert foreign_turn.valid == (action != "all")
    assert store.structured_memory_cursor(DM) == 0
    assert store.db.execute("SELECT COUNT(*) FROM memory_revisions").fetchone()[0] == 0
    assert not store.active_structured_memory(DM)
    assert not store.seen(10)
