"""Bot-admin slash commands for hot-reloadable runtime settings."""

import logging

import discord
from discord import app_commands

from rio_bot.core.routing import chunks
from rio_bot.core.runtime_config import (
    RUNTIME_SETTING_SPECS,
    RuntimeConfigAudit,
    RuntimeSettings,
    format_runtime_value,
)
from rio_bot.core.runtime_settings_service import apply_runtime_setting, apply_runtime_side_effects

log = logging.getLogger("rio")

_KEY_CHOICES = [
    app_commands.Choice(name=spec.env_name, value=attr)
    for attr, spec in RUNTIME_SETTING_SPECS.items()
]
_ALWAYS_REPLY_KEY = "always_reply_channel_ids"


async def _send_config_response(interaction: discord.Interaction, text: str):
    parts = iter(chunks(text))
    await interaction.response.send_message(next(parts), ephemeral=True)
    for part in parts:
        await interaction.followup.send(part, ephemeral=True)


class AlwaysReplyCommands(app_commands.Group):
    def __init__(self, config):
        super().__init__(name="always-reply", description="서버 채널별 리오 자동응답 관리")
        self.config = config

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.config.interaction_check(interaction)

    async def _channel_setting(self, interaction, channel, *, enabled=None):
        target = channel if channel is not None else interaction.channel
        if (
            interaction.guild_id is None
            or not isinstance(target, (discord.TextChannel, discord.Thread))
            or target.guild.id != interaction.guild_id
        ):
            await interaction.response.send_message(
                "현재 서버의 텍스트 채널이나 스레드를 선택해 주세요.", ephemeral=True
            )
            return

        settings = self.config.client.settings
        channel_ids = set(settings.always_reply_channel_ids)
        active = target.id in channel_ids
        if enabled is not None and enabled != active:
            if enabled:
                channel_ids.add(target.id)
            else:
                channel_ids.remove(target.id)
            try:
                result = apply_runtime_setting(
                    settings, key=_ALWAYS_REPLY_KEY,
                    value=",".join(map(str, sorted(channel_ids))),
                    actor_kind="discord", actor_id=str(interaction.user.id),
                    request_id=str(interaction.id),
                )
            except ValueError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return
            active = target.id in result["value"]

        state = "켜짐" if active else "꺼짐"
        source = "DB" if settings.source(_ALWAYS_REPLY_KEY) == "db" else "시작 설정"
        await interaction.response.send_message(
            f"<#{target.id}> 자동응답 **{state}** · 설정 출처 {source}", ephemeral=True
        )

    @app_commands.command(name="enable", description="현재 또는 선택한 채널의 자동응답 켜기")
    async def enable(
        self, interaction: discord.Interaction,
        channel: discord.TextChannel | discord.Thread | None = None,
    ):
        await self._channel_setting(interaction, channel, enabled=True)

    @app_commands.command(name="disable", description="현재 또는 선택한 채널의 자동응답 끄기")
    async def disable(
        self, interaction: discord.Interaction,
        channel: discord.TextChannel | discord.Thread | None = None,
    ):
        await self._channel_setting(interaction, channel, enabled=False)

    @app_commands.command(name="status", description="현재 또는 선택한 채널의 자동응답 확인")
    async def status(
        self, interaction: discord.Interaction,
        channel: discord.TextChannel | discord.Thread | None = None,
    ):
        await self._channel_setting(interaction, channel)


@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class ConfigCommands(app_commands.Group):
    def __init__(self, client):
        super().__init__(name="config", description="런타임 설정 관리 (봇 관리자 전용)")
        self.client = client
        self.add_command(AlwaysReplyCommands(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in self.client.emoji_admin_ids:
            await interaction.response.send_message(
                "봇 소유자 또는 지정된 관리자만 사용할 수 있어요.", ephemeral=True
            )
            return False
        if not isinstance(self.client.settings, RuntimeSettings):
            await interaction.response.send_message(
                "이 실행 방식에서는 런타임 설정 변경을 사용할 수 없어요.", ephemeral=True
            )
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error):
        log.warning("Config command failed (%s)", type(error).__name__)
        text = "런타임 설정을 처리하지 못했어요. /config status로 현재 상태를 확인해 주세요."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="status", description="현재 런타임 설정과 DB override 확인")
    async def status(self, interaction: discord.Interaction):
        lines = [
            "런타임 설정",
            "`DB`가 있으면 즉시 적용되고, 없으면 시작 시 읽은 .env/.env.local 또는 코드 기본값을 사용해요.",
            "",
        ]
        for spec, value, source in self.client.settings.rows():
            label = "DB" if source == "db" else "startup"
            lines.append(f"`{spec.env_name}` = `{format_runtime_value(value)}`  [{label}]")
        await _send_config_response(interaction, "\n".join(lines))

    @app_commands.command(name="set", description="런타임 설정을 DB에 저장하고 즉시 적용")
    @app_commands.describe(
        key="변경할 설정",
        value=(
            "bool은 on/off, 호출어와 채널 ID는 쉼표 구분, 목록이나 위치를 비우려면 none"
        ),
    )
    @app_commands.choices(key=_KEY_CHOICES)
    async def set_config(self, interaction: discord.Interaction, key: str, value: str):
        try:
            result = apply_runtime_setting(
                self.client.settings, key=key, value=value,
                actor_kind="discord", actor_id=str(interaction.user.id), request_id=str(interaction.id),
            )
            apply_runtime_side_effects(self.client, key)
        except ValueError as exc:
            self.client.settings.record_audit(
                RuntimeConfigAudit(
                    actor_kind="discord", actor_id=str(interaction.user.id),
                    action="runtime_config.set", target=RUNTIME_SETTING_SPECS[key].env_name,
                    outcome="failure", request_id=str(interaction.id),
                )
            )
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        spec = RUNTIME_SETTING_SPECS[key]
        await _send_config_response(
            interaction,
            f"`{spec.env_name}`을 `{result['display_value']}`로 변경했어요. "
            "DB override라 재시작 후에도 유지돼요.",
        )

    @app_commands.command(name="reset", description="DB override를 삭제하고 시작 시 기본값으로 복귀")
    @app_commands.describe(key="초기화할 설정")
    @app_commands.choices(key=_KEY_CHOICES)
    async def reset(self, interaction: discord.Interaction, key: str):
        try:
            result = apply_runtime_setting(
                self.client.settings, key=key, value=None,
                actor_kind="discord", actor_id=str(interaction.user.id), request_id=str(interaction.id),
            )
        except ValueError as exc:
            self.client.settings.record_audit(
                RuntimeConfigAudit(
                    actor_kind="discord", actor_id=str(interaction.user.id),
                    action="runtime_config.reset", target=RUNTIME_SETTING_SPECS[key].env_name,
                    outcome="failure", request_id=str(interaction.id),
                )
            )
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        apply_runtime_side_effects(self.client, key)
        spec = RUNTIME_SETTING_SPECS[key]
        await _send_config_response(
            interaction,
            f"`{spec.env_name}`의 DB override를 삭제했어요. "
            f"이제 시작 시 값 `{result['display_value']}`을 사용해요.",
        )
