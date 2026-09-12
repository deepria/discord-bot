import logging
from enum import Enum

import discord
from discord import app_commands

from .runtime_knowledge import RuntimeKnowledgeRegistry

log = logging.getLogger("hina")


class Awareness(str, Enum):
    SELF = "self"
    DIRECT_EXPERIENCE = "direct_experience"
    REPORTED = "reported"
    PUBLIC_KNOWLEDGE = "public_knowledge"
    INFERENCE = "inference"
    AUDIENCE_ONLY = "audience_only"
    UNKNOWN = "unknown"


class _KnowledgeGroup(app_commands.Group):
    label = "knowledge"

    async def interaction_check(self, interaction):
        if interaction.user.id not in self.client.emoji_admin_ids:
            await interaction.response.send_message(
                "봇 소유자 또는 지정된 관리자만 사용할 수 있어요.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction, error):
        log.warning("%s command failed (%s)", self.label, type(error).__name__)
        text = f"{self.label} 항목을 처리하지 못했어요. /{self.label} list로 확인해 주세요."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    async def _add(self, interaction, identifier, content, keywords, subjects, awareness, timeline):
        try:
            self.registry.add(identifier, content, keywords, subjects, awareness.value, timeline)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            f"{self.label} `{identifier}`를 추가하고 활성화했어요. 다음 응답부터 반영돼요.",
            ephemeral=True)

    async def _list(self, interaction):
        try:
            rows = self.registry.list()
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if not rows:
            await interaction.response.send_message(f"등록된 {self.label} 항목이 없어요.", ephemeral=True)
            return
        lines = [f"{self.label} {len(rows)}/100"]
        for row in rows[:18]:
            state = "ON" if row["enabled"] else "OFF"
            preview = discord.utils.escape_markdown(row["content"].replace("\n", " "))
            if len(preview) > 90:
                preview = preview[:87] + "..."
            lines.append(f"`{row['id']}` [{state}] — {preview}")
        if len(rows) > 18:
            lines.append(f"… 외 {len(rows) - 18}개")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    async def _show(self, interaction, identifier):
        try:
            row = self.registry.get(identifier)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        state = "ON" if row["enabled"] else "OFF"
        text = (
            f"`{row['id']}` [{state}]\n"
            f"awareness: `{row['awareness']}`\n"
            f"timeline: {row['timeline']}\n"
            f"subjects: {', '.join(row['subjects'])}\n"
            f"keywords: {', '.join(row['keywords'])}\n\n"
            f"{row['content']}"
        )
        await interaction.response.send_message(text[:1900], ephemeral=True)

    async def _edit(self, interaction, identifier, content, keywords, subjects, awareness, timeline):
        try:
            self.registry.edit(
                identifier,
                content=content,
                keywords=keywords,
                subjects=subjects,
                awareness=awareness,
                timeline=timeline,
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            f"{self.label} `{identifier}`를 수정했어요. 다음 응답부터 반영돼요.", ephemeral=True)

    async def _set_enabled(self, interaction, identifier, enabled):
        try:
            self.registry.set_enabled(identifier, enabled)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        state = "활성화" if enabled else "비활성화"
        await interaction.response.send_message(
            f"{self.label} `{identifier}`를 {state}했어요.", ephemeral=True)

    async def _remove(self, interaction, identifier):
        try:
            self.registry.remove(identifier)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            f"{self.label} `{identifier}`를 삭제했어요.", ephemeral=True)


@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class LoreCommands(_KnowledgeGroup):
    label = "lore"

    def __init__(self, client):
        super().__init__(name="lore", description="동적 세계관 사실 관리 (봇 관리자 전용)")
        self.client = client
        self.registry = RuntimeKnowledgeRegistry(client.settings.runtime_lore_path, kind="world_fact")

    @app_commands.command(name="add", description="확정 사실을 런타임 lore에 추가")
    @app_commands.describe(
        identifier="영문 ID", content="사실 본문", keywords="쉼표로 구분한 검색어",
        subjects="쉼표로 구분한 인물/사건", awareness="히나의 인지 범위",
        timeline="작중 시점 또는 적용 시점",
    )
    async def add(self, interaction: discord.Interaction, identifier: str, content: str,
                  keywords: str, subjects: str,
                  awareness: Awareness = Awareness.PUBLIC_KNOWLEDGE,
                  timeline: str = "시점 미지정"):
        await self._add(interaction, identifier, content, keywords, subjects, awareness, timeline)

    @app_commands.command(name="list", description="동적 lore 목록 확인")
    async def list_items(self, interaction: discord.Interaction):
        await self._list(interaction)

    @app_commands.command(name="show", description="동적 lore 한 항목 자세히 보기")
    async def show(self, interaction: discord.Interaction, identifier: str):
        await self._show(interaction, identifier)

    @app_commands.command(name="edit", description="동적 lore 항목 수정; 비운 값은 유지")
    async def edit(self, interaction: discord.Interaction, identifier: str,
                   content: str | None = None, keywords: str | None = None,
                   subjects: str | None = None, awareness: str | None = None,
                   timeline: str | None = None):
        await self._edit(interaction, identifier, content, keywords, subjects, awareness, timeline)

    @app_commands.command(name="enable", description="동적 lore 활성화")
    async def enable(self, interaction: discord.Interaction, identifier: str):
        await self._set_enabled(interaction, identifier, True)

    @app_commands.command(name="disable", description="동적 lore 비활성화")
    async def disable(self, interaction: discord.Interaction, identifier: str):
        await self._set_enabled(interaction, identifier, False)

    @app_commands.command(name="remove", description="동적 lore 영구 삭제")
    async def remove(self, interaction: discord.Interaction, identifier: str):
        await self._remove(interaction, identifier)


@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class ContextCommands(_KnowledgeGroup):
    label = "context"

    def __init__(self, client):
        super().__init__(name="context", description="동적 스토리 해석·맥락 관리 (봇 관리자 전용)")
        self.client = client
        self.registry = RuntimeKnowledgeRegistry(client.settings.context_path, kind="interpretation")

    @app_commands.command(name="add", description="스토리 해석이나 대사 맥락을 추가")
    @app_commands.describe(
        identifier="영문 ID", content="해석/맥락 본문", keywords="쉼표로 구분한 검색어",
        subjects="쉼표로 구분한 인물/사건", awareness="히나의 인지 범위",
        timeline="작중 시점 또는 적용 시점",
    )
    async def add(self, interaction: discord.Interaction, identifier: str, content: str,
                  keywords: str, subjects: str,
                  awareness: Awareness = Awareness.INFERENCE,
                  timeline: str = "시점 미지정"):
        await self._add(interaction, identifier, content, keywords, subjects, awareness, timeline)

    @app_commands.command(name="list", description="동적 context 목록 확인")
    async def list_items(self, interaction: discord.Interaction):
        await self._list(interaction)

    @app_commands.command(name="show", description="동적 context 한 항목 자세히 보기")
    async def show(self, interaction: discord.Interaction, identifier: str):
        await self._show(interaction, identifier)

    @app_commands.command(name="edit", description="동적 context 항목 수정; 비운 값은 유지")
    async def edit(self, interaction: discord.Interaction, identifier: str,
                   content: str | None = None, keywords: str | None = None,
                   subjects: str | None = None, awareness: str | None = None,
                   timeline: str | None = None):
        await self._edit(interaction, identifier, content, keywords, subjects, awareness, timeline)

    @app_commands.command(name="enable", description="동적 context 활성화")
    async def enable(self, interaction: discord.Interaction, identifier: str):
        await self._set_enabled(interaction, identifier, True)

    @app_commands.command(name="disable", description="동적 context 비활성화")
    async def disable(self, interaction: discord.Interaction, identifier: str):
        await self._set_enabled(interaction, identifier, False)

    @app_commands.command(name="remove", description="동적 context 영구 삭제")
    async def remove(self, interaction: discord.Interaction, identifier: str):
        await self._remove(interaction, identifier)
