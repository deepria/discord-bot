"""Hierarchical persistent-memory and recent-chat controls for repeatable debugging."""
import logging
from enum import Enum

import discord
from discord import app_commands

from .admin_list import MAX_DISCORD_TEXT, table_row
from .instruction_commands import InstructionCommands
from .knowledge_commands import KnowledgeCommands
from .routing import Scope

log = logging.getLogger("hina")

_TARGET_CHOICES = [
    app_commands.Choice(name="현재 채널", value="channel"),
    app_commands.Choice(name="현재 서버", value="server"),
    app_commands.Choice(name="전역", value="global"),
]
_VALUE_CHOICES = [
    app_commands.Choice(name="normal — 읽기/쓰기", value="normal"),
    app_commands.Choice(name="read_only — 읽기만", value="read_only"),
    app_commands.Choice(name="write_only — 쓰기만", value="write_only"),
    app_commands.Choice(name="off — 읽기/쓰기 끄기", value="off"),
    app_commands.Choice(name="inherit — 상위 설정 따르기", value="inherit"),
]
_CHAT_LOG_CHOICES = [
    app_commands.Choice(name="on — 최근 채널 로그 읽기", value="on"),
    app_commands.Choice(name="off — 최근 채널 로그 읽지 않기", value="off"),
    app_commands.Choice(name="inherit — 상위 설정 따르기", value="inherit"),
]
_VIEW_CHOICES = [
    app_commands.Choice(name="모든 서버/채널", value="all"),
    app_commands.Choice(name="직접 설정된 override 중심", value="overrides"),
]
_SOURCE_LABEL = {"channel": "채널", "server": "서버", "global": "전역", "default": "기본값"}


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


def _table_pages(title: str, columns: list[str], rows: list[list[str]], widths: list[int]) -> list[str]:
    heading = [table_row(columns, widths), table_row(["-" * width for width in widths], widths)]
    groups: list[list[str]] = []
    current: list[str] = []
    budget = MAX_DISCORD_TEXT - len(title) - 40
    for values in rows:
        line = table_row(values, widths)
        candidate = "\n".join(heading + current + [line])
        if current and len(candidate) > budget:
            groups.append(current)
            current = []
        current.append(line)
    groups.append(current)
    count = len(groups)
    return [
        f"{title} ({index}/{count})\n```text\n" + "\n".join(heading + lines) + "\n```"
        for index, lines in enumerate(groups, 1)
    ]


@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class MemoryCommands(app_commands.Group):
    def __init__(self, client):
        super().__init__(name="memory", description="장기 기억·최근 채널 로그 설정 관리 (봇 관리자 전용)")
        self.client = client
        if hasattr(client, "tree") and hasattr(client, "settings"):
            client.tree.add_command(InstructionCommands(client))
            client.tree.add_command(KnowledgeCommands(client))

    async def interaction_check(self, interaction):
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
        text = "기억 설정을 처리하지 못했어요. /memory status로 현재 상태를 확인해 주세요."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    @staticmethod
    def _target_key(scope: Scope, target: str) -> str:
        if target == "global":
            return "global"
        if target == "server":
            if scope.guild_id is None:
                raise ValueError("DM에서는 서버 설정을 변경할 수 없어요.")
            return scope.realm
        if target == "channel":
            return scope.channel
        raise ValueError("알 수 없는 설정 범위예요.")

    @staticmethod
    def _chain_lines(chain: dict, default: str, *, include_server: bool) -> list[str]:
        global_text = chain["global"] or f"{default} (기본값)"
        lines = [f"최종 적용: **{chain['effective']}** (출처: {_SOURCE_LABEL[chain['source']]})",
                 f"전역: `{global_text}`"]
        parent = chain["global"] or default
        if include_server:
            server = chain["server"]
            lines.append(f"서버: `{'상속 → ' + parent if server is None else server}`")
            parent = server or parent
        channel = chain["channel"]
        lines.append(f"채널: `{'상속 → ' + parent if channel is None else channel}`")
        return lines

    def _status_text(self, scope: Scope) -> str:
        memory = self.client.store.memory_mode_chain(scope)
        memory_lines = self._chain_lines(memory, "normal", include_server=scope.guild_id is not None)
        mode = MemoryMode(str(memory["effective"]))
        memory_lines.append(
            f"장기 기억 읽기: {'켜짐' if mode.reads else '꺼짐'} / "
            f"새 장기 기억 저장: {'켜짐' if mode.writes else '꺼짐'}")

        chat = self.client.store.chat_log_mode_chain(scope)
        chat_lines = self._chain_lines(chat, "on", include_server=scope.guild_id is not None)
        if scope.guild_id is None:
            chat_lines.append("DM에서는 최근 채널 로그 문맥을 사용하지 않아요.")
        else:
            chat_lines.append(
                "최근 채널 로그 읽기·수집: " + ("켜짐" if chat["effective"] == "on" else "꺼짐"))
        return "장기 기억\n" + "\n".join(memory_lines) + "\n\n최근 채널 로그\n" + "\n".join(chat_lines)

    @app_commands.command(name="mode", description="전역/서버/채널 장기 기억 설정 또는 상속 지정")
    @app_commands.describe(
        value="적용할 모드. inherit는 상위 범위 설정을 따릅니다",
        target="적용 범위. 기본은 현재 채널",
    )
    @app_commands.choices(value=_VALUE_CHOICES, target=_TARGET_CHOICES)
    async def mode(
        self,
        interaction: discord.Interaction,
        value: str,
        target: str = "channel",
    ):
        try:
            scope = self.scope(interaction)
            key = self._target_key(scope, target)
            if value == "inherit" and target == "global":
                raise ValueError("전역 설정은 상속할 상위 범위가 없어요. normal 등 실제 모드를 선택해 주세요.")
            if value not in {choice.value for choice in _VALUE_CHOICES}:
                raise ValueError("알 수 없는 기억 모드예요.")
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        async with self.client.channel_lock(scope):
            self.client.store.set_memory_mode_override(key, None if value == "inherit" else value)
        changed = {"channel": "채널", "server": "서버", "global": "전역"}[target]
        state = "상위 설정을 따르도록 변경" if value == "inherit" else f"{value}로 변경"
        await interaction.followup.send(
            f"{changed} 장기 기억 설정을 {state}했어요.\n\n{self._status_text(scope)}", ephemeral=True)

    @app_commands.command(name="chatlog", description="전역/서버/채널 최근 채널 로그 읽기 설정")
    @app_commands.describe(
        value="on/off 또는 상위 설정 상속",
        target="적용 범위. 기본은 현재 채널",
    )
    @app_commands.choices(value=_CHAT_LOG_CHOICES, target=_TARGET_CHOICES)
    async def chatlog(
        self,
        interaction: discord.Interaction,
        value: str,
        target: str = "channel",
    ):
        try:
            scope = self.scope(interaction)
            key = self._target_key(scope, target)
            if value == "inherit" and target == "global":
                raise ValueError("전역 chat log 설정은 상속할 수 없어요. on 또는 off를 선택해 주세요.")
            if value not in {choice.value for choice in _CHAT_LOG_CHOICES}:
                raise ValueError("알 수 없는 chat log 모드예요.")
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        async with self.client.channel_lock(scope):
            self.client.store.set_chat_log_mode_override(
                key, None if value == "inherit" else value)
            effective_off = not self.client.store.chat_log_enabled(scope)
            if value == "off" or (value == "inherit" and effective_off):
                if target == "global":
                    self.client.recent.clear_all()
                elif target == "server":
                    self.client.recent.forget(scope)
                else:
                    self.client.recent.clear_channel(scope)
        changed = {"channel": "채널", "server": "서버", "global": "전역"}[target]
        state = "상위 설정을 따르도록 변경" if value == "inherit" else f"{value}으로 변경"
        await interaction.followup.send(
            f"{changed} 최근 채널 로그 설정을 {state}했어요.\n\n{self._status_text(scope)}",
            ephemeral=True)

    @app_commands.command(name="status", description="현재 채널의 장기 기억·최근 로그 설정 확인")
    async def status(self, interaction: discord.Interaction):
        try:
            text = self._status_text(self.scope(interaction))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(text, ephemeral=True)

    def _overview_rows(self, user_id: int, view: str) -> list[list[str]]:
        store = self.client.store
        memory_overrides = store.memory_mode_overrides()
        chat_overrides = store.chat_log_mode_overrides()
        memory_global = memory_overrides.get("global")
        chat_global = chat_overrides.get("global")
        rows = [["전역", "GLOBAL", memory_global or "기본(normal)", memory_global or "normal",
                 chat_global or "기본(on)", chat_global or "on"]]
        known = {"global"}
        settings = getattr(self.client, "settings", None)
        allowed = getattr(settings, "allowed_guild_ids", frozenset()) if settings else frozenset()
        guilds = sorted(getattr(self.client, "guilds", []), key=lambda guild: guild.name.casefold())
        for guild in guilds:
            if allowed and guild.id not in allowed:
                continue
            server_key = f"guild:{guild.id}"
            memory_server = memory_overrides.get(server_key)
            chat_server = chat_overrides.get(server_key)
            memory_server_effective = memory_server or memory_global or "normal"
            chat_server_effective = chat_server or chat_global or "on"
            known.add(server_key)
            rows.append(["서버", guild.name, memory_server or "상속", memory_server_effective,
                         chat_server or "상속", chat_server_effective])

            channels = list(getattr(guild, "text_channels", [])) + list(getattr(guild, "threads", []))
            channels = sorted({channel.id: channel for channel in channels}.values(),
                              key=lambda channel: channel.name.casefold())
            inherited = 0
            for channel in channels:
                scope = Scope(guild.id, channel.id, user_id)
                memory_direct = memory_overrides.get(scope.channel)
                chat_direct = chat_overrides.get(scope.channel)
                known.add(scope.channel)
                if view == "overrides" and memory_direct is None and chat_direct is None:
                    inherited += 1
                    continue
                rows.append([
                    "채널", f"{guild.name}/#{channel.name}",
                    memory_direct or "상속", memory_direct or memory_server_effective,
                    chat_direct or "상속", chat_direct or chat_server_effective,
                ])
            if view == "overrides" and inherited:
                rows.append(["채널", f"{guild.name}/(나머지 {inherited}개)",
                             "상속", memory_server_effective, "상속", chat_server_effective])

        for key in sorted(set(memory_overrides) | set(chat_overrides)):
            if key in known:
                continue
            memory_mode = memory_overrides.get(key)
            chat_mode = chat_overrides.get(key)
            rows.append(["미확인", key, memory_mode or "-", memory_mode or "-",
                         chat_mode or "-", chat_mode or "-"])
        return rows

    @app_commands.command(name="overview", description="모든 서버/채널의 기억·최근 로그 설정 한눈에 보기")
    @app_commands.describe(view="모든 채널을 보거나 직접 override된 항목 중심으로 압축해서 보기")
    @app_commands.choices(view=_VIEW_CHOICES)
    async def overview(self, interaction: discord.Interaction, view: str = "all"):
        if view not in {choice.value for choice in _VIEW_CHOICES}:
            await interaction.response.send_message("알 수 없는 보기 방식이에요.", ephemeral=True)
            return
        rows = self._overview_rows(interaction.user.id, view)
        pages = _table_pages(
            "기억/최근 로그 설정 · 직접=직접 저장, 적용=상속까지 계산한 최종 값",
            ["범위", "서버/채널", "기억 직접", "기억 적용", "로그 직접", "로그 적용"],
            rows,
            [6, 32, 12, 12, 10, 10],
        )
        await interaction.response.send_message(pages[0], ephemeral=True)
        for page in pages[1:]:
            await interaction.followup.send(page, ephemeral=True)
