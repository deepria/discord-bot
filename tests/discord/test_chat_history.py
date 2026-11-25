from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from rio_bot.core.store import Store
from rio_bot.discord.chat_history import ChatHistory


def fixture_history(*, dm=True, store=None):
    store = store or Store(":memory:")
    now = datetime.now(UTC) + timedelta(seconds=30)
    author = NS(id=100, display_name="작성자", bot=False)
    other = NS(id=200, display_name="다른 사람", bot=False)
    bot = NS(id=300, bot=True)
    access = NS(view_channel=True, read_message_history=True, manage_threads=False)
    channel = NS(
        id=10, recipient=author, permissions_for=Mock(return_value=access),
        type=discord.ChannelType.private if dm else discord.ChannelType.text,
    )
    guild = None if dm else NS(
        id=20, me=bot, unavailable=False, get_channel_or_thread=Mock(return_value=channel),
        fetch_member=AsyncMock(return_value=author),
    )

    def message(text, *, who=author, seconds=-1):
        message_id = discord.utils.time_snowflake(now + timedelta(seconds=seconds))
        at = discord.utils.snowflake_time(message_id)
        return NS(
            id=message_id, channel=channel, guild=guild,
            author=who, created_at=at, content=text, mentions=[], webhook_id=None,
        )

    current = message("예전 결정 알려줘", seconds=0)
    messages = []

    async def iterate(*, limit, before, after, oldest_first):
        assert not oldest_first
        for old in sorted(messages, key=lambda row: row.id, reverse=True)[:limit]:
            if (after is None or after.id < old.id) and old.id < before.id:
                yield old

    channel.history = Mock(side_effect=iterate)
    settings = NS(external_context_policy="full", dm_always_reply=False, call_prefixes=("리오야",))
    lookup = ChatHistory(current, bot.id, store, settings, Mock())
    return lookup, message, messages, access, other


@pytest.mark.asyncio
async def test_time_author_scope_and_partial_pagination():
    lookup, message, rows, _, other = fixture_history(dm=False)
    rows.extend(message(f"결정 {i}", seconds=-i) for i in range(1, 14))
    rows.append(message("결정 다른 사람", seconds=-14, who=other))
    rows.append(message("미래 결정", seconds=1))
    start = message("", seconds=-14).created_at
    end = message("", seconds=-1).created_at
    filters = {
        "start_at": start.isoformat(), "end_at": end.isoformat(),
        "query": "결정", "author_user_ids": ["100"],
    }
    first = await lookup.run(filters)
    assert first["status"] == "partial"
    assert [row["content"] for row in first["messages"]] == [f"결정 {i}" for i in range(2, 12)]
    cursor = first["next_cursor"]
    assert await lookup.run({"cursor": cursor, "query": "another"}) == {"status": "invalid_arguments"}
    second = await lookup.run({**filters, "cursor": cursor})
    assert [row["content"] for row in second["messages"]] == ["결정 12", "결정 13"]
    assert not second["has_more"]
    assert await lookup.run({"channel_id": "42"}) == {"status": "invalid_arguments"}


@pytest.mark.asyncio
async def test_preexisting_reset_and_deleted_discord_message(tmp_path):
    path = str(tmp_path / "rio.db")
    lookup, message, rows, _, _ = fixture_history(store=Store(path))
    old = message("retained", seconds=-60)
    deleted = message("deleted", seconds=-61)
    lookup.store.add(lookup.scope, old.id, old.content, "")
    lookup.store.add(lookup.scope, deleted.id, deleted.content, "")
    rows.extend([old, message("no local proof", seconds=-62)])
    before = await lookup.run({})
    assert [record["content"] for record in before["messages"]] == ["retained"]

    lookup.store.forget(lookup.scope)
    assert not await lookup.validate()
    lookup.store.db.close()
    reopened, _, new_rows, _, _ = fixture_history(store=Store(path))
    new_rows.extend(rows)
    assert (await reopened.run({}))["status"] == "empty"


@pytest.mark.asyncio
@pytest.mark.parametrize("purge", ["forget", "purge_channel_memory", "purge_realm_memory", "purge_all_memory"])
async def test_post_install_messages_remain_excluded_after_reset(purge, monkeypatch):
    lookup, message, rows, _, _ = fixture_history(dm=False)
    old = message("post-install history")
    rows.append(old)
    monkeypatch.setattr("rio_bot.core.store.datetime", NS(now=lambda tz: lookup.message.created_at))
    reset = getattr(lookup.store, purge)
    reset() if purge == "purge_all_memory" else reset(lookup.scope)
    assert (await lookup.run({}))["status"] == "empty"


@pytest.mark.asyncio
async def test_dm_recipient_and_guild_private_thread_access():
    lookup, message, rows, _, other = fixture_history()
    rows.append(message("wrong author", who=other))
    assert (await lookup.run({}))["status"] == "empty"
    lookup.message.channel.history.reset_mock()
    lookup.message.channel.recipient = other
    assert (await lookup.run({}))["status"] == "permission_denied"
    lookup.message.channel.history.assert_not_called()

    lookup, message, rows, _, _ = fixture_history(dm=False)
    channel = lookup.message.channel
    channel.type = discord.ChannelType.private_thread
    channel.fetch_members = AsyncMock(side_effect=[
        [NS(id=100), NS(id=300)], [NS(id=300)],
    ])
    rows.append(message("private"))
    assert await lookup.run({}) == {"status": "permission_denied"}
    assert not lookup.used


@pytest.mark.asyncio
async def test_privacy_policy_disallows_non_invocations_and_stops_on_policy_change():
    lookup, message, rows, _, _ = fixture_history(dm=False)
    lookup.settings.external_context_policy = "bot_interactions_only"
    rows.extend([message("private chatter", seconds=-2), message("리오야 이전 결정")])
    result = await lookup.run({})
    assert [row["content"] for row in result["messages"]] == ["리오야 이전 결정"]
    lookup.store.set_chat_log_mode_override(lookup.scope.channel, "off")
    assert not await lookup.validate()
    assert (await lookup.run({}))["status"] == "history_disabled"


@pytest.mark.asyncio
async def test_discord_error_returns_content_free_status():
    lookup, _, _, _, _ = fixture_history()
    response = NS(status=403, reason="Forbidden")
    lookup.message.channel.history.side_effect = discord.Forbidden(response, "private text")
    assert await lookup.run({}) == {"status": "source_unavailable"}
