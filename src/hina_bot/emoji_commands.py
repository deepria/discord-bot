"""Curated application emoji registry and owner-only slash commands."""
import asyncio
import logging
import re
import time

import discord
from discord import app_commands

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
                raise ValueError("이미 등록된 별칭이에요. 설명 변경은 /emoji edit를 사용해 주세요.")
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


class EmojiCommands(app_commands.Group):
    def __init__(self, client):
        super().__init__(name="emoji", description="히나가 사용할 이모지 관리 (봇 관리자 전용)")
        self.client = client

    async def interaction_check(self, interaction):
        if interaction.user.id not in self.client.emoji_admin_ids:
            await interaction.response.send_message("봇 소유자 또는 지정된 관리자만 사용할 수 있어요.",
                                                    ephemeral=True)
            return False
        return True

    async def on_error(self, interaction, error):
        original = getattr(error, "original", error)
        log.warning("Emoji command failed (%s)", type(original).__name__)
        text = str(original) if isinstance(original, ValueError) else "처리하지 못했어요. 잠시 후 다시 시도해 주세요."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="add", description="기존 이모지 또는 이미지를 등록하고 별칭·의미 지정")
    @app_commands.describe(alias="예: hina_happy", description="예: 기쁘거나 칭찬받았을 때",
                           image="PNG·GIF·JPEG·WebP, 최대 256 KiB", source="기존 서버 이모지 또는 이모지 ID")
    async def add(self, interaction: discord.Interaction, alias: str, description: str,
                  image: discord.Attachment | None = None, source: str | None = None):
        await interaction.response.defer(ephemeral=True)
        markup = await self.client.emoji_registry.add(alias, description, image, source)
        await interaction.followup.send(f"등록했어요: {markup} `:{alias}:`", ephemeral=True)

    @app_commands.command(name="list", description="등록한 이모지와 사용 상황 확인")
    async def list_emojis(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        catalog = {e["name"]: e for e in await self.client.emoji_registry.catalog()}
        rows = self.client.store.emoji_rows()
        if not rows:
            await interaction.followup.send("등록된 이모지가 없어요. /emoji add로 추가해 주세요.", ephemeral=True)
            return
        embed = discord.Embed(title=f"히나 이모지 {len(rows)}/20")
        for row in rows:
            live = catalog.get(row["alias"])
            preview = live["markup"] if live else "(사용 불가)"
            embed.add_field(name=f":{row['alias']}:", value=f"{preview} {row['description']}", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="edit", description="등록한 별칭의 사용 상황 변경")
    async def edit(self, interaction: discord.Interaction, alias: str, description: str):
        await interaction.response.defer(ephemeral=True)
        await self.client.emoji_registry.edit(alias, description)
        await interaction.followup.send("사용 상황을 변경했어요.", ephemeral=True)

    @app_commands.command(name="remove", description="히나가 사용하는 목록에서 제외 (원본 이미지는 유지)")
    async def remove(self, interaction: discord.Interaction, alias: str):
        await interaction.response.defer(ephemeral=True)
        await self.client.emoji_registry.remove(alias)
        await interaction.followup.send("사용 목록에서 제외했어요. 원본 서버 또는 앱의 이모지는 삭제하지 않아요.", ephemeral=True)
