import logging
from contextvars import ContextVar

import discord

from rio_bot.core.message_provenance import observable_discord_text

from .forward_context import is_forward
from .routing import trigger_text

log = logging.getLogger("rio")

REPLY_CONTEXT = ContextVar("reply_context", default=())


def _row(target, bot_id: int, call_prefixes: tuple[str, ...]) -> dict | None:
    author = getattr(target, "author", None)
    user_id = getattr(author, "id", None)
    raw = getattr(target, "content", "") or ""
    if not raw or author is None or user_id is None:
        return None
    triggered = trigger_text(target, bot_id, prefixes=call_prefixes)
    content = observable_discord_text(raw, triggered_text=triggered).strip()
    if not content:
        return None

    own_bot = user_id == bot_id
    other_bot = bool(getattr(author, "bot", False)) or getattr(target, "webhook_id", None) is not None
    role = "assistant" if own_bot else ("bot" if other_bot else "user")
    created_at = getattr(target, "created_at", None)
    return {
        "message_id": str(getattr(target, "id", "")),
        "user_id": str(user_id),
        "name": str(getattr(author, "display_name", getattr(author, "name", "")))[:100],
        "content": content[:4000],
        "role": role,
        "context_kind": "replied_message",
        "reference_strength": "explicit_reply",
        "at": created_at.isoformat() if created_at is not None else "",
    }


async def resolve_reply_target(message):
    """Resolve a readable Discord reply target in the current channel."""
    reference = getattr(message, "reference", None)
    if reference is None or is_forward(message):
        return None

    channel = getattr(message, "channel", None)
    current_channel_id = getattr(channel, "id", None)
    reference_channel_id = getattr(reference, "channel_id", None)
    if (
        current_channel_id is not None
        and reference_channel_id is not None
        and current_channel_id != reference_channel_id
    ):
        return None

    target = getattr(reference, "resolved", None)
    if target is not None and getattr(target, "author", None) is None:
        target = None

    if target is None:
        message_id = getattr(reference, "message_id", None)
        fetch_message = getattr(channel, "fetch_message", None)
        if message_id is None or fetch_message is None:
            return None
        try:
            target = await fetch_message(message_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            log.warning("Reply context lookup failed (%s)", type(exc).__name__)
            return None

    target_channel = getattr(target, "channel", None)
    target_channel_id = getattr(target_channel, "id", current_channel_id)
    if (
        current_channel_id is not None
        and target_channel_id is not None
        and current_channel_id != target_channel_id
    ):
        return None

    return target


def reply_context_rows(target, bot_id: int, *, call_prefixes=("리오야",)) -> list[dict]:
    row = _row(target, bot_id, call_prefixes)
    return [row] if row is not None else []


async def collect_reply_context(message, bot_id: int) -> list[dict]:
    """Return request-scoped context for an explicit reply, independent of passive capture."""
    return reply_context_rows(await resolve_reply_target(message), bot_id)
