"""Collect native audio only from the explicit Discord request and its reply."""

import asyncio
import io
import math
import struct
import wave
from itertools import islice
from pathlib import PurePath

import discord

from rio_bot.ai.audio_context import AudioInput

_MAX_FILES = 2
_MAX_BYTES = 4 * 1024 * 1024
_MAX_SECONDS = 120
_AUDIO_EXTENSIONS = {".ogg", ".opus", ".oga", ".wav", ".flac", ".mp3", ".m4a", ".webm", ".aac"}


def _is_audio(attachment):
    mime = str(getattr(attachment, "content_type", None) or "").lower().split(";", 1)[0]
    suffix = PurePath(getattr(attachment, "filename", None) or "").suffix.lower()
    return mime.startswith("audio/") or mime == "application/ogg" or suffix in _AUDIO_EXTENSIONS


def _opus_seconds(data):
    """Read a single Discord Ogg Opus stream and reject chained or incomplete pages."""
    offset = 0
    serial = data[14:18]
    final = 0
    pages = 0
    while offset + 27 <= len(data):
        page = data[offset:offset + 27]
        if page[:5] != b"OggS\x00" or page[14:18] != serial:
            return None
        segments = page[26]
        body = offset + 27 + segments
        if body > len(data):
            return None
        end = body + sum(data[offset + 27:body])
        if end > len(data):
            return None
        if pages == 0:
            if not page[5] & 2 or not data[body:end].startswith(b"OpusHead") or end - body < 19:
                return None
        elif page[5] & 2:
            return None
        final = int.from_bytes(page[6:14], "little", signed=True)
        offset = end
        pages += 1
        if page[5] & 4:
            return final / 48000 if offset == len(data) and pages > 1 else None
    return None


def _format(data):
    if data.startswith(b"OggS"):
        return "audio/ogg", _opus_seconds(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        try:
            with wave.open(io.BytesIO(data), "rb") as stream:
                frames = stream.getnframes()
                seconds = frames / stream.getframerate()
                if 0 < seconds <= _MAX_SECONDS:
                    sample_bytes = stream.getnchannels() * stream.getsampwidth()
                    if len(stream.readframes(frames)) == frames * sample_bytes:
                        return "audio/wav", seconds
        except (EOFError, ValueError, wave.Error, struct.error):
            pass
        return "audio/wav", None
    if data[:8] == b"fLaC\x00\x00\x00\x22" and len(data) > 42:
        streaminfo = int.from_bytes(data[18:26], "big")
        samples = streaminfo & ((1 << 36) - 1)
        rate = (streaminfo >> 44) & 0xfffff
        return "audio/flac", samples / rate if rate else None
    return None, None


def _declared_seconds(attachment):
    value = getattr(attachment, "duration", None)
    return value if isinstance(value, (int, float)) and math.isfinite(value) and value > 0 else None


async def collect_audio(message, reply, *, provider):
    records = []
    remaining = _MAX_BYTES
    for target, relation in ((message, "current_message"), (reply, "explicit_reply")):
        if target is None:
            continue
        attachments = (item for item in (getattr(target, "attachments", ()) or ())
                       if _is_audio(item))
        for index, attachment in enumerate(islice(attachments, _MAX_FILES - len(records)), 1):
            declared = _declared_seconds(attachment)
            metadata = {
                "message_id": str(target.id), "attached_by": str(target.author.id),
                "relation": relation, "attachment_number": index,
                "duration_seconds": declared, "status": "provider_unsupported",
            }
            data = b""
            if provider == "gemini":
                metadata["status"] = "limit"
                size = getattr(attachment, "size", 0)
                if 0 < size <= remaining and (declared is None or declared <= _MAX_SECONDS):
                    metadata["status"] = "unavailable"
                    try:
                        async with asyncio.timeout(3):
                            data = await attachment.read()
                    except (TimeoutError, discord.HTTPException, OSError):
                        pass
                    if data:
                        mime, duration = _format(data) if len(data) <= remaining else (None, None)
                        if mime and duration is not None and 0 < duration <= _MAX_SECONDS:
                            remaining -= len(data)
                            metadata.update(status="attached", mime_type=mime,
                                            duration_seconds=round(duration, 2))
                        else:
                            data = b""
                            metadata.update(status="unsupported_or_over_limit", mime_type=mime)
            records.append(AudioInput(metadata, data))
    return records
