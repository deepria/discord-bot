import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from rio_bot.ai.runtime_llm import LLM
from rio_bot.core.config import Settings
from rio_bot.core.routing import Scope
from rio_bot.core.runtime_config import RuntimeSettings
from rio_bot.core.store import Store
from rio_bot.discord import web_bot

OWNER = 100
SOURCE = Scope(1, 10, OWNER, True)
DM = Scope(None, 20, OWNER)


def fake_channel(channel_type, channel_id):
    channel = MagicMock(spec=channel_type)
    channel.id = channel_id
    channel.type = discord.ChannelType.private if channel_type is discord.DMChannel else (
        discord.ChannelType.text
    )
    channel.typing.return_value.__aenter__ = AsyncMock()
    channel.typing.return_value.__aexit__ = AsyncMock()
    channel.send = AsyncMock(return_value=NS(id=999))
    return channel


@pytest.fixture
async def rio(tmp_path):
    store = Store(":memory:")
    usage_path = tmp_path / "usage.jsonl"
    settings = RuntimeSettings(Settings(
        "test", "test", db_path=str(tmp_path / "rio.sqlite3"),
        dm_always_reply=True, cooldown=0, summary_every=100,
        usage_log_path=str(usage_path), event_log_path="", telemetry_db_path="",
    ), store)
    create = AsyncMock(return_value=NS(status="completed", output_text="이어갈 수 있어.", output=[]))
    client = NS(responses=NS(create=create), close=AsyncMock())
    llm = LLM(settings, client=client)
    llm.auto_reply_is_for_other = AsyncMock(return_value=False)
    bot = web_bot.RioClient(settings, store=store, llm=llm)
    bot._connection.user = NS(id=99)
    original = fake_channel(discord.TextChannel, SOURCE.channel_id)
    original.permissions_for.return_value = NS(view_channel=True, read_message_history=True)
    original.fetch_message = AsyncMock(return_value=NS(author=NS(id=OWNER)))
    member = NS(id=OWNER)
    guild = NS(id=1, default_role=NS(), unavailable=False,
               get_channel=MagicMock(return_value=original), fetch_member=AsyncMock(return_value=member))
    bot.get_guild = MagicMock(return_value=guild)
    dm = fake_channel(discord.DMChannel, DM.channel_id)

    def msg(message_id, content, *, in_guild=False):
        return NS(id=message_id, content=content, type=discord.MessageType.default,
                  guild=guild if in_guild else None, channel=original if in_guild else dm,
                  author=NS(id=OWNER, bot=False, display_name="사용자"),
                  mentions=[], attachments=[], stickers=[], reference=None, webhook_id=None)

    def payload():
        data = create.await_args.kwargs
        return json.loads(data["input"][0]["content"].split("\n", 1)[1]), data

    yield NS(bot=bot, store=store, settings=settings, guild=guild, original=original, dm=dm,
             msg=msg, payload=payload, create=create, usage_path=usage_path)
    await bot.close()


def add_turn(store, source, message_id, user="내일 이어서 얘기하자", reply="찾아봤어"):
    store.add(source, message_id, user, reply)
    store.add_shared_call(source, message_id, "사용자", user)


async def test_public_turn_flows_through_discord_to_one_dm_answer_and_can_be_disabled(rio):
    await rio.bot.on_message(rio.msg(1001, "리오야 증미역 근처 점심 얘기는 내일 이어서 하자", in_guild=True))
    assert rio.store.seen(1001)

    await rio.bot.on_message(rio.msg(1002, "그 얘기 마저 하자"))
    assert "public_dm_recent_conversation" not in rio.payload()[0]
    rio.guild.fetch_member.assert_not_awaited()

    assert rio.settings.set_text("PUBLIC_SERVER_MEMORY_IN_DM", "true") is True
    await rio.bot.on_message(rio.msg(1003, "그 얘기 마저 하자"))
    reference, request = rio.payload()
    [turn] = reference["public_dm_recent_conversation"]
    assert turn["source"] == SOURCE.conversation
    assert turn["message_id"] == "1001"
    assert "증미역" in turn["user"]
    assert turn["rio"] == "이어갈 수 있어."
    assert request["input"][-1]["content"] == "그 얘기 마저 하자"
    assert "현재 DM의 호칭과 관계" in request["instructions"]
    assert "검색이나 확인을 실행했다고 판단하지 마세요" in request["instructions"]

    with pytest.MonkeyPatch.context() as patch:
        plan = MagicMock(wraps=rio.bot.llm.build_information_plan)
        patch.setattr(rio.bot.llm, "build_information_plan", plan)
        await rio.bot.on_message(rio.msg(1004, "그럼 거기 근처 식당 영업시간 찾아줘"))
        assert "증미역" in plan.call_args.args[0].routing_query
    assert rio.payload()[1]["tools"]
    rows = [json.loads(line) for line in rio.usage_path.read_text().splitlines()]
    assert rows[-1]["web_search_calls"] == 0  # Tool availability alone proves no search.
    assert rows[-1]["public_dm_sources"] == 1
    assert rows[-1]["public_dm_estimated_input_tokens"] > 0
    assert "증미역" not in rio.usage_path.read_text()

    rio.settings.set_text("PUBLIC_SERVER_MEMORY_IN_DM", "false")
    await rio.bot.on_message(rio.msg(1005, "그 얘기 마저 하자"))
    assert "public_dm_recent_conversation" not in rio.payload()[0]
    assert [row["message_id"] for row in rio.store.history(DM)] == ["1002", "1003", "1004", "1005"]
    assert rio.create.await_count == 5
    assert rio.dm.send.await_count == 4
    rio.bot.llm.auto_reply_is_for_other.assert_not_awaited()


@pytest.mark.parametrize("case", [
    "other_owner", "private_at_capture", "private_now", "not_member", "cannot_view",
    "cannot_read_history", "chat_log_off", "dm_read_off", "expired", "store_deleted",
    "discord_deleted", "permission_error", "disallowed_guild", "policy_changed_during_lookup",
    "deleted_during_lookup", "store_error", "group_dm",
])
async def test_excluded_sources_leave_the_dm_answer_available(rio, case):
    source = SOURCE
    if case == "other_owner":
        source = Scope(1, 10, 200, True)
    if case == "private_at_capture":
        source = Scope(1, 10, OWNER)
    add_turn(rio.store, source, 1001)
    rio.settings.set_text("PUBLIC_SERVER_MEMORY_IN_DM", "true")

    http_response = NS(status=403, reason="Forbidden")
    if case == "private_now":
        rio.original.permissions_for.return_value.view_channel = False
    if case == "not_member":
        rio.guild.fetch_member.side_effect = discord.NotFound(
            NS(status=404, reason="Not Found"), ""
        )
    if case in {"cannot_view", "cannot_read_history"}:
        rio.original.permissions_for.side_effect = lambda holder: NS(
            view_channel=holder is rio.guild.default_role or case != "cannot_view",
            read_message_history=holder is rio.guild.default_role or case != "cannot_read_history",
        )
    if case == "chat_log_off":
        rio.store.set_chat_log_mode_override(SOURCE.channel, "off")
    if case == "dm_read_off":
        rio.store.set_memory_mode(DM, "write_only")
    if case == "expired":
        with rio.store.db:
            rio.store.db.execute("UPDATE shared_calls SET created_at=datetime('now', '-61 minutes')")
    if case == "store_deleted":
        rio.store.forget(SOURCE)
    if case == "discord_deleted":
        rio.original.fetch_message.side_effect = discord.NotFound(
            NS(status=404, reason="Not Found"), ""
        )
    if case == "permission_error":
        rio.guild.fetch_member.side_effect = discord.Forbidden(http_response, "")
    if case == "disallowed_guild":
        rio.bot.settings = NS(public_memory_in_dm=True, allowed_guild_ids={2})
        result = await rio.bot.public_dm_context(DM, set(), explicit_summary=False)
        assert not result.recent
        assert result.telemetry["public_dm_excluded"] == {"guild_not_allowed": 1}
        rio.bot.settings = rio.settings
        return
    if case == "policy_changed_during_lookup":
        async def disable(_):
            rio.settings.set_text("PUBLIC_SERVER_MEMORY_IN_DM", "false")
            return NS(author=NS(id=OWNER))
        rio.original.fetch_message.side_effect = disable
    if case == "deleted_during_lookup":
        async def forget(_):
            rio.store.forget(SOURCE)
            return NS(author=NS(id=OWNER))
        rio.original.fetch_message.side_effect = forget
    if case == "store_error":
        rio.store.recent_public_sources = MagicMock(side_effect=RuntimeError("lookup failed"))
    if case == "group_dm":
        rio.dm.type = discord.ChannelType.group

    await rio.bot.on_message(rio.msg(1002, "그 얘기 마저 하자"))
    reference, _ = rio.payload()
    assert "public_dm_recent_conversation" not in reference
    assert reference["public_server_context"] == []
    rio.dm.send.assert_awaited_once()
    rio.create.assert_awaited_once()


async def test_source_and_turn_limits_budget_duplicates_and_reply_exportability(rio):
    rio.settings.set_text("PUBLIC_SERVER_MEMORY_IN_DM", "true")
    for channel_id in range(10, 16):
        source = Scope(1, channel_id, OWNER, True)
        for index in range(3):
            add_turn(rio.store, source, channel_id * 10 + index, "x" * 396, "y" * 104)
    with rio.store.db:
        rio.store.db.execute("UPDATE turns SET exportable=0 WHERE message_id='151'")
        rio.store.db.execute("DELETE FROM turns WHERE message_id='141'")
        rio.store.db.execute("UPDATE shared_calls SET content=? WHERE message_id='132'", ("z" * 4001,))

    result = await rio.bot.public_dm_context(DM, {"152"}, explicit_summary=False)
    assert [row["message_id"] for row in result.recent] == ["121", "122", "131", "141", "142", "151"]
    assert result.telemetry["public_dm_sources"] == 4
    assert result.telemetry["public_dm_excluded"] == {"duplicate": 1, "character_budget": 1}
    assert all("rio" not in row for row in result.recent if row["message_id"] in {"141", "151"})
    assert sum(len(row["user"]) + len(row.get("rio", "")) for row in result.recent) <= 4000


async def test_old_summary_keeps_its_explicit_lookup_path(rio):
    rio.settings.set_text("PUBLIC_SERVER_MEMORY_IN_DM", "true")
    add_turn(rio.store, SOURCE, 1001, "old call text")
    rio.store.save_shared_summary(SOURCE, "사용자", "예전 공개 요약", 1)
    with rio.store.db:
        old = datetime.now(UTC) - timedelta(hours=2)
        rio.store.db.execute("UPDATE shared_calls SET created_at=?", (old.strftime("%F %T"),))

    await rio.bot.on_message(rio.msg(1002, "그 얘기 마저 하자"))
    assert rio.payload()[0]["public_server_context"] == []
    await rio.bot.on_message(rio.msg(1003, "저번에 서버에서 얘기한 거 기억나?"))
    reference, _ = rio.payload()
    assert "public_dm_recent_conversation" not in reference
    assert reference["public_server_context"][0]["summary"] == "예전 공개 요약"
    assert "old call text" not in str(reference)


async def test_location_survives_a_generic_public_turn_and_search_uses_current_result(rio, monkeypatch):
    rio.settings.set_text("PUBLIC_SERVER_MEMORY_IN_DM", "true")
    add_turn(rio.store, SOURCE, 1001, "증미역 근처 점심 식당을 알아보자", "좋아")
    add_turn(rio.store, SOURCE, 1002, "내일 이어서 얘기하자", "그래")
    plan = MagicMock(wraps=rio.bot.llm.build_information_plan)
    monkeypatch.setattr(rio.bot.llm, "build_information_plan", plan)
    rio.create.return_value.output = [NS(type="web_search_call")]

    await rio.bot.on_message(rio.msg(1003, "그럼 거기 근처 식당 영업시간 찾아줘"))
    assert "증미역" in plan.call_args.args[0].routing_query
    assert rio.payload()[1]["tools"]
    row = json.loads(rio.usage_path.read_text().splitlines()[-1])
    assert row["web_search_calls"] == 1
    rio.create.assert_awaited_once()


async def test_discord_lookup_timeout_falls_back_to_the_dm(rio, monkeypatch):
    rio.settings.set_text("PUBLIC_SERVER_MEMORY_IN_DM", "true")
    add_turn(rio.store, SOURCE, 1001)
    monkeypatch.setattr(web_bot, "_PUBLIC_DM_TIMEOUT", 0)

    async def suspend(_):
        await asyncio.sleep(0)
        return NS(id=OWNER)

    rio.guild.fetch_member.side_effect = suspend
    await rio.bot.on_message(rio.msg(1002, "그 얘기 마저 하자"))
    assert "public_dm_recent_conversation" not in rio.payload()[0]
    row = json.loads(rio.usage_path.read_text().splitlines()[-1])
    assert row["public_dm_excluded"] == {"timeout": 1}
