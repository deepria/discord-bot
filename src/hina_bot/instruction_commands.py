import discord


class InstructionCommands:
    HELP = (
        "히나야 /instruction add <id> <내용>\n"
        "히나야 /instruction list\n"
        "히나야 /instruction edit <id> <내용>\n"
        "히나야 /instruction enable <id>\n"
        "히나야 /instruction disable <id>\n"
        "히나야 /instruction remove <id>"
    )

    def __init__(self, client):
        self.client = client

    async def handle(self, message, text: str):
        if message.author.id not in self.client.emoji_admin_ids:
            return "봇 소유자 또는 지정된 관리자만 사용할 수 있어요."
        parts = text.split(maxsplit=1)
        action, rest = (parts[0], parts[1] if len(parts) > 1 else "") if parts else ("", "")
        registry = self.client.instruction_registry
        try:
            if action == "list" and not rest:
                rows = registry.list()
                if not rows:
                    return "등록된 동적 instruction이 없어요.\n" + self.HELP
                lines = [f"동적 instruction {len(rows)}/50"]
                for row in rows:
                    state = "ON" if row.get("enabled", True) else "OFF"
                    text = discord.utils.escape_markdown(row.get("text", ""))
                    if len(text) > 180:
                        text = text[:177] + "..."
                    lines.append(f"`{row.get('id', '?')}` [{state}] — {text}")
                return "\n".join(lines)
            if action in {"add", "edit"}:
                args = rest.split(maxsplit=1)
                if len(args) != 2:
                    return self.HELP
                identifier, body = args
                if action == "add":
                    registry.add(identifier, body)
                    return f"instruction `{identifier}`를 추가하고 활성화했어요."
                registry.edit(identifier, body)
                return f"instruction `{identifier}` 내용을 수정했어요."
            if action in {"enable", "disable", "remove"} and rest and len(rest.split()) == 1:
                if action == "remove":
                    registry.remove(rest)
                    return f"instruction `{rest}`를 삭제했어요."
                registry.set_enabled(rest, action == "enable")
                return f"instruction `{rest}`를 {'활성화' if action == 'enable' else '비활성화'}했어요."
            return self.HELP
        except ValueError as exc:
            return str(exc)
