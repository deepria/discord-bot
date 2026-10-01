import json
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import discord
import pytest

from rio_bot.core.presence import AUTO, PresenceError, presence_snapshot, validate_presence
from rio_bot.core.store import Store
from rio_bot.discord.presence import PresenceController


@pytest.fixture
def controller(tmp_path):
    store = Store(str(tmp_path / "rio.sqlite3"))
    client = SimpleNamespace(store=store, stopping=False, change_presence=AsyncMock())
    controller = PresenceController(client, interval=0)
    controller.connection_changed(True)
    yield controller
    store.close()


def manual(**kwargs):
    return {**AUTO, "mode": "manual", "activity_text": "코드 수정 중", **kwargs}


@pytest.mark.parametrize("status", ["online", "idle", "dnd", "invisible"])
@pytest.mark.parametrize("activity_type", ["playing", "watching", "listening"])
async def test_applies_status_activity_and_commits_only_after_send(controller, status, activity_type):
    await controller.tick()
    candidate = manual(status=status, activity_type=activity_type)
    request_id = str(uuid4())
    controller.store.submit(candidate, "123", request_id)
    assert controller.store.snapshot()["configured"] == AUTO
    await controller.tick()
    result = controller.store.snapshot(request_id)
    assert result["configured"] == candidate
    assert result["operation"]["state"] == "success"
    call = controller.client.change_presence.call_args.kwargs
    assert call["status"] == getattr(discord.Status, status)
    assert call["activity"].type == getattr(discord.ActivityType, activity_type)
    assert call["activity"].name == candidate["activity_text"]
    assert "코드 수정 중" not in json.dumps(result["audit"], ensure_ascii=False)
    assert controller.store.db.execute(
        "SELECT payload FROM bot_presence_requests WHERE request_id=?", (request_id,)
    ).fetchone()[0] is None


@pytest.mark.parametrize("changes", [
    {"status": "offline"}, {"activity_type": "streaming"}, {"mode": "random"},
    {"activity_text": "   "}, {"activity_text": "a" * 129},
    {"activity_text": "line\nbreak"}, {"activity_text": "\x00"},
])
def test_invalid_requests_are_rejected(changes):
    with pytest.raises(PresenceError):
        validate_presence(manual(**changes))


def test_unicode_length_and_trim():
    assert validate_presence(manual(activity_text=" 🎮 " * 2))["activity_text"] == "🎮  🎮"
    assert len(validate_presence(manual(activity_text="🎮" * 128))["activity_text"]) == 128


async def test_failure_keeps_committed_configuration_and_restores(controller):
    await controller.tick()
    request_id = str(uuid4())
    controller.store.submit(manual(), "123", request_id)
    controller.client.change_presence.side_effect = RuntimeError("secret error detail")
    await controller.tick()
    result = controller.store.snapshot(request_id)
    assert result["configured"] == AUTO
    assert result["operation"]["state"] == "unknown"
    assert "secret" not in json.dumps(result)
    controller.client.change_presence.side_effect = None
    await controller.tick()
    assert controller.store.snapshot()["apply_state"] == "sent"
    assert controller.store.snapshot()["last_sent"] == AUTO


async def test_auto_keeps_last_manual_and_restart_restores(controller):
    await controller.tick()
    controller.store.submit(manual(status="dnd"), "123", str(uuid4()))
    await controller.tick()
    before = controller.store.snapshot()["configured"]
    controller.connection_changed(False)
    assert not controller.store.snapshot()["connected"]
    controller.connection_changed(True)
    await controller.tick()
    assert controller.store.snapshot()["last_sent"] == before
    controller.store.submit(dict(AUTO), "123", str(uuid4()))
    await controller.tick()
    assert controller.store.snapshot()["configured"] == AUTO
    assert controller.store.snapshot()["manual"] == before
    restarted = PresenceController(controller.client, interval=0)
    assert not restarted.store.snapshot()["connected"]
    restarted.connection_changed(True)
    await restarted.tick()
    assert restarted.store.snapshot()["last_sent"] == AUTO


async def test_manual_restart_and_unfinished_requests(controller):
    await controller.tick()
    controller.store.submit(manual(status="invisible"), "123", str(uuid4()))
    await controller.tick()
    request_id = str(uuid4())
    controller.store.submit(manual(status="idle"), "123", request_id)
    restarted = PresenceController(controller.client, interval=0)
    snapshot = restarted.store.snapshot(request_id)
    assert snapshot["configured"] == manual(status="invisible")
    assert snapshot["operation"]["state"] == "unknown"
    restarted.connection_changed(True)
    assert restarted.store.snapshot()["apply_state"] == "pending"
    await restarted.tick()
    assert restarted.client.change_presence.call_args.kwargs["status"] == discord.Status.invisible


async def test_disconnect_during_send_never_commits(controller):
    await controller.tick()
    request_id = str(uuid4())
    controller.store.submit(manual(), "123", request_id)

    async def disconnect(**kwargs):
        controller.connection_changed(False)

    controller.client.change_presence.side_effect = disconnect
    await controller.tick()
    snapshot = controller.store.snapshot(request_id)
    assert snapshot["configured"] == AUTO
    assert snapshot["operation"]["state"] == "unknown"
    assert snapshot["apply_state"] == "unavailable"


async def test_idempotency_conflict_disconnection_and_stale_heartbeat(controller):
    await controller.tick()
    request_id = str(uuid4())
    controller.store.submit(manual(), "123", request_id)
    assert controller.store.submit(manual(), "123", request_id)["operation"]["state"] == "queued"
    with pytest.raises(PresenceError) as conflict:
        controller.store.submit(manual(activity_text="다른 값"), "123", request_id)
    assert conflict.value.status_code == 409
    with pytest.raises(PresenceError):
        controller.store.submit(manual(), "456", str(uuid4()))
    await controller.tick()
    calls = controller.client.change_presence.await_count
    controller.store.submit(manual(), "123", request_id)
    await controller.tick()
    assert controller.client.change_presence.await_count == calls
    controller.connection_changed(False)
    with pytest.raises(PresenceError) as unavailable:
        controller.store.submit(manual(), "123", str(uuid4()))
    assert unavailable.value.status_code == 503
    controller.connection_changed(True)
    with controller.store.db:
        controller.store.db.execute("UPDATE bot_presence SET heartbeat=?", (time.time() - 60,))
    assert not controller.store.snapshot()["connected"]


async def test_expiration_and_rate_limit(controller):
    await controller.tick()
    controller.interval = 5
    request_id = str(uuid4())
    controller.store.submit(manual(), "123", request_id)
    with controller.store.db:
        controller.store.db.execute(
            "UPDATE bot_presence_requests SET created_at=?", (time.time() - 31,)
        )
    await controller.tick()
    assert controller.store.snapshot(request_id)["operation"]["state"] == "failure"
    controller.store.submit(manual(), "123", str(uuid4()))
    await controller.tick()
    calls = controller.client.change_presence.await_count
    controller.connection_changed(True)
    await controller.tick()
    assert controller.client.change_presence.await_count == calls


async def test_read_only_snapshot_does_not_revive_old_run(controller, tmp_path):
    path = str(tmp_path / "rio.sqlite3")
    await controller.tick()
    assert presence_snapshot(path)["configured"] == AUTO
    with sqlite3.connect(path) as db:
        db.execute("UPDATE bot_presence SET heartbeat=0")
    assert not presence_snapshot(path)["connected"]


async def test_send_then_commit_failure_compensates(controller, monkeypatch):
    await controller.tick()
    request_id = str(uuid4())
    controller.store.submit(manual(), "123", request_id)
    applied = controller.store.applied
    monkeypatch.setattr(controller.store, "applied", lambda *args: (_ for _ in ()).throw(sqlite3.OperationalError()))
    await controller.tick()
    assert controller.store.snapshot(request_id)["configured"] == AUTO
    assert controller.store.snapshot(request_id)["operation"]["state"] == "unknown"
    monkeypatch.setattr(controller.store, "applied", applied)
    await controller.tick()
    assert controller.client.change_presence.call_args.kwargs["activity"].name == "대기 중"
