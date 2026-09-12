"""Per-channel persistent-memory controls for repeatable debugging."""
import logging
from enum import Enum

import discord
from discord import app_commands

from .instruction_commands import InstructionCommands
from .knowledge_commands import KnowledgeCommands
from .routing import Scope

log = logging.getLogger("hina")


class MemoryMode(str, Enum):
    normal = "normal"
    read_only = "read_only"
    write_only = "write_only"
    off = "off"

    @property
    def reads(self):
        return self in (MemoryMode.normal, MemoryMode.read_only)

    @property
    def writes(self):
        return self in (MemoryMode.normal, MemoryMode.write_only)


def mode_text(mode):
    return (f"현재 채널: **{mode.value}**\n"
            f"장기 기억을 답변에 사용: {'켜짐' if mode.reads else '꺼짐'}\n"
            f"새 장기 기억 저장: {'켜짐' if mode.writes else '꺼짐'}\n"
            "같은 채널의 최근 대화 문맥은 이 설정과 별개로 계속 사용해요.\n"
            "기존 장기 기억은 유지돼요. 캐릭터 프롬프트·이모지 설정도 계속 적용돼요.")


@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class MemoryCommands(app_commands.Group):
    def __init__(self, client):
        super().__init__(name="memory", description="현재 채널의 장기 기억 디버깅 모드 (봇 관리자 전용)")
        self.client = client
        # HinaClient already has its CommandTree before this group is constructed.
        # Tests sometimes construct this group with a minimal mock client that has no tree/settings.
        if hasattr(client, "tree") and hasattr(client, "settings"):
            client.tree.add_command(InstructionCommands(client))
            client.tree.add_command(KnowledgeCommands(client))

    async def interaction_check(self, interaction):
        # Bot ownership/BOT_ADMIN_IDS is the authority here, not guild Administrator permission.
        # This lets designated bot admins manage the bot from any server or DM where the app
        # command is available.
        if interaction.user.id not in self.client.emoji_admin_ids:
            await interaction.response.send_message("봇 소유자 또는 지정된 관리자만 사용할 수 있어요.",
                                                    ephemeral=True)
            return False
        return True

    @staticmethod
    def scope(interaction):
        if interaction.channel_id is None:
            raise ValueError("채널 안에서 실행해 주세요.")
        return Scope(interaction.guild_id, interaction.channel_id, interaction.user.id)

    async def on_error(self, interaction, error):
        log.warning("Memory mode command failed (%s)", type(error).__name__)
        text = "모드를 변경하지 못했어요. /memory status로 현재 상태를 확인해 주세요."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="mode", description="장기 기억 사용·저장 모드 변경; 최근 채널 문맥은 유지")
    @app_commands.describe(value="normal: 정상 / read_only: 읽기만 / write_only: 쓰기만 / off: 모두 끄기")
    async def mode(self, interaction: discord.Interaction, value: MemoryMode):
        await interaction.response.defer(ephemeral=True)
        scope = self.scope(interaction)
        # Wait for any in-flight turn in this channel; never acknowledge an incomplete switch.
        async with self.client.channel_lock(scope):
            self.client.store.set_memory_mode(scope, value.value)
        await interaction.followup.send(mode_text(value), ephemeral=True)

    @app_commands.command(name="status", description="현재 채널의 장기 기억 사용·저장 상태 확인")
    async def status(self, interaction: discord.Interaction):
        mode = MemoryMode(self.client.store.memory_mode(self.scope(interaction)))
        await interaction.response.send_message(mode_text(mode), ephemeral=True)
