"""Curated application emoji registry and owner-only message commands."""
import asyncio
import logging
import re
import time

import discord

log = logging.getLogger("hina")


class EmojiRegistry:
    def __init__(self, client, store):
        self.client, self.store = client, store
        self.lock = asyncio.Lock()
        self.live = {}
        self.checked_at = 0.0

    async def refresh(self):
        self.live = {str(e.id): e for e in await self.client.fetch_application_emojis()}
        self.checked_at = time.monotonic()

    async def catalog(self, channel=None):
        async with self.lock:
            rows = self.store.emoji_rows()
            if any(r["source_guild_id"] is None for r in rows) and time.monotonic() - self.checked_at > 300:
                try:
                    await self.refresh()
                except discord.HTTPException:
                    # Do not fall back to unregistered guild emoji or stale app IDs.
                    self.live = {}
                    self.checked_at = time.monotonic()
            catalog = []
            for row in rows:
                if row["source_guild_id"] is None:
                    emoji = self.live.get(row["emoji_id"])
                else:
                    emoji = self.client.get_emoji(int(row["emoji_id"]))
                    if emoji is None or emoji.guild is None or emoji.guild.me is None or not emoji.is_usable():
                        continue
                    target = getattr(channel, "guild", None)
                    if (target is not None and target.id != int(row["source_guild_id"])
                            and (target.me is None
                                 or not channel.permissions_for(target.me).external_emojis)):
                        continue
                if emoji is not None:
                    catalog.append({"name": row["alias"], "description": row["description"],
                                    "id": row["emoji_id"], "markup": str(emoji)})
            return catalog

    async def add(self, alias, description, attachment=None, source=None):
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", alias):
            raise ValueError("별칭은 소문자로 시작하는 영문·숫자·밑줄 2~32자로 입력해 주세요.")
        description = description.strip()
        if not 1 <= len(description) <= 100:
            raise ValueError("사용 상황을 1~100자로 입력해 주세요.")
        if (attachment is None) == (source is None):
            raise ValueError("source의 기존 이모지 또는 image 파일 중 하나만 지정해 주세요.")
        if attachment is not None and attachment.size > 256 * 1024:
            raise ValueError("이미지는 256 KiB 이하여야 해요.")
        async with self.lock:
            rows = self.store.emoji_rows()
            if any(r["alias"] == alias for r in rows):
                raise ValueError("이미 등록된 별칭이에요. 설명 변경은 리오야 /이모지 수정를 사용해 주세요.")
            if len(rows) >= 20:
                raise ValueError("최대 20개까지 등록할 수 있어요. 먼저 하나를 목록에서 제외해 주세요.")
            if source is not None:
                match = re.fullmatch(r"(?:<a?:[^:<>\s]{1,32}:([0-9]{1,20})>|([0-9]{1,20}))", source.strip())
                emoji = self.client.get_emoji(int(match[1] or match[2])) if match else None
                if emoji is None or emoji.guild is None or emoji.guild.me is None or not emoji.is_usable():
                    raise ValueError("봇이 사용할 수 있는 서버 이모지 또는 해당 ID를 지정해 주세요.")
                if any(r["emoji_id"] == str(emoji.id) for r in rows):
                    raise ValueError("이미 등록된 이모지예요.")
                self.store.add_emoji(alias, str(emoji.id), description, str(emoji.guild.id))
                return str(emoji)
            data = await attachment.read()
            if len(data) > 256 * 1024 or not (
                data.startswith((b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"\xff\xd8\xff"))
                or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
            ):
                raise ValueError("PNG·GIF·JPEG·WebP 이미지 파일을 첨부해 주세요.")
            emoji = await self.client.create_application_emoji(name=alias, image=data)
            try:
                self.store.add_emoji(alias, str(emoji.id), description)
            except Exception:  # Compensate remote creation after local failure.
                try:
                    await emoji.delete()
                except discord.HTTPException:
                    log.warning("Emoji registration rollback failed; inspect Developer Portal")
                raise
            self.live[str(emoji.id)] = emoji
            return str(emoji)

    async def edit(self, alias, description):
        if not 1 <= len(description.strip()) <= 100:
            raise ValueError("사용 상황을 1~100자로 입력해 주세요.")
        async with self.lock:
            if not self.store.edit_emoji(alias, description.strip()):
                raise ValueError("등록되지 않은 별칭이에요.")

    async def remove(self, alias):
        async with self.lock:
            if not self.store.remove_emoji(alias):
                raise ValueError("등록되지 않은 별칭이에요.")


class EmojiCommands:
    HELP = ("리오야 /이모지 등록 별칭 <이모지 또는 ID> 사용 상황\n"
            "리오야 /이모지 등록 별칭 사용 상황 + 이미지 한 개 첨부\n"
            "리오야 /이모지 목록\n리오야 /이모지 수정 별칭 사용 상황\n"
            "리오야 /이모지 삭제 별칭")

    def __init__(self, client):
        self.client = client

    async def handle(self, message, text):
        if message.author.id not in self.client.emoji_admin_ids:
            return "봇 소유자 또는 지정된 관리자만 사용할 수 있어요."
        parts = text.split(maxsplit=1)
        action, rest = (parts[0], parts[1] if len(parts) > 1 else "") if parts else ("", "")
        attachments = list(getattr(message, "attachments", []))
        try:
            if action == "등록":
                args = rest.split(maxsplit=1 if attachments else 2)
                if len(attachments) > 1 or len(args) != (2 if attachments else 3):
                    return self.HELP
                alias = args[0]
                if attachments:
                    if re.match(r"(?:<a?:|[0-9]{1,20}(?:\s|$))", args[1]):
                        return "기존 이모지와 이미지 파일은 동시에 지정할 수 없어요."
                    markup = await self.client.emoji_registry.add(alias, args[1], attachments[0])
                else:
                    markup = await self.client.emoji_registry.add(alias, args[2], source=args[1])
                return f"등록했어요: {markup} `:{alias}:`"
            if attachments:
                return "이미지 첨부는 등록 명령에서만 사용할 수 있어요."
            if action == "목록" and not rest:
                catalog = {e["name"]: e for e in await self.client.emoji_registry.catalog(message.channel)}
                rows = self.client.store.emoji_rows()
                lines = [f"리오 이모지 {len(rows)}/20"]
                for row in rows:
                    preview = catalog.get(row["alias"], {}).get("markup", "(사용 불가)")
                    description = discord.utils.escape_markdown(row["description"])
                    lines.append(f"`:{row['alias']}:` {preview} — {description}")
                return "\n".join(lines) if rows else "등록된 이모지가 없어요.\n" + self.HELP
            if action == "수정":
                args = rest.split(maxsplit=1)
                if len(args) != 2:
                    return self.HELP
                await self.client.emoji_registry.edit(*args)
                return "사용 상황을 변경했어요."
            if action == "삭제" and rest and len(rest.split()) == 1:
                await self.client.emoji_registry.remove(rest)
                return "사용 목록에서 제외했어요. 원본 이모지는 삭제하지 않아요."
            return self.HELP
        except ValueError as exc:
            return str(exc)
        except Exception as exc:  # noqa: BLE001 - keep credentials and remote payloads out of replies
            log.warning("Emoji command failed (%s)", type(exc).__name__)
            return "처리하지 못했어요. 잠시 후 다시 시도해 주세요."
