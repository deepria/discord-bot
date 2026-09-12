import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from hina_bot.memory_commands import MemoryCommands, MemoryMode
from hina_bot.recent import RecentMessages
from hina_bot.routing import Scope
from hina_bot.store import Store


class ModeTests(unittest.TestCase):
    def test_flags(self):
        expected = {"normal": (True, True), "read_only": (True, False),
                    "write_only": (False, True), "off": (False, False)}
        for value, flags in expected.items():
            mode = MemoryMode(value)
            self.assertEqual((mode.reads, mode.writes), flags)

    def test_global_server_channel_precedence_and_inheritance(self):
        store = Store(":memory:")
        channel = Scope(1, 10, 100)
        sibling = Scope(1, 20, 200)
        other_guild = Scope(2, 30, 100)
        dm = Scope(None, 40, 100)

        self.assertEqual(store.memory_mode(channel), "normal")
        self.assertEqual(store.memory_mode_chain(channel)["source"], "default")

        store.set_memory_mode_override("global", "off")
        self.assertEqual(store.memory_mode(channel), "off")
        self.assertEqual(store.memory_mode(other_guild), "off")
        self.assertEqual(store.memory_mode(dm), "off")

        store.set_memory_mode_override("guild:1", "read_only")
        self.assertEqual(store.memory_mode(channel), "read_only")
        self.assertEqual(store.memory_mode(sibling), "read_only")
        self.assertEqual(store.memory_mode(other_guild), "off")

        store.set_memory_mode_override(channel.channel, "write_only")
        chain = store.memory_mode_chain(channel)
        self.assertEqual(chain["effective"], "write_only")
        self.assertEqual(chain["source"], "channel")

        store.set_memory_mode_override(channel.channel, None)
        self.assertEqual(store.memory_mode(channel), "read_only")
        self.assertEqual(store.memory_mode_chain(channel)["source"], "server")
        store.set_memory_mode_override("guild:1", None)
        self.assertEqual(store.memory_mode(channel), "off")
        self.assertEqual(store.memory_mode_chain(channel)["source"], "global")
        store.close()

    def test_existing_channel_override_persists_and_memory_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "modes.db")
            store = Store(path)
            scope = Scope(1, 10, 100)
            store.add(scope, 1, "keep", "keep reply")
            # Backward-compatible setter still means a channel override.
            store.set_memory_mode(scope, "off")
            self.assertEqual(store.memory_mode(Scope(1, 10, 200)), "off")
            self.assertEqual(store.memory_mode(Scope(1, 20, 100)), "normal")
            store.close()

            store = Store(path)
            self.assertEqual(store.memory_mode(scope), "off")
            self.assertEqual(store.memory_mode_override(scope.channel), "off")
            self.assertEqual(store.history(scope)[0]["content"], "keep")
            store.set_memory_mode_override(scope.channel, None)
            self.assertEqual(store.memory_mode(scope), "normal")
            with self.assertRaises(ValueError):
                store.set_memory_mode(scope, "invalid")
            store.close()


class ModeCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_access_control(self):
        group = MemoryCommands(NS(emoji_admin_ids={100, 101}))
        interaction = NS(user=NS(id=200), response=NS(send_message=AsyncMock()))
        self.assertFalse(await group.interaction_check(interaction))
        for admin_id in (100, 101):
            interaction.user.id = admin_id
            self.assertTrue(await group.interaction_check(interaction))
        self.assertEqual({c.name for c in group.commands}, {"mode", "status", "overview"})

    def test_available_in_guilds_and_private_contexts(self):
        group = MemoryCommands(NS(emoji_admin_ids={100}))
        self.assertTrue(group.allowed_contexts.guild)
        self.assertTrue(group.allowed_contexts.dm_channel)
        self.assertTrue(group.allowed_contexts.private_channel)
        self.assertTrue(group.allowed_installs.guild)
        self.assertTrue(group.allowed_installs.user)

    async def test_switch_waits_for_inflight_turn_and_preserves_recent_context(self):
        store, recent, lock = Store(":memory:"), RecentMessages(), asyncio.Lock()
        scope, other = Scope(1, 10, 100), Scope(1, 20, 100)
        recent.add(scope, 1, "A", "current")
        recent.add(other, 2, "A", "other channel")
        client = NS(store=store, recent=recent, channel_lock=lambda _: lock)
        group = MemoryCommands(client)
        interaction = NS(guild_id=1, channel_id=10, user=NS(id=100),
                         response=NS(defer=AsyncMock(), send_message=AsyncMock()),
                         followup=NS(send=AsyncMock()))
        await lock.acquire()
        task = asyncio.create_task(group.mode.callback(group, interaction, "off", "channel"))
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.assertEqual(store.memory_mode(scope), "normal")
        lock.release()
        await task
        self.assertEqual(store.memory_mode(scope), "off")
        self.assertEqual(len(recent.context(scope, 3)), 1)
        self.assertEqual(len(recent.context(other, 3)), 1)
        self.assertTrue(interaction.followup.send.call_args.kwargs["ephemeral"])
        store.close()

    async def test_inherit_removes_lower_override_and_status_shows_chain(self):
        store, lock = Store(":memory:"), asyncio.Lock()
        scope = Scope(1, 10, 100)
        store.set_memory_mode_override("global", "off")
        store.set_memory_mode_override("guild:1", "read_only")
        store.set_memory_mode_override(scope.channel, "write_only")
        client = NS(store=store, channel_lock=lambda _: lock)
        group = MemoryCommands(client)
        interaction = NS(guild_id=1, channel_id=10, user=NS(id=100),
                         response=NS(defer=AsyncMock(), send_message=AsyncMock()),
                         followup=NS(send=AsyncMock()))

        await group.mode.callback(group, interaction, "inherit", "channel")
        self.assertIsNone(store.memory_mode_override(scope.channel))
        self.assertEqual(store.memory_mode(scope), "read_only")
        text = interaction.followup.send.call_args.args[0]
        self.assertIn("최종 적용: **read_only**", text)
        self.assertIn("채널: `상속 → read_only`", text)
        store.close()

    async def test_global_cannot_inherit_and_server_target_requires_guild(self):
        store = Store(":memory:")
        client = NS(store=store, channel_lock=lambda _: asyncio.Lock())
        group = MemoryCommands(client)
        interaction = NS(guild_id=None, channel_id=10, user=NS(id=100),
                         response=NS(defer=AsyncMock(), send_message=AsyncMock()),
                         followup=NS(send=AsyncMock()))
        await group.mode.callback(group, interaction, "inherit", "global")
        self.assertIn("전역 설정은", interaction.response.send_message.call_args.args[0])
        interaction.response.send_message.reset_mock()
        await group.mode.callback(group, interaction, "off", "server")
        self.assertIn("DM에서는 서버 설정", interaction.response.send_message.call_args.args[0])
        store.close()

    def test_overview_lists_effective_values_and_can_compact_inherited_channels(self):
        store = Store(":memory:")
        store.set_memory_mode_override("global", "off")
        store.set_memory_mode_override("guild:1", "read_only")
        store.set_memory_mode_override("guild:1:channel:11", "normal")
        guild = NS(id=1, name="테스트 서버",
                   text_channels=[NS(id=10, name="일반"), NS(id=11, name="봇")], threads=[])
        client = NS(store=store, guilds=[guild], settings=NS(allowed_guild_ids=frozenset()))
        group = MemoryCommands(client)

        rows = group._overview_rows(100, "all")
        self.assertIn(["전역", "GLOBAL", "off", "off"], rows)
        self.assertIn(["서버", "테스트 서버", "read_only", "read_only"], rows)
        self.assertIn(["채널", "테스트 서버/#일반", "상속", "read_only"], rows)
        self.assertIn(["채널", "테스트 서버/#봇", "normal", "normal"], rows)

        compact = group._overview_rows(100, "overrides")
        self.assertIn(["채널", "테스트 서버/#봇", "normal", "normal"], compact)
        self.assertIn(["채널", "테스트 서버/(나머지 1개)", "상속", "read_only"], compact)
        store.close()
