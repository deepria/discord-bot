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
from rio_bot.discord.web_bot import RioClient

DM = Scope(None, 10, 100)
OLD = "커피가 좋아"
NEW = "이제 커피 안 마셔"


def add_preference(store, scope=DM, *, message_id=1001):
    store.add(scope, message_id, OLD, "기억할게.", turn_id=f"turn-{message_id}")
    last = store.history(scope)[-1]
    store.save_summary(scope, f"예전 요약 · {OLD}", last["id"])
    store.save_structured_memory(scope, through_id=last["id"], items=[{
        "kind": "preference", "content": OLD, "source_message_ids": [str(message_id)],
        "confidence": 0.9, "disclosure": "channel" if scope.public_at_capture else "owner_private",
    }])
    return store.verified_structured_memory(scope)[0]["id"]


@pytest.fixture
async def rio(tmp_path):
    store = Store(str(tmp_path / "test.db"))
    model = AsyncMock(return_value=NS(status="completed", output_text="알겠어.", output=[]))
    provider = NS(responses=NS(create=model), close=AsyncMock())
    settings = RuntimeSettings(Settings(
        "test", "test", db_path=str(tmp_path / "test.db"), event_log_path="",
        usage_log_path="", telemetry_db_path="", cooldown=0, summary_every=2,
        chat_web_search=False,
    ), store)
    llm = LLM(settings, client=provider)
    bot = RioClient(settings, store=store, llm=llm)
    bot._connection.user = NS(id=99)
    memory = bot.tree.get_command("memory")

    def interaction(*, scope=DM, channel_type=discord.ChannelType.private, command=None):
        return NS(
            guild_id=scope.guild_id, channel_id=scope.channel_id, user=NS(id=scope.user_id),
            channel=NS(type=channel_type), command=command,
            response=NS(send_message=AsyncMock(), defer=AsyncMock(), is_done=lambda: False),
            followup=NS(send=AsyncMock()),
        )

    try:
        yield NS(bot=bot, store=store, llm=llm, model=model, memory=memory,
                 interaction=interaction)
    finally:
        await bot.close()


async def test_dm_owner_can_list_correct_and_withdraw_without_reintroducing_earlier_text(rio):
    item_id = add_preference(rio.store)
    items = rio.memory.get_command("items")
    user = rio.interaction()
    await items.callback(user)
    listing = user.response.send_message.await_args.args[0]
    assert OLD in listing and "1001" in listing
    assert user.response.send_message.await_args.kwargs["ephemeral"] is True

    for outsider in (
        rio.interaction(scope=Scope(None, 10, 200)),
        rio.interaction(scope=Scope(None, 50, 100)),
        rio.interaction(channel_type=discord.ChannelType.group),
        rio.interaction(scope=Scope(1, 10, 100)),
    ):
        await items.callback(outsider)
        assert OLD not in outsider.response.send_message.await_args.args[0]

    rio.store.add(DM, 1002, NEW, f"전에는 {OLD}라고 했지.", turn_id="turn-1002")
    correct = rio.memory.get_command("correct")
    await correct.callback(rio.interaction(), item_id, "1002", False)
    assert OLD in rio.store.summary(DM)[0]
    wrong_user = rio.interaction(scope=Scope(None, 10, 200))
    await correct.callback(wrong_user, item_id, "1002", True)
    assert OLD in rio.store.summary(DM)[0]
    await correct.callback(user, item_id, "1002", True)
    assert "정정했어요" in user.followup.send.await_args.args[0]

    now = datetime.now(UTC)
    await rio.llm.answer(rio.store, DM, "시험 계정", "내 음료 취향이 어땠지?", channel_context=[{
        "role": "assistant", "content": OLD, "context_kind": "replied_message",
        "at": (now - timedelta(minutes=1)).isoformat(),
    }])
    payload = rio.model.await_args.kwargs["input"][0]["content"]
    context = json.loads(payload.split("\n", 1)[1])
    assert OLD not in payload
    assert context["conversation_memory"] == NEW
    assert context["conversation_history"] == context["channel_recent_messages"] == []

    [replacement] = rio.store.verified_structured_memory(DM)
    withdraw = rio.memory.get_command("withdraw")
    await withdraw.callback(user, replacement["id"], True)
    assert "철회했어요" in user.followup.send.await_args.args[0]
    await rio.llm.answer(rio.store, DM, "시험 계정", "내 음료 취향이 어땠지?")
    payload = rio.model.await_args.kwargs["input"][0]["content"]
    assert OLD not in payload and NEW not in payload


async def test_clear_and_admin_purge_keep_owner_and_target_boundaries(rio):
    foreign = Scope(None, 10, 200)
    add_preference(rio.store)
    add_preference(rio.store, foreign, message_id=2001)
    clear = rio.memory.get_command("clear")
    await clear.callback(rio.interaction(), False)
    assert rio.store.verified_structured_memory(DM)
    await clear.callback(rio.interaction(), True)
    assert not rio.store.verified_structured_memory(DM)
    assert rio.store.verified_structured_memory(foreign)

    purge = rio.memory.get_command("purge")
    user = rio.interaction(command=purge)
    assert not await rio.memory.interaction_check(user)
    assert rio.store.verified_structured_memory(foreign)

    rio.bot.emoji_admin_ids.add(100)
    assert await rio.memory.interaction_check(user)
    await purge.callback(rio.memory, user, "global", False)
    assert rio.store.verified_structured_memory(foreign)
    await purge.callback(rio.memory, user, "global", True)
    assert not rio.store.verified_structured_memory(foreign)


async def test_verified_candidate_is_available_to_owner_without_automatic_recall(rio):
    item_id = add_preference(rio.store)
    marker = "자동 회상 시험용 후보"
    with rio.store.db:
        rio.store.db.execute(
            "UPDATE structured_memory_items SET content=? WHERE id=?", (marker, item_id),
        )
    interaction = rio.interaction()
    await rio.memory.get_command("items").callback(interaction)
    assert marker in interaction.response.send_message.await_args.args[0]
    await rio.llm.answer(rio.store, DM, "시험 계정", "예전 약속 기억해?")
    assert marker not in rio.model.await_args.kwargs["input"][0]["content"]


@pytest.mark.parametrize("shared", [False, True])
async def test_in_flight_summary_cannot_restore_withdrawn_history(rio, shared):
    scope = Scope(1, 10, 100, True) if shared else DM
    item_id = add_preference(rio.store, scope)
    rio.store.add_shared_call(scope, 1001, "synthetic", OLD)
    for message_id in (1002, 1003):
        rio.store.add(scope, message_id, f"{OLD} {message_id}", "응", turn_id=str(message_id))
        rio.store.add_shared_call(scope, message_id, "synthetic", OLD)
    started, release = asyncio.Event(), asyncio.Event()

    async def reply(**_kwargs):
        started.set()
        await release.wait()
        return NS(status="completed", output_text=OLD, output=[])

    rio.model.side_effect = reply
    summarize = rio.llm.summarize_shared if shared else rio.llm.summarize
    task = asyncio.create_task(summarize(rio.store, scope))
    await asyncio.wait_for(started.wait(), 1)
    try:
        assert rio.store.delete_structured_memory(scope, item_id)
    finally:
        release.set()
        await task
    assert rio.store.summary(scope) == rio.store.shared_summary(scope) == ("", 0)


async def test_global_purge_cancels_in_flight_reply_storage_and_echo(rio):
    add_preference(rio.store)
    channel = MagicMock(spec=discord.DMChannel, id=DM.channel_id)
    channel.type = discord.ChannelType.private
    channel.typing.return_value.__aenter__ = AsyncMock()
    channel.typing.return_value.__aexit__ = AsyncMock()
    channel.send = AsyncMock(return_value=NS(id=9999))
    message = NS(
        id=1002, content="리오야 음료 추천해 줘",
        author=NS(id=DM.user_id, bot=False, display_name="시험 계정"),
        guild=None, channel=channel, webhook_id=None, mentions=[], attachments=[],
        stickers=[], reference=None,
    )
    started, release = asyncio.Event(), asyncio.Event()

    async def reply(**_kwargs):
        started.set()
        await release.wait()
        return NS(status="completed", output_text=f"예전에는 {OLD}라고 했어", output=[])

    rio.model.side_effect = reply
    task = asyncio.create_task(rio.bot.on_message(message))
    await asyncio.wait_for(started.wait(), 1)
    try:
        rio.store.purge_all_memory()
    finally:
        release.set()
        await task

    assert not rio.store.seen(1002)
    assert not rio.store.verified_structured_memory(DM)
    assert OLD not in channel.send.await_args.args[0]
