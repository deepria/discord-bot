"""Regression tests for SIGTERM and SQLite-safe Discord shutdown."""
import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

from rio_bot.core.store import Store
from rio_bot.discord.bot import RioClient
from rio_bot.discord.runtime_entry import install_sigterm_handler


class StoreCheckpointTests(unittest.TestCase):
    def test_checkpoint_requests_wal_truncation(self):
        store = object.__new__(Store)
        store.db = MagicMock()
        store.checkpoint()

        store.db.execute.assert_called_once_with("PRAGMA wal_checkpoint(TRUNCATE)")
        store.db.execute.return_value.fetchone.assert_called_once_with()


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    def _client(self):
        client = object.__new__(RioClient)
        client.settings = NS(shutdown_grace_seconds=0)
        client.stopping = False
        client._close_task = None
        client.active_tasks = set()
        client._publish_runtime_status = MagicMock()
        client.llm = NS(close=AsyncMock(side_effect=RuntimeError("provider unavailable")))
        client.store = NS(checkpoint=MagicMock(side_effect=RuntimeError("checkpoint failed")),
                          close=MagicMock())
        client.events = NS(close=MagicMock())
        return client

    async def test_close_is_idempotent_and_continues_after_cleanup_failures(self):
        client = self._client()
        active = asyncio.create_task(asyncio.sleep(60))
        client.active_tasks.add(active)

        with patch("rio_bot.discord.bot.discord.Client.close", new=AsyncMock()) as parent_close:
            await asyncio.gather(client.close(), client.close())

        self.assertTrue(client.stopping)
        self.assertTrue(active.cancelled())
        client.llm.close.assert_awaited_once()
        client.store.checkpoint.assert_called_once()
        client.store.close.assert_called_once()
        client.events.close.assert_called_once()
        parent_close.assert_awaited_once()

    async def test_sigterm_schedules_the_client_close_on_its_running_loop(self):
        queued = []
        loop = NS(is_running=lambda: True, call_soon_threadsafe=queued.append)
        bot = NS(loop=loop, is_closed=lambda: False, close=AsyncMock(), stopping=False)
        captured = {}

        with patch("rio_bot.discord.runtime_entry.signal.getsignal", return_value=None), patch(
            "rio_bot.discord.runtime_entry.signal.signal",
            side_effect=lambda _signal, handler: captured.setdefault("handler", handler),
        ):
            install_sigterm_handler(bot)

        captured["handler"](None, None)
        self.assertEqual(len(queued), 1)
        queued[0]()
        await asyncio.sleep(0)
        self.assertTrue(bot.stopping)
        bot.close.assert_awaited_once()
