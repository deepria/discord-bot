import base64
import io
import json
import wave
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import discord
import httpx
import pytest

from rio_bot.ai.providers import _GeminiResponses
from rio_bot.ai.runtime_llm import LLM
from rio_bot.core.config import Settings
from rio_bot.core.routing import Scope
from rio_bot.core.store import Store
from rio_bot.discord.audio import collect_audio
from rio_bot.discord.web_bot import RioClient


def wav():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0" * 1600)
    return buffer.getvalue()


def attachment(data, **overrides):
    defaults = {
        "content_type": "audio/wav", "filename": "voice.wav", "size": len(data),
        "duration": None, "read": AsyncMock(return_value=data),
        "url": "https://cdn.discordapp.com/attachments/private-url",
    }
    return NS(**(defaults | overrides))


def opus():
    def page(flags, granule, number, body):
        return (b"OggS\0" + bytes([flags]) + granule.to_bytes(8, "little")
                + b"test" + number.to_bytes(4, "little") + b"\0" * 4
                + bytes([1, len(body)]) + body)
    return page(2, 0, 0, b"OpusHead" + b"\0" * 11) + page(4, 48000, 1, b"sample")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["wav", "opus", "oversize", "overlong", "bad", "read_failed",
                                  "openai", "openrouter"])
async def test_audio_formats_and_metadata_fallback(kind):
    data = opus() if kind == "opus" else wav()
    options = {
        "oversize": {"size": 50 * 1024 * 1024},
        "overlong": {"duration": 121},
        "read_failed": {"read": AsyncMock(side_effect=OSError("private url"))},
    }
    part = attachment(b"bad" if kind == "bad" else data, **options.get(kind, {}))
    provider = kind if kind in {"openai", "openrouter"} else "gemini"
    message = NS(id=1, author=NS(id=10), attachments=[part])
    result, = await collect_audio(message, None, provider=provider)
    assert "url" not in json.dumps(result.metadata)
    if kind in {"wav", "opus"}:
        assert result.metadata["status"] == "attached"
        assert result.data == data and result.metadata["duration_seconds"] > 0
    else:
        assert result.metadata["status"] != "attached"
        assert not result.data and not result.blocks()
    if kind in {"oversize", "overlong", "openai", "openrouter"}:
        part.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_gemini_actual_request_receives_only_current_and_explicit_reply_audio():
    sent = []

    async def handle(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "completed", "steps": [
            {"type": "model_output", "content": [{"type": "text", "text": "테스트 응답"}]},
        ]})

    settings = Settings(
        "test", "test", cooldown=0, dm_always_reply=True, chat_web_search=False,
        provider="gemini", usage_log_path="", event_log_path="", telemetry_db_path="",
        summary_every=99,
    )
    store = Store(":memory:")
    author = NS(id=100, bot=False, display_name="질문자")
    channel = MagicMock(spec=discord.DMChannel, id=10, recipient=author)
    channel.type = discord.ChannelType.private
    channel.typing.return_value.__aenter__ = AsyncMock()
    channel.typing.return_value.__aexit__ = AsyncMock()
    channel.send = AsyncMock(return_value=NS(id=1000))
    common = {
        "channel": channel, "guild": None, "author": author, "webhook_id": None, "mentions": [],
        "stickers": [], "reference": None, "type": discord.MessageType.default,
    }
    raw = wav()
    clip = attachment(raw)
    original = NS(**(common | {"id": 200, "content": "", "attachments": [clip]}))
    reply = NS(**(common | {"id": 201, "content": "이 음성에서 뭐라고 했어?", "attachments": [],
                           "type": discord.MessageType.reply, "reference": NS(
                               channel_id=10, type=discord.MessageReferenceType.reply,
                               resolved=original, message_id=200,
                           )}))
    unrelated = NS(**(common | {"id": 202, "content": "안녕", "attachments": []}))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        llm = LLM(settings, client=NS(
            responses=_GeminiResponses(http), provider_name="gemini", close=AsyncMock(),
        ))
        bot = RioClient(settings, store=store, llm=llm)
        bot._connection.user = NS(id=99)
        try:
            await bot.on_message(original)
            await bot.on_message(reply)
            await bot.on_message(unrelated)
            rows = store.history(Scope(None, 10, 100))
            assert len(rows) == 2 and rows[0]["reply"] == ""
            assert not original.content
        finally:
            await bot.close()

    for index, relation in [(0, "current_message"), (1, "explicit_reply")]:
        body = sent[index]
        meta = json.loads(body["input"][0]["content"][0]["text"].split("\n", 1)[1])
        assert meta["audio_attachments"][0]["relation"] == relation
        assert {"type": "audio", "data": base64.b64encode(raw).decode(), "mime_type": "audio/wav"} in (
            body["input"][-1]["content"]
        )
        assert not body["store"]
        assert clip.url not in json.dumps(body)
    assert "audio_attachments" not in json.dumps(sent[-1]["input"])
    assert clip.read.await_count == 2
