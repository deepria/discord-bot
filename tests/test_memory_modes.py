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

    def test_scope_persistence_and_existing_memory_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "modes.db")
            store = Store(path)
            scope = Scope(1, 10, 100)
            store.add(scope, 1, "keep", "keep reply")
            store.set_memory_mode(scope, "off")
            self.assertEqual(store.memory_mode(Scope(1, 10, 200)), "off")
            self.assertEqual(store.memory_mode(Scope(1, 20, 100)), "normal")
            self.assertEqual(store.memory_mode(Scope(None, 10, 100)), "normal")
            store.close()
            store = Store(path)
            self.assertEqual(store.memory_mode(scope), "off")
            store.set_memory_mode(scope, "normal")
            self.assertEqual(store.history(scope)[0]["content"], "keep")
            with self.assertRaises(ValueError):
                store.set_memory_mode(scope, "invalid")
            store.close()


class ModeCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_access_control(self):
        group = MemoryCommands(NS(emoji_admin_ids={100}))
        interaction = NS(user=NS(id=200), response=NS(send_message=AsyncMock()))
        self.assertFalse(await group.interaction_check(interaction))
        interaction.user.id = 100
        self.assertTrue(await group.interaction_check(interaction))
        self.assertEqual({c.name for c in group.commands}, {"mode", "status"})

    async def test_switch_waits_for_inflight_turn_and_clears_only_this_channel(self):
        store, recent, lock = Store(":memory:"), RecentMessages(), asyncio.Lock()
        scope, other = Scope(1, 10, 100), Scope(1, 20, 100)
        recent.add(scope, 1, "A", "current")
        recent.add(other, 2, "A", "other channel")
        client = NS(store=store, recent=recent, channel_lock=lambda _: lock)
        group = MemoryCommands(client)
        interaction = NS(guild_id=1, channel_id=10, user=NS(id=100),
                         response=NS(defer=AsyncMock()), followup=NS(send=AsyncMock()))
        await lock.acquire()
        task = asyncio.create_task(group.mode.callback(group, interaction, MemoryMode.off))
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.assertEqual(store.memory_mode(scope), "normal")
        lock.release()
        await task
        self.assertEqual(store.memory_mode(scope), "off")
        self.assertEqual(recent.context(scope, 3), [])
        self.assertEqual(len(recent.context(other, 3)), 1)
        self.assertTrue(interaction.followup.send.call_args.kwargs["ephemeral"])
        store.close()
