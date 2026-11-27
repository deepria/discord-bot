"""On-demand Discord history scoped to the message that initiated the answer."""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import uuid4

import discord

from rio_bot.ai.egress_policy import strict_policy
from rio_bot.ai.local_tools import LocalTool
from rio_bot.core.message_provenance import observable_discord_text

from .chatlog_capture import capture_mode
from .routing import Scope, trigger_text

_SCHEMA = {
    "type": "function",
    "name": "get_chat_history",
    "description": "Read older user messages only in the current Discord conversation.",
    "strict": False,
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {"type": "string", "description": "Optional literal substring, up to 200 chars."},
            "author_user_ids": {
                "type": "array", "items": {"type": "string"},
                "description": "Up to five known Discord user IDs.",
            },
            "start_at": {"type": "string", "description": "Inclusive ISO 8601 with time zone."},
            "end_at": {"type": "string", "description": "Exclusive ISO 8601 with time zone."},
            "cursor": {"type": "string", "description": "Previous next_cursor with the same filters."},
        },
    },
}
_GUILD_TYPES = {
    discord.ChannelType.text, discord.ChannelType.news, discord.ChannelType.public_thread,
    discord.ChannelType.private_thread, discord.ChannelType.news_thread,
}
_SCAN_LIMIT = 100
_RESULT_LIMIT = 10
_LOOKUP_SECONDS = 4
_TOTAL_LOOKUP_SECONDS = 9


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None or not 2015 <= result.year < 2100:
        raise ValueError
    return result


def _first_snowflake_at(value):
    value = value.astimezone(UTC)
    remainder = value.microsecond % 1000
    if remainder:
        value += timedelta(microseconds=1000 - remainder)
    return discord.utils.time_snowflake(value)


def _filters(arguments):
    if arguments.keys() - _SCHEMA["parameters"]["properties"].keys():
        raise ValueError
    query = arguments.get("query", "")
    authors = arguments.get("author_user_ids", [])
    if not isinstance(query, str) or len(query) > 200:
        raise ValueError
    if not isinstance(authors, list) or len(authors) > 5 or any(
        not isinstance(author, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", author)
        or int(author) >= 2**64 for author in authors
    ):
        raise ValueError
    start = _timestamp(arguments["start_at"]) if "start_at" in arguments else None
    end = _timestamp(arguments["end_at"]) if "end_at" in arguments else None
    if start is not None and end is not None and start >= end:
        raise ValueError
    return query.strip().casefold(), frozenset(map(int, authors)), start, end


class ChatHistory:
    def __init__(self, message, bot_id, store, settings, events):
        self.message = message
        self.bot_id = bot_id
        self.store = store
        self.settings = settings
        self.events = events
        self.scope = Scope(message.guild.id if message.guild else None,
                           message.channel.id, message.author.id)
        self.tool = LocalTool(_SCHEMA, self.run, self.validate)
        self._cursors = {}
        self._returned = {}
        self._chars_left = 9000
        self._seconds_left = _TOTAL_LOOKUP_SECONDS

    @property
    def used(self):
        return bool(self._returned)

    def enabled(self):
        return (self.store.chat_log_enabled(self.scope)
                and self.store.memory_mode(self.scope) in {"normal", "read_only"})

    async def _accessible_channel(self):
        channel = self.message.channel
        if self.scope.guild_id is None:
            if (channel.type == discord.ChannelType.private
                    and getattr(getattr(channel, "recipient", None), "id", None) == self.scope.user_id):
                return channel
            return None
        if channel.type not in _GUILD_TYPES:
            return None
        guild = self.message.guild
        channel = guild.get_channel_or_thread(self.scope.channel_id)
        if channel is None or guild.unavailable:
            return None
        # Member caching is optional in the production client.
        requester = await guild.fetch_member(self.scope.user_id)
        bot = guild.me
        if bot is None:
            return None
        permissions = [channel.permissions_for(member) for member in (requester, bot)]
        if not all(p.view_channel and p.read_message_history for p in permissions):
            return None
        if channel.type == discord.ChannelType.private_thread:
            participants = {member.id for member in await channel.fetch_members()}
            if not all(p.manage_threads or member_id in participants
                       for p, member_id in zip(permissions, (requester.id, bot.id), strict=True)):
                return None
        return channel

    def _eligible(self, old, bounds, *, direct_only):
        if old.author.bot or old.webhook_id is not None or not old.content.strip():
            return False
        if self.scope.guild_id is None and old.author.id != self.scope.user_id:
            return False
        floor, installed = bounds
        at = old.created_at.timestamp()
        if at <= floor:
            return False
        if at <= installed:
            author_scope = Scope(self.scope.guild_id, self.scope.channel_id, old.author.id)
            if not self.store.has_retained_history_message(author_scope, old.id):
                return False
        return not direct_only or trigger_text(
            old, self.bot_id, self.settings.dm_always_reply, self.settings.call_prefixes,
        ) is not None

    def _bounds(self, author_id):
        if author_id == self.scope.user_id:
            return self.store.chat_history_bounds(self.scope)
        author_scope = Scope(self.scope.guild_id, self.scope.channel_id, author_id)
        floor, installed = self.store.chat_history_bounds(author_scope)
        return max(floor, self.store.chat_history_bounds(self.scope)[0]), installed

    def _direct_only(self):
        return (strict_policy(self.settings.external_context_policy)
                or capture_mode(self.store, self.scope) == "direct")

    async def validate(self):
        if not self.used:
            return True
        try:
            async with asyncio.timeout(2):
                if not self.enabled() or await self._accessible_channel() is None:
                    return False
        except (TimeoutError, discord.HTTPException):
            return False
        direct_only = self._direct_only()
        return self.enabled() and all(
            self._eligible(message, self._bounds(message.author.id), direct_only=direct_only)
            for message in self._returned.values()
        )

    async def run(self, arguments):
        started = monotonic()
        scanned = 0
        records = []
        status = "invalid_arguments"
        try:
            filters = _filters(arguments)
            upper = self.message.id
            if "cursor" in arguments:
                cursor = arguments["cursor"]
                if not isinstance(cursor, str) or self._cursors.get(cursor, (None,))[0] != filters:
                    raise ValueError
                upper = self._cursors.pop(cursor)[1]
        except (ValueError, OverflowError):
            result = {"status": status}
        else:
            try:
                async with asyncio.timeout(min(_LOOKUP_SECONDS, self._seconds_left)):
                    result, scanned, records = await self._lookup(filters, upper)
            except (TimeoutError, discord.HTTPException):
                result = {"status": "source_unavailable"}
        finally:
            elapsed = monotonic() - started
            self._seconds_left = max(0, self._seconds_left - elapsed)
        self.events.emit(
            "history.lookup", outcome=result["status"], scanned=scanned, returned=len(records),
            chars=sum(len(row["content"]) for row in records), elapsed_ms=round(elapsed * 1000),
        )
        return result

    async def _lookup(self, filters, upper):
        if not self.enabled():
            return {"status": "history_disabled"}, 0, []
        if not self._chars_left or not self._seconds_left:
            return {"status": "lookup_budget"}, 0, []
        channel_type = self.message.channel.type
        if channel_type not in _GUILD_TYPES | {discord.ChannelType.private}:
            return {"status": "unsupported_scope"}, 0, []
        channel = await self._accessible_channel()
        if channel is None:
            return {"status": "permission_denied"}, 0, []

        query, authors, start, end = filters
        if end is not None:
            upper = min(upper, _first_snowflake_at(end))
        after = discord.Object(id=_first_snowflake_at(start) - 1) if start else None
        if after is not None and after.id >= upper - 1:
            return {"status": "empty", "has_more": False}, 0, []

        inspected, candidates = 0, []
        direct_only = self._direct_only()
        bounds = {}
        async for old in channel.history(limit=_SCAN_LIMIT, before=discord.Object(id=upper),
                                         after=after, oldest_first=False):
            inspected += 1
            upper = old.id
            if old.id in self._returned or (authors and old.author.id not in authors):
                continue
            if old.author.id not in bounds:
                bounds[old.author.id] = self._bounds(old.author.id)
            if not self._eligible(old, bounds[old.author.id], direct_only=direct_only):
                continue
            triggered = trigger_text(
                old, self.bot_id, self.settings.dm_always_reply, self.settings.call_prefixes,
            )
            visible = observable_discord_text(old.content, triggered_text=triggered)
            if visible and query in visible.casefold():
                candidates.append((old, visible))
                if len(candidates) == _RESULT_LIMIT:
                    break
        if await self._accessible_channel() is None or not self.enabled():
            return {"status": "permission_denied"}, inspected, []

        direct_only = self._direct_only()
        records, pending = [], {}
        chars_left = min(4500, self._chars_left)
        more = inspected == _SCAN_LIMIT or len(candidates) == _RESULT_LIMIT
        for old, visible in candidates:
            if not self._eligible(old, self._bounds(old.author.id), direct_only=direct_only):
                continue
            if not chars_left:
                more = True
                upper = old.id + 1
                break
            content = visible[:min(1500, chars_left)]
            chars_left -= len(content)
            self._chars_left -= len(content)
            pending[old.id] = old
            records.append({
                "author_user_id": str(old.author.id), "author": old.author.display_name[:80],
                "message_id": str(old.id), "at": old.created_at.isoformat(),
                "content": content, "truncated": len(content) < len(visible),
            })
        self._returned.update(pending)
        result = {
            "status": "partial" if more or any(row["truncated"] for row in records)
                      else "ok" if records else "empty",
            "messages": records, "has_more": more,
        }
        if more and self._chars_left:
            cursor = uuid4().hex
            self._cursors[cursor] = (filters, upper)
            result["next_cursor"] = cursor
        return result, inspected, records
