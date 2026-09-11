"""SDK/adapter contract tests; no Discord login or paid API requests."""
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

try:
    import discord
    import httpx
    from openai import AsyncOpenAI
    AVAILABLE = True
except ModuleNotFoundError:
    AVAILABLE = False

from hina_bot.routing import Scope
from hina_bot.store import Store

if AVAILABLE:
    from hina_bot.bot import HinaClient
    from hina_bot.config import Settings
    from hina_bot.llm import LLM


@unittest.skipUnless(AVAILABLE, "Install project dev dependencies to test SDK/Discord adapters")
class SDKTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []

        def handler(request):
            self.calls.append(json.loads(request.content))
            return httpx.Response(200, json={
                "id": "resp_test", "object": "response", "created_at": 0,
                "status": "completed", "model": "gpt-4.1-mini",
                "output": [{"type": "message", "id": "msg_test", "role": "assistant",
                            "status": "completed", "content": [
                                {"type": "output_text", "text": "응, 기억하고 있어.",
                                 "annotations": []}]}],
            })

        client = AsyncOpenAI(api_key="test-not-a-real-key",
                             http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        self.llm = LLM(Settings("test", "test", summary_every=2), client=client)
        self.store = Store(":memory:")

    async def asyncTearDown(self):
        await self.llm.close()
        self.store.close()

    async def test_sdk_payload_and_no_server_access_to_dm(self):
        server = Scope(1, 10, 100, True)
        dm = Scope(None, 20, 100)
        self.store.add(dm, 1, "DM 비밀", "응")
        reply = await self.llm.answer(self.store, server, "사용자", "안녕",
                                      public_context=[{"secret": "must not enter server"}])
        self.assertEqual(reply, "응, 기억하고 있어.")
        payload = self.calls[-1]
        self.assertFalse(payload["store"])
        self.assertNotIn("DM 비밀", str(payload))
        self.assertNotIn("must not enter server", str(payload))
        self.assertIn("소라사키 히나", payload["instructions"])

    async def test_dm_reads_public_context_without_copying_to_summary_input(self):
        dm = Scope(None, 20, 100)
        await self.llm.answer(self.store, dm, "사용자", "안녕",
                              public_context=[{"summary": "public-source-marker"}])
        self.assertIn("public-source-marker", str(self.calls[-1]["input"]))
        self.store.add(dm, 1, "안녕", "응")
        self.store.add(dm, 2, "반가워", "응")
        await self.llm.summarize(self.store, dm)
        self.assertNotIn("public-source-marker", self.calls[-1]["input"])
        self.assertEqual(self.store.summary(dm)[0], "응, 기억하고 있어.")


@unittest.skipUnless(AVAILABLE, "Install project dev dependencies to test SDK/Discord adapters")
class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.llm = NS(answer=AsyncMock(return_value="안녕"), summarize=AsyncMock(), close=AsyncMock())
        self.bot = HinaClient(Settings("test", "test", cooldown=0), store=self.store, llm=self.llm)
        self.bot._connection.user = NS(id=99)
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.id = 10
        self.channel.send = AsyncMock()
        self.channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
        self.channel.typing.return_value.__aexit__ = AsyncMock(return_value=None)
        self.channel.permissions_for.return_value = NS(view_channel=True, read_message_history=True)
        self.guild = NS(id=1, default_role=NS(), unavailable=False)
        self.author = NS(id=100, bot=False, display_name="사용자",
                         guild_permissions=NS(manage_guild=False))

    async def asyncTearDown(self):
        await self.bot.close()

    def message(self, text="히나야 안녕", id=1):
        return NS(id=id, content=text, author=self.author, guild=self.guild,
                  channel=self.channel, mentions=[], webhook_id=None)

    async def test_plain_channel_send_and_duplicate_suppression(self):
        await self.bot.on_message(self.message())
        await self.bot.on_message(self.message())
        self.assertEqual(self.llm.answer.await_count, 1)
        kwargs = self.channel.send.call_args.kwargs
        self.assertNotIn("reference", kwargs)
        mentions = kwargs["allowed_mentions"].to_dict()
        self.assertEqual(mentions["parse"], [])
        self.assertFalse(mentions.get("replied_user", False))

    async def test_untriggered_message_is_not_saved(self):
        await self.bot.on_message(self.message("일반 대화"))
        self.llm.answer.assert_not_awaited()
        self.assertFalse(self.store.seen(1))

    async def test_model_failure_does_not_create_memory(self):
        self.llm.answer.side_effect = RuntimeError("fake failure")
        await self.bot.on_message(self.message())
        self.llm.answer.assert_awaited_once()
        self.assertFalse(self.store.seen(1))
        self.llm.summarize.assert_not_awaited()

    async def test_non_admin_cannot_write_server_note(self):
        await self.bot.on_message(self.message("히나야 /서버메모 override"))
        self.assertEqual(self.store.note("guild:1"), "")
        self.llm.answer.assert_not_awaited()

    async def test_current_visibility_and_membership_rechecked(self):
        self.store.add(Scope(1, 10, 100, True), 500, "공개", "응")
        member = NS(id=100)
        self.guild.get_channel = MagicMock(return_value=self.channel)
        self.guild.fetch_member = AsyncMock(return_value=member)
        self.bot.get_guild = MagicMock(return_value=self.guild)
        self.assertEqual(len(await self.bot.public_sources(100)), 1)
        self.channel.permissions_for.return_value = NS(view_channel=False, read_message_history=False)
        self.assertEqual(await self.bot.public_sources(100), [])
        self.channel.permissions_for.return_value = NS(view_channel=True, read_message_history=True)
        self.guild.fetch_member.return_value = None
        self.assertEqual(await self.bot.public_sources(100), [])

    async def test_threads_excluded(self):
        self.store.add(Scope(1, 10, 100, True), 500, "공개", "응")
        self.guild.get_channel = MagicMock(return_value=MagicMock(spec=discord.Thread))
        self.bot.get_guild = MagicMock(return_value=self.guild)
        self.assertEqual(await self.bot.public_sources(100), [])
