from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.config import Settings
from hina_bot.store import Store
from hina_bot.web_bot import HinaClient


@pytest.fixture
def slash_bot():
    store = Store(":memory:")
    llm = NS(close=AsyncMock())
    bot = HinaClient(
        Settings("test", "test", cooldown=0, bot_admin_ids=frozenset({100})),
        store=store,
        llm=llm,
    )
    yield bot
    store.close()


def test_runtime_registers_unified_slash_commands(slash_bot):
    memory = slash_bot.tree.get_command("memory")
    assert memory is not None
    for name in (
        "mode", "chatlog", "status", "overview", "show", "note", "note-clear", "clear",
        "server-show", "server-note", "server-clear",
    ):
        assert memory.get_command(name) is not None

    emoji = slash_bot.tree.get_command("emoji")
    assert emoji is not None
    for name in ("add", "list", "edit", "remove"):
        assert emoji.get_command(name) is not None

    assert slash_bot.tree.get_command("instruction") is not None
    assert slash_bot.tree.get_command("knowledge") is not None
    assert slash_bot.tree.get_command("help") is not None


@pytest.mark.asyncio
async def test_production_client_disables_prefix_slash_parser(slash_bot):
    assert slash_bot._management_text("/메모 old-style") is False
    assert await slash_bot.command(None, None, "/메모 old-style") is None


@pytest.mark.asyncio
async def test_memory_group_only_keeps_configuration_commands_admin_only(slash_bot):
    memory = slash_bot.tree.get_command("memory")

    user_response = NS(send_message=AsyncMock())
    user = NS(id=200)
    show_interaction = NS(user=user, command=NS(name="show"), response=user_response)
    assert await memory.interaction_check(show_interaction) is True

    mode_interaction = NS(user=user, command=NS(name="mode"), response=user_response)
    assert await memory.interaction_check(mode_interaction) is False
    user_response.send_message.assert_awaited()

    admin_interaction = NS(user=NS(id=100), command=NS(name="mode"), response=NS(send_message=AsyncMock()))
    assert await memory.interaction_check(admin_interaction) is True


@pytest.mark.asyncio
async def test_memory_note_is_available_as_slash_command(slash_bot):
    memory = slash_bot.tree.get_command("memory")
    note = memory.get_command("note")
    response = NS(send_message=AsyncMock())
    interaction = NS(
        guild_id=1,
        channel_id=10,
        user=NS(id=200, guild_permissions=NS(manage_guild=False)),
        response=response,
    )

    await note.callback(interaction, "새 개인 메모")

    assert slash_bot.store.note("guild:1:user:200") == "새 개인 메모"
    response.send_message.assert_awaited()
