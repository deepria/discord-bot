import asyncio
import logging
import time
import weakref

import discord

from .config import Settings
from .emojis import available_emojis, render_emojis
from .llm import LLM
from .recent import RecentMessages
from .routing import Scope, chunks, trigger_text
from .store import Store

log = logging.getLogger("hina")
HELP = """호출: @봇 멘션, 핑을 켠 답장, 또는 메시지 맨 앞의 `히나야`
관리는 호출 뒤에 아래 문구를 붙여 주세요. Discord 슬래시 명령은 아닙니다.
`/기억` — 현재 채널의 내 요약과 개인 메모 확인
`/메모 내용` — 같은 서버 내 채널에서 공유할 내 메모 교체 (DM은 분리)
`/메모삭제` — 개인 메모만 삭제
`/기억삭제 확인` — 현재 서버의 모든 채널에서 내 대화·요약·메모 삭제 (DM에서는 내 DM 기억)
`/서버기억` — 서버 공통 메모 확인
`/서버메모 내용` / `/서버메모삭제` — 서버 관리 권한으로 공통 메모 관리
공개 서버에서 같은 사용자가 나눈 대화는 DM에서 참고할 수 있어요. DM 기억은 서버로 넘어가지 않아요.
같은 채널의 일반 대화도 최근 문맥으로 잠시 보관하고, 호출 시 OpenAI에 함께 보내요.
공개 채널에서 직접 호출한 발화만 같은 서버의 다른 사용자·채널에서 장기 기억으로 참고해요.
첨부파일·이미지·답장 원문을 읽는 기능은 아직 없어요."""


class HinaClient(discord.Client):
    def __init__(self, settings: Settings, *, store=None, llm=None):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.emojis_and_stickers = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none(),
                         max_messages=None)
        self.settings = settings
        self.store = store or Store(settings.db_path, settings.history_turns)
        self.llm = llm or LLM(settings)
        self.locks = weakref.WeakValueDictionary()
        self.cooldowns = {}
        self.recent = RecentMessages(budget=settings.channel_context_chars)
        self.channel_locks = weakref.WeakValueDictionary()
        self.slots = asyncio.Semaphore(settings.concurrency)
        self.pending_count = 0
        self.active_tasks = set()
        self.stopping = False

    async def on_ready(self):
        log.info("Bot connected (id=%s)", self.user.id)

    async def on_error(self, event, *args, **kwargs):
        # Discord's default handler prints message arguments and full tracebacks.
        log.error("Discord event failed: %s", event)

    async def close(self):
        self.stopping = True
        try:
            if self.active_tasks:
                _, pending = await asyncio.wait(list(self.active_tasks), timeout=50)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        finally:
            await self.llm.close()
            self.store.close()
            await super().close()

    async def send_text(self, channel, text):
        for part in chunks(text):
            await channel.send(part, allowed_mentions=discord.AllowedMentions.none())

    async def command(self, message, scope, text):
        cmd, _, arg = text.partition(" ")
        arg = arg.strip()
        if cmd == "/도움말":
            return HELP
        if cmd == "/기억":
            summary, _ = self.store.summary(scope)
            return ("이 채널에서의 기억:\n" + (summary or "아직 요약된 기억이 없어요.") +
                    "\n\n개인 메모:\n" + (self.store.note(scope.user_note) or "없어요."))
        if cmd == "/메모":
            if not arg or len(arg) > 1500:
                return "`히나야 /메모 내용` 형식으로 1~1500자를 입력해 주세요. 기존 메모를 교체해요."
            self.store.set_note(scope.user_note, arg)
            return "개인 메모를 저장했어요. 서버에서는 같은 서버의 다른 채널에서도 참고해요."
        if cmd == "/메모삭제":
            self.store.set_note(scope.user_note, "")
            return "개인 메모를 삭제했어요."
        if cmd == "/기억삭제":
            if arg != "확인":
                return "현재 서버 또는 DM의 내 기억 전체를 삭제하려면 `히나야 /기억삭제 확인`을 보내 주세요."
            self.store.forget(scope)
            self.recent.forget(scope)
            return "이 서버 또는 DM에서의 대화 기록, 자동 요약, 개인 메모를 삭제했어요."
        if cmd in {"/서버기억", "/서버메모", "/서버메모삭제"}:
            if scope.guild_id is None:
                return "서버에서만 사용할 수 있는 명령이에요."
            if cmd == "/서버기억":
                return self.store.note(scope.realm) or "서버 공통 메모가 없어요."
            if not message.author.guild_permissions.manage_guild:
                return "서버 관리 권한이 필요해요."
            if cmd == "/서버메모":
                if not arg or len(arg) > 1500:
                    return "1~1500자의 서버 공통 메모를 입력해 주세요. 서버 전체에서 참고해요."
                self.store.set_note(scope.realm, arg)
                return "서버 공통 메모를 교체했어요."
            self.store.set_note(scope.realm, "")
            return "서버 공통 메모를 삭제했어요."
        return None

    async def public_sources(self, user_id: int, guild_id: int | None = None):
        """Fail closed; only ordinary public text channels, with live membership checks."""
        if guild_id is None and not self.settings.public_memory_in_dm:
            return []
        allowed, members = [], {}
        for scope in self.store.public_candidates(user_id, guild_id):
            if (self.settings.allowed_guild_ids
                    and scope.guild_id not in self.settings.allowed_guild_ids):
                continue
            guild = self.get_guild(scope.guild_id)
            if guild is None or guild.unavailable:
                continue
            # Exclude all threads, voice chats and forum posts in v1, even public ones.
            channel = guild.get_channel(scope.channel_id)
            if not isinstance(channel, discord.TextChannel):
                continue
            public = channel.permissions_for(guild.default_role)
            if not (public.view_channel and public.read_message_history):
                continue
            if scope.guild_id not in members:
                try:
                    members[scope.guild_id] = await guild.fetch_member(user_id)
                except discord.HTTPException:
                    members[scope.guild_id] = None
            member = members[scope.guild_id]
            if member is None:
                continue
            permissions = channel.permissions_for(member)
            if not (permissions.view_channel and permissions.read_message_history):
                continue
            allowed.append(scope)
            if len(allowed) == 4:
                break
        return allowed

    async def on_message(self, message):
        if self.stopping or not self.user:
            return
        guild_id = message.guild.id if message.guild else None
        if (guild_id is not None and self.settings.allowed_guild_ids
                and guild_id not in self.settings.allowed_guild_ids):
            return
        text = trigger_text(message, self.user.id, self.settings.dm_always_reply)
        if self.pending_count >= 100:
            return
        public_at_capture = False
        if guild_id is not None and isinstance(message.channel, discord.TextChannel):
            permissions = message.channel.permissions_for(message.guild.default_role)
            public_at_capture = permissions.view_channel and permissions.read_message_history
        scope = Scope(guild_id, message.channel.id, message.author.id, public_at_capture)
        if message.author.bot or message.webhook_id is not None:
            return
        # Management commands must not enter the shared channel buffer.
        management = text is not None and text.startswith((
            "/도움말", "/기억", "/메모", "/서버기억", "/서버메모"))
        if guild_id is not None and not management:
            self.recent.add(scope, message.id, message.author.display_name, message.content)
        if text is None:
            return
        channel_key = (scope.realm, scope.channel_id)
        channel_lock = self.channel_locks.get(channel_key)
        if channel_lock is None:
            channel_lock = asyncio.Lock()
            self.channel_locks[channel_key] = channel_lock
        # One lock per realm+user, including all channels, so /기억삭제 cannot race a response.
        key = scope.user_note
        lock = self.locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[key] = lock
        self.pending_count += 1
        task = asyncio.current_task()
        self.active_tasks.add(task)
        try:
            async with channel_lock, lock:
                if self.store.seen(message.id):
                    return
                if len(text) > 4000:
                    await self.send_text(message.channel, "한 번에 4000자 이내로 이야기해 주세요.")
                    return
                command_reply = await self.command(message, scope, text)
                if command_reply is not None:
                    await self.send_text(message.channel, command_reply)
                    return
                now = time.monotonic()
                self.cooldowns = {k: v for k, v in self.cooldowns.items()
                                  if now - v < self.settings.cooldown}
                if key in self.cooldowns:
                    return
                self.cooldowns[key] = now
                if not text:
                    await self.send_text(message.channel, "응, 선생님. 무슨 일이야?")
                    return
                async with self.slots:
                    async with message.channel.typing():
                        sources = await self.public_sources(scope.user_id, guild_id)
                        context = self.store.public_context(sources)
                        emoji_catalog = available_emojis(message.guild)
                        answer = await self.llm.answer(
                            self.store, scope, message.author.display_name, text,
                            public_context=context,
                            channel_context=self.recent.context(scope, message.id),
                            emoji_catalog=emoji_catalog)
                        current = {e["id"] for e in available_emojis(message.guild)}
                        answer = render_emojis(answer, [e for e in emoji_catalog if e["id"] in current])
                        if not answer:
                            answer = "응, 선생님."
                        sent = await message.channel.send(
                            next(chunks(answer)), allowed_mentions=discord.AllowedMentions.none())
                        for part in list(chunks(answer))[1:]:
                            await message.channel.send(part, allowed_mentions=discord.AllowedMentions.none())
                        if guild_id is not None:
                            self.recent.add(scope, sent.id, "히나", answer, role="assistant")
                    # Commit only after Discord delivery. Never memorize a failed model request.
                    self.store.add(scope, message.id, text, answer)
                    self.store.add_shared_call(scope, message.id, message.author.display_name, text)
                    for summarize in (self.llm.summarize, self.llm.summarize_shared):
                        try:
                            await summarize(self.store, scope)
                        except Exception as exc:  # noqa: BLE001 - isolate summary failures; redact logs
                            log.warning("Memory summary deferred (%s)", type(exc).__name__)
        except discord.HTTPException as exc:
            log.warning("Discord delivery failed (%s)", type(exc).__name__)
        except Exception as exc:  # noqa: BLE001 - isolate event/summary failures; redact logs
            log.warning("Conversation failed (%s)", type(exc).__name__)
            try:
                await self.send_text(message.channel, "지금은 답변을 이어가기 어렵네요. 잠시 후 다시 불러 주세요.")
            except discord.HTTPException:
                pass
        finally:
            self.active_tasks.discard(task)
            self.pending_count -= 1


def main():
    # Do not log SDK request bodies, prompts, credentials, or Discord message content.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    log.setLevel(logging.INFO)
    try:
        settings = Settings.load()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    bot = HinaClient(settings)
    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
