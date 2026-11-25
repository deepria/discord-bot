import asyncio
import logging
import re
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from time import monotonic

import discord

from rio_bot.ai.chat_history_context import CURRENT_CHAT_HISTORY
from rio_bot.ai.dm_memory_recall import CURRENT_PRIVATE_DM
from rio_bot.ai.egress_policy import strict_policy
from rio_bot.ai.forward_context import CURRENT_FORWARD_CONTEXT
from rio_bot.ai.public_dm_context import CURRENT_PUBLIC_DM_LOOKUP, PublicDMContext
from rio_bot.ai.vision import CURRENT_VISUAL_INPUTS
from rio_bot.core.message_provenance import split_inline_quotes

from .bot import RioClient as BaseRioClient
from .chat_history import ChatHistory
from .chat_llm import LLM
from .chatlog_capture import capture_mode
from .chatlog_capture_commands import install_chatlog_capture
from .config import Settings
from .forward_context import forward_context_rows, is_forward
from .reply_context import REPLY_CONTEXT, reply_context_rows, resolve_reply_target
from .routing import Scope, trigger_text
from .slash_commands import install_slash_commands
from .target_context import TARGET_CONTEXT, collect
from .target_recent import CURRENT_DIRECT_TRIGGER, TargetAwareRecentMessages
from .vision import VisionLimits, collect_visual_inputs

log = logging.getLogger("rio")

CURRENT_PUBLIC_CONTEXT_REQUEST = ContextVar(
    "current_public_context_request",
    default=(False, ()),
)
_PUBLIC_MEMORY_QUERY = re.compile(
    r"(?:기억(?:나|해|하고)|전에|저번|지난번|예전에|다른\s*(?:채널|방)|"
    r"서버(?:에서|의)|평소|원래|(?:말|얘기)했|어떤\s*(?:사람|애|유저)|"
    r"성격|인상|평판|어떻게\s*생각)",
    re.IGNORECASE,
)
_BROAD_SERVER_MEMORY_QUERY = re.compile(
    r"(?:서버(?:에서|의).*(?:누가|누구|사람들|다른\s*사람)|"
    r"누가.*(?:말했|얘기했))",
    re.IGNORECASE,
)
_PUBLIC_DM_TIMEOUT = 2.0
_PUBLIC_DM_SOURCES = 4
_PUBLIC_DM_CHARS = 4000


def _augment_empty_call(content: str, text: str | None, has_visuals: bool) -> str | None:
    """Only add intent when a bare trigger actually carries a visual attachment."""
    if text is None or text or not has_visuals:
        return None
    return (content + " 이 이미지나 스티커를 봐줘.").strip()


def _public_context_request(scope: Scope, text: str, sampled: list[dict]):
    """Choose whether cross-channel public memory is relevant to this invocation.

    Ordinary chat should not receive unrelated users' summaries. Explicitly targeted user questions
    may use that target's public memory, while history/memory questions without a target default to
    the current user's own public calls. Only clearly broad server-history questions may fan out.
    """
    target_ids = tuple({
        int(item["user_id"])
        for item in sampled
        if str(item.get("user_id", "")).isdigit()
    })
    if target_ids:
        return True, target_ids
    if not _PUBLIC_MEMORY_QUERY.search(text):
        return False, ()
    if scope.guild_id is not None and _BROAD_SERVER_MEMORY_QUERY.search(text):
        return True, None
    return True, (scope.user_id,)


class RioClient(BaseRioClient):
    """Production Discord client wired to current context, web search, and vision."""

    def __init__(self, settings: Settings, *, store=None, llm=None):
        if llm is None:
            llm = LLM(settings)
        super().__init__(settings, store=store, llm=llm)
        self.recent = TargetAwareRecentMessages(
            budget=settings.channel_context_chars,
            store=self.store,
        )
        self.vision_limits = VisionLimits.from_settings(settings)
        install_slash_commands(self)
        install_chatlog_capture(self)

    @staticmethod
    def _management_text(text):
        return False

    async def command(self, message, scope, text):
        return None

    async def public_sources(self, user_id: int, guild_id: int | None = None):
        enabled, requested_ids = CURRENT_PUBLIC_CONTEXT_REQUEST.get()
        if not enabled or guild_id is None:
            return []

        requested = None if requested_ids is None else set(requested_ids)
        allowed, members = [], {}
        for source in self.store.public_candidates(user_id, guild_id):
            if requested is not None and source.user_id not in requested:
                continue
            guild, channel, _ = self._public_channel(source)
            if channel is None:
                continue
            if source.guild_id not in members:
                try:
                    # Access is checked for the current caller, not for the source-message author.
                    members[source.guild_id] = await guild.fetch_member(user_id)
                except discord.HTTPException:
                    members[source.guild_id] = None
            member = members[source.guild_id]
            if member is None:
                continue
            permissions = channel.permissions_for(member)
            if not (permissions.view_channel and permissions.read_message_history):
                continue
            allowed.append(source)
            if len(allowed) == 4:
                break
        return allowed

    def _public_channel(self, source):
        if self.settings.allowed_guild_ids and source.guild_id not in self.settings.allowed_guild_ids:
            return None, None, "guild_not_allowed"
        guild = self.get_guild(source.guild_id)
        if guild is None or guild.unavailable:
            return None, None, "guild_unavailable"
        channel = guild.get_channel(source.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return guild, None, "not_text_channel"
        public = channel.permissions_for(guild.default_role)
        if not (public.view_channel and public.read_message_history):
            return guild, None, "not_public"
        return guild, channel, ""

    def _public_dm_enabled(self, scope):
        return (scope.guild_id is None and self.settings.public_memory_in_dm
                and self.store.memory_mode(scope) in {"normal", "read_only"})

    async def public_dm_context(self, scope, excluded_ids, *, explicit_summary):
        started = monotonic()
        excluded = {}
        result = PublicDMContext()
        try:
            async with asyncio.timeout(_PUBLIC_DM_TIMEOUT):
                if self._public_dm_enabled(scope):
                    result = await self._read_public_dm(scope, excluded_ids, explicit_summary, excluded)
                else:
                    excluded["disabled"] = 1
        except Exception as exc:  # noqa: BLE001 - keep the answer path and redact content.
            excluded["timeout" if isinstance(exc, TimeoutError) else "lookup_error"] = 1
        result.telemetry = {
            "public_dm_sources": len({row["source"] for row in result.recent}),
            "public_dm_turns": len(result.recent),
            "public_dm_summary_sources": len(result.summaries),
            "public_dm_excluded": excluded,
            "public_dm_lookup_ms": round((monotonic() - started) * 1000),
        }
        return result

    async def _read_public_dm(self, scope, excluded_ids, explicit_summary, excluded):
        def exclude(reason):
            excluded[reason] = excluded.get(reason, 0) + 1

        now = datetime.now(UTC)
        window = {
            "since": (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
            "until": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
        recent_sources = self.store.recent_public_sources(scope.user_id, **window)
        summary_sources = (
            self.store.public_candidates(scope.user_id) if explicit_summary else []
        )
        candidates = dict.fromkeys(recent_sources + summary_sources)
        members, verified, summary_allowed = {}, {}, []
        for source in candidates:
            want_recent = source in recent_sources and len(verified) < _PUBLIC_DM_SOURCES
            want_summary = source in summary_sources and len(summary_allowed) < _PUBLIC_DM_SOURCES
            if not (want_recent or want_summary):
                continue
            guild, channel, reason = self._public_channel(source)
            if source.user_id != scope.user_id or channel is None:
                exclude(reason or "wrong_owner")
                continue
            if not self.store.chat_log_enabled(source):
                exclude("chat_log_off")
                continue
            if guild.id not in members:
                try:
                    members[guild.id] = await guild.fetch_member(scope.user_id)
                except discord.NotFound:
                    members[guild.id] = None
                except discord.HTTPException:
                    exclude("member_lookup_failed")
                    continue
            member = members[guild.id]
            if member is None or member.id != scope.user_id:
                exclude("not_member")
                continue
            permissions = channel.permissions_for(member)
            if not (permissions.view_channel and permissions.read_message_history):
                exclude("no_read_access")
                continue
            if want_recent:
                rows = self.store.recent_public_turns(source, scope.user_id, **window)
                ids = set()
                for row in rows:
                    message_id = row["message_id"]
                    if message_id in excluded_ids:
                        exclude("duplicate")
                        continue
                    try:
                        original = await channel.fetch_message(int(message_id))
                    except discord.NotFound:
                        exclude("message_missing")
                        continue
                    except discord.HTTPException:
                        exclude("message_lookup_failed")
                        continue
                    if original.author.id != scope.user_id:
                        exclude("wrong_owner")
                        continue
                    ids.add(message_id)
                if ids:
                    verified[source] = ids
            if want_summary and self.store.shared_summary(source)[0]:
                summary_allowed.append(source)

        if not self._public_dm_enabled(scope):
            exclude("disabled")
            return PublicDMContext()

        # Recheck policy, cached permissions and the database after all Discord awaits.
        # A reset or policy change during the lookup must win before any text leaves the bot.
        window["since"] = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        recent, summaries = [], []
        for source in dict.fromkeys([*verified, *summary_allowed]):
            guild, channel, reason = self._public_channel(source)
            if channel is None or not self.store.chat_log_enabled(source):
                exclude(reason or "chat_log_off")
                continue
            permissions = channel.permissions_for(members[guild.id])
            if not (permissions.view_channel and permissions.read_message_history):
                exclude("no_read_access")
                continue
            if source in verified:
                for row in self.store.recent_public_turns(source, scope.user_id, **window):
                    if row["message_id"] in verified[source]:
                        recent.append({
                            "source": source.conversation, "user_id": str(scope.user_id),
                            "message_id": row["message_id"], "at": row["created_at"],
                            "user": row["content"],
                            **({"rio": row["reply"]} if row["reply"] else {}),
                        })
            if source in summary_allowed:
                summary, _ = self.store.shared_summary(source)
                if summary:
                    summaries.append({"source": source.conversation, "user_id": str(scope.user_id),
                                      "summary": summary})

        chosen, seen, used = [], set(excluded_ids), 0
        for row in sorted(recent, key=lambda row: (row["at"], int(row["message_id"])), reverse=True):
            if row["message_id"] in seen:
                exclude("duplicate")
                continue
            size = len(row["user"]) + len(row.get("rio", ""))
            if used + size > _PUBLIC_DM_CHARS:
                exclude("character_budget")
                continue
            seen.add(row["message_id"])
            chosen.append(row)
            used += size
        return PublicDMContext(recent=list(reversed(chosen)), summaries=summaries)

    async def hydrate_recent_history(self, message, scope):
        """Backfill the bounded history while respecting the active capture policy."""
        if scope.guild_id is None or not self.recent.needs_hydration(scope):
            return
        created_at = getattr(message, "created_at", None)
        history = getattr(message.channel, "history", None)
        if created_at is None or history is None:
            self.recent.mark_hydrated(scope)
            return

        after = created_at - timedelta(seconds=self.recent.ttl)
        policy = capture_mode(self.store, scope)
        try:
            async for old in history(
                limit=self.recent.limit,
                before=message,
                after=after,
                oldest_first=False,
            ):
                if old.webhook_id is not None:
                    continue
                own_bot = self.user is not None and old.author.id == self.user.id
                # Discord history does not reliably identify who an old Rio message answered.
                # Such rows would fail closed later anyway and can evict useful user messages from
                # the bounded buffer, so do not hydrate them at all.
                if own_bot:
                    continue
                other_bot = bool(old.author.bot)
                historical_text = trigger_text(
                    old,
                    self.user.id,
                    self.settings.dm_always_reply,
                    self.settings.call_prefixes,
                )
                if policy == "direct" and historical_text is None:
                    continue
                if self._management_text(historical_text) or not old.content:
                    continue
                historical_scope = Scope(
                    scope.guild_id,
                    scope.channel_id,
                    old.author.id,
                    scope.public_at_capture,
                )
                direct_token = CURRENT_DIRECT_TRIGGER.set(historical_text is not None)
                try:
                    self.recent.add(
                        historical_scope,
                        old.id,
                        old.author.display_name,
                        old.content,
                        role="bot" if other_bot else "user",
                        unix_time=old.created_at.timestamp(),
                        direct_trigger=historical_text is not None,
                    )
                finally:
                    CURRENT_DIRECT_TRIGGER.reset(direct_token)
        except discord.HTTPException as exc:
            log.warning("Recent channel history backfill failed (%s)", type(exc).__name__)
            return
        self.recent.mark_hydrated(scope)

    async def on_message(self, message):
        if self.user is None:
            return await super().on_message(message)
        text = self.message_trigger(message)
        scope = Scope(
            message.guild.id if message.guild else None,
            message.channel.id,
            message.author.id,
        )

        # Other bots never trigger Rio, but in `capture=all` their visible channel messages are
        # useful conversational context just like human side chatter. `capture=direct` keeps its
        # stricter privacy/attention boundary and omits them unless the current user explicitly
        # replies to one, which is handled below as request-scoped reply context.
        own_bot = message.author.id == self.user.id
        if message.author.bot and not own_bot:
            if (
                message.webhook_id is None
                and scope.guild_id is not None
                and self.store.chat_log_enabled(scope)
                and capture_mode(self.store, scope) == "all"
                and message.content
            ):
                self.recent.add(
                    scope,
                    message.id,
                    message.author.display_name,
                    message.content,
                    role="bot",
                    direct_trigger=False,
                )
            return

        direct_only = scope.guild_id is not None and capture_mode(self.store, scope) == "direct"
        sampled = []
        replied = []
        reply_author_id = None
        visuals = []
        forwarded = []
        if text is not None:
            try:
                reply_target = await resolve_reply_target(message)
                if getattr(message, "type", discord.MessageType.reply) == discord.MessageType.reply:
                    reply_author_id = getattr(getattr(reply_target, "author", None), "id", None)
                replied = reply_context_rows(reply_target, self.user.id)
                forwarded = await forward_context_rows(message, message, "current_forward")
                if is_forward(reply_target) and (
                    reply_author_id == scope.user_id
                    or (
                        not direct_only and not strict_policy(self.settings.external_context_policy)
                    )
                    or trigger_text(
                        reply_target, self.user.id, self.settings.dm_always_reply,
                        self.settings.call_prefixes,
                    ) is not None
                ):
                    forwarded.extend(await forward_context_rows(
                        reply_target, message, "explicit_reply", limit=2 - len(forwarded),
                    ))
                sampled = await collect(
                    message,
                    self.user.id,
                    text,
                    direct_only=direct_only,
                    call_prefixes=self.settings.call_prefixes,
                )
                visuals = await collect_visual_inputs(
                    message,
                    limits=self.vision_limits,
                    include_reply=True,
                    include_recent=scope.guild_id is not None and bool(text),
                    allowed_reply_author_id=scope.user_id,
                    allowed_context_author_id=scope.user_id,
                    recent_filter=lambda old: (
                        getattr(getattr(old, "author", None), "id", None) == self.user.id
                        or trigger_text(
                            old,
                            self.user.id,
                            self.settings.dm_always_reply,
                            self.settings.call_prefixes,
                        ) is not None
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - request-scoped context must fail open.
                log.warning("Request context collection skipped (%s)", type(exc).__name__)
        public_request = (
            _public_context_request(scope, text, sampled)
            if text is not None
            else (False, ())
        )
        target_token = TARGET_CONTEXT.set(tuple(sampled))
        reply_token = REPLY_CONTEXT.set(tuple(replied))
        forward_token = CURRENT_FORWARD_CONTEXT.set(tuple(forwarded))
        visual_token = CURRENT_VISUAL_INPUTS.set(tuple(visuals))
        public_token = CURRENT_PUBLIC_CONTEXT_REQUEST.set(public_request)
        dm_lookup = None
        private_dm = (
            scope.guild_id is None
            and getattr(message.channel, "type", None) == discord.ChannelType.private
        )
        if private_dm:
            authored, _ = split_inline_quotes(text or "")
            explicit_summary = bool(_PUBLIC_MEMORY_QUERY.search(authored))

            async def dm_lookup(request_scope, excluded_ids):
                if request_scope.conversation != scope.conversation:
                    return PublicDMContext()
                return await self.public_dm_context(
                    request_scope, excluded_ids, explicit_summary=explicit_summary,
                )

        dm_token = CURRENT_PUBLIC_DM_LOOKUP.set(dm_lookup)
        private_dm_token = CURRENT_PRIVATE_DM.set(scope.conversation if private_dm else None)
        history = ChatHistory(message, self.user.id, self.store, self.settings, self.events)
        history_token = CURRENT_CHAT_HISTORY.set(history)

        # Forward evidence already reaches the model separately from the user's own words.
        # A bare call with only visuals uses the existing visual prompt.
        original_content = None
        augmented_content = _augment_empty_call(message.content, text, bool(visuals) and not forwarded)
        if augmented_content is not None:
            original_content = message.content
            message.content = augmented_content
        try:
            return await super().on_message(message, reply_author_id=reply_author_id)
        finally:
            if original_content is not None:
                message.content = original_content
            CURRENT_CHAT_HISTORY.reset(history_token)
            CURRENT_PRIVATE_DM.reset(private_dm_token)
            CURRENT_PUBLIC_DM_LOOKUP.reset(dm_token)
            CURRENT_PUBLIC_CONTEXT_REQUEST.reset(public_token)
            CURRENT_VISUAL_INPUTS.reset(visual_token)
            CURRENT_FORWARD_CONTEXT.reset(forward_token)
            REPLY_CONTEXT.reset(reply_token)
            TARGET_CONTEXT.reset(target_token)


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    log.setLevel(logging.INFO)
    try:
        settings = Settings.load()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    bot = RioClient(settings)
    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
