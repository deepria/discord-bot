import logging

import discord
from discord import app_commands

from .knowledge_ingest import KnowledgeIngestor
from .runtime_knowledge import RuntimeKnowledgeRegistry

log = logging.getLogger("hina")


@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class KnowledgeCommands(app_commands.Group):
    def __init__(self, client):
        super().__init__(
            name="knowledge",
            description="조사 메모를 자동 분해·조정해 설정 사실·해석으로 반영 (봇 관리자 전용)",
        )
        self.client = client
        self.fact_registry = RuntimeKnowledgeRegistry(
            client.settings.runtime_lore_path, kind="world_fact")
        self.context_registry = RuntimeKnowledgeRegistry(
            client.settings.context_path, kind="interpretation")
        self.ingestor = KnowledgeIngestor(client.llm)

    async def interaction_check(self, interaction):
        if interaction.user.id not in self.client.emoji_admin_ids:
            await interaction.response.send_message(
                "봇 소유자 또는 지정된 관리자만 사용할 수 있어요.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction, error):
        log.warning("Knowledge command failed (%s)", type(error).__name__)
        text = "knowledge를 처리하지 못했어요. 잠시 후 다시 시도해 주세요."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    def _all_rows(self):
        rows = []
        rows.extend(("fact", row) for row in self.fact_registry.list())
        rows.extend(("context", row) for row in self.context_registry.list())
        return rows

    def _find(self, identifier: str):
        matches = []
        for label, registry in (("fact", self.fact_registry), ("context", self.context_registry)):
            try:
                matches.append((label, registry, registry.get(identifier)))
            except ValueError:
                pass
        if not matches:
            raise ValueError("등록되지 않은 knowledge ID입니다.")
        if len(matches) > 1:
            raise ValueError("같은 ID가 사실/해석 양쪽에 있어 자동 처리할 수 없습니다.")
        return matches[0]

    @app_commands.command(
        name="ingest",
        description="긴 조사 메모를 기존 knowledge와 조정해 자동 반영",
    )
    @app_commands.describe(text="정리할 최신 조사 메모. 사실과 추측이 섞여 있어도 됩니다 (최대 6000자)")
    async def ingest(self, interaction: discord.Interaction, text: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await self.ingestor.ingest(text)
        except (TypeError, ValueError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        added = result["added"]
        updated = result["updated"]
        removed = result["removed"]
        held = result["held"]
        skipped = result["skipped"]
        lines = [
            (
                f"knowledge 반영 완료: 추가 {len(added)}개 / 갱신 {len(updated)}개 / "
                f"대체 삭제 {len(removed)}개"
            ),
            f"기존 내용 유지·중복: {len(skipped)}개 / 애매해서 보류: {len(held)}개",
        ]
        changed = added + updated
        if changed:
            lines.append("\n반영된 항목:")
            for item in changed[:12]:
                kind = "사실" if item["kind"] == "world_fact" else "해석"
                verb = "갱신" if item in updated else "추가"
                lines.append(f"- `{item['id']}` [{kind}/{verb}]")
            if len(changed) > 12:
                lines.append(f"- … 외 {len(changed) - 12}개")
        if removed:
            lines.append("\n중복·구버전으로 제거:")
            for item in removed[:6]:
                lines.append(f"- `{item['id']}` → `{item['superseded_by']}`")
            if len(removed) > 6:
                lines.append(f"- … 외 {len(removed) - 6}개")
        if held:
            lines.append("\n보류된 항목:")
            for item in held[:6]:
                reason = discord.utils.escape_markdown(item["reason"].replace("\n", " "))
                lines.append(f"- `{item['id']}` — {reason[:140]}")
            if len(held) > 6:
                lines.append(f"- … 외 {len(held) - 6}개")
        await interaction.followup.send("\n".join(lines)[:1900], ephemeral=True)

    @app_commands.command(name="list", description="자동 반영된 knowledge 목록 확인")
    async def list_items(self, interaction: discord.Interaction):
        try:
            rows = self._all_rows()
        except (TypeError, ValueError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if not rows:
            await interaction.response.send_message("자동 반영된 knowledge가 없어요.", ephemeral=True)
            return
        lines = [f"knowledge {len(rows)}/200"]
        for label, row in rows[:18]:
            state = "ON" if row["enabled"] else "OFF"
            kind = "사실" if label == "fact" else "해석"
            preview = discord.utils.escape_markdown(row["content"].replace("\n", " "))
            if len(preview) > 80:
                preview = preview[:77] + "..."
            lines.append(f"`{row['id']}` [{kind}/{state}] — {preview}")
        if len(rows) > 18:
            lines.append(f"… 외 {len(rows) - 18}개")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="show", description="자동 반영된 knowledge 한 항목 자세히 보기")
    async def show(self, interaction: discord.Interaction, identifier: str):
        try:
            label, _, row = self._find(identifier)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        state = "ON" if row["enabled"] else "OFF"
        kind = "사실" if label == "fact" else "해석"
        text = (
            f"`{row['id']}` [{kind}/{state}]\n"
            f"awareness: `{row['awareness']}`\n"
            f"timeline: {row['timeline']}\n"
            f"subjects: {', '.join(row['subjects'])}\n"
            f"keywords: {', '.join(row['keywords'])}\n\n"
            f"{row['content']}"
        )
        await interaction.response.send_message(text[:1900], ephemeral=True)

    async def _set_enabled(self, interaction, identifier: str, enabled: bool):
        try:
            _, registry, _ = self._find(identifier)
            registry.set_enabled(identifier, enabled)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        state = "활성화" if enabled else "비활성화"
        await interaction.response.send_message(
            f"knowledge `{identifier}`를 {state}했어요.", ephemeral=True)

    @app_commands.command(name="enable", description="knowledge 항목 활성화")
    async def enable(self, interaction: discord.Interaction, identifier: str):
        await self._set_enabled(interaction, identifier, True)

    @app_commands.command(name="disable", description="knowledge 항목 비활성화")
    async def disable(self, interaction: discord.Interaction, identifier: str):
        await self._set_enabled(interaction, identifier, False)

    @app_commands.command(name="remove", description="knowledge 항목 영구 삭제")
    async def remove(self, interaction: discord.Interaction, identifier: str):
        try:
            _, registry, _ = self._find(identifier)
            registry.remove(identifier)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            f"knowledge `{identifier}`를 삭제했어요.", ephemeral=True)
