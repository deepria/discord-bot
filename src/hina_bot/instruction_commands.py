import logging

import discord
from discord import app_commands

from .instructions import InstructionRegistry

log = logging.getLogger("hina")


@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
class InstructionCommands(app_commands.Group):
    def __init__(self, client):
        super().__init__(name="instruction", description="동적 캐릭터 instruction 관리 (봇 관리자 전용)")
        self.client = client
        self.registry = InstructionRegistry(client.settings.instruction_path)

    async def interaction_check(self, interaction):
        # Discord hides this group from non-administrators by default. Keep the bot-owner/
        # BOT_ADMIN_IDS check as an application-side backstop in case guild command permissions
        # are overridden later.
        if interaction.user.id not in self.client.emoji_admin_ids:
            await interaction.response.send_message(
                "봇 소유자 또는 지정된 관리자만 사용할 수 있어요.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction, error):
        log.warning("Instruction command failed (%s)", type(error).__name__)
        text = "instruction을 처리하지 못했어요. /instruction list로 현재 상태를 확인해 주세요."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="add", description="새 동적 instruction 추가 및 즉시 활성화")
    @app_commands.describe(identifier="영문 ID", text="히나에게 추가할 보조 지침")
    async def add(self, interaction: discord.Interaction, identifier: str, text: str):
        try:
            self.registry.add(identifier, text)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            f"instruction `{identifier}`를 추가하고 활성화했어요.", ephemeral=True)

    @app_commands.command(name="list", description="저장된 동적 instruction 목록 확인")
    async def list_items(self, interaction: discord.Interaction):
        rows = self.registry.list()
        if not rows:
            await interaction.response.send_message("등록된 동적 instruction이 없어요.", ephemeral=True)
            return
        lines = [f"동적 instruction {len(rows)}/50"]
        for row in rows:
            state = "ON" if row.get("enabled", True) else "OFF"
            text = discord.utils.escape_markdown(row.get("text", ""))
            if len(text) > 180:
                text = text[:177] + "..."
            lines.append(f"`{row.get('id', '?')}` [{state}] — {text}")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="edit", description="기존 instruction 본문 수정")
    @app_commands.describe(identifier="수정할 ID", text="새 보조 지침")
    async def edit(self, interaction: discord.Interaction, identifier: str, text: str):
        try:
            self.registry.edit(identifier, text)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            f"instruction `{identifier}` 내용을 수정했어요.", ephemeral=True)

    @app_commands.command(name="enable", description="instruction 활성화")
    async def enable(self, interaction: discord.Interaction, identifier: str):
        await self._set_enabled(interaction, identifier, True)

    @app_commands.command(name="disable", description="instruction 비활성화")
    async def disable(self, interaction: discord.Interaction, identifier: str):
        await self._set_enabled(interaction, identifier, False)

    async def _set_enabled(self, interaction, identifier: str, enabled: bool):
        try:
            self.registry.set_enabled(identifier, enabled)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        state = "활성화" if enabled else "비활성화"
        await interaction.response.send_message(
            f"instruction `{identifier}`를 {state}했어요.", ephemeral=True)

    @app_commands.command(name="remove", description="instruction 영구 삭제")
    async def remove(self, interaction: discord.Interaction, identifier: str):
        try:
            self.registry.remove(identifier)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            f"instruction `{identifier}`를 삭제했어요.", ephemeral=True)
