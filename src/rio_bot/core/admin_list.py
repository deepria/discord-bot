from datetime import UTC, datetime
from io import BytesIO
from unicodedata import combining, east_asian_width

import discord

MAX_DISCORD_TEXT = 1900


def created_timestamp(row: dict) -> float | None:
    value = row.get("created_at")
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def created_label(row: dict) -> str:
    timestamp = created_timestamp(row)
    if timestamp is None:
        return "시간 미기록"
    return f"<t:{int(timestamp)}:F>"


def created_compact(row: dict) -> str:
    timestamp = created_timestamp(row)
    if timestamp is None:
        return "미기록"
    value = datetime.fromtimestamp(timestamp, tz=UTC)
    return value.strftime("%m-%d %H:%MZ")


def sort_rows(rows: list, sort: str, row_of) -> list:
    if sort == "recent":
        return sorted(
            rows,
            key=lambda item: (created_timestamp(row_of(item)) is not None,
                              created_timestamp(row_of(item)) or 0),
            reverse=True,
        )
    if sort == "id":
        return sorted(rows, key=lambda item: str(row_of(item).get("id", "")).casefold())
    if sort == "state":
        return sorted(
            rows,
            key=lambda item: (not bool(row_of(item).get("enabled", True)),
                              created_timestamp(row_of(item)) or 0),
        )
    # Default: creation time, oldest first. Legacy rows without timestamps stay first.
    return sorted(
        rows,
        key=lambda item: (created_timestamp(row_of(item)) is not None,
                          created_timestamp(row_of(item)) or 0),
    )


def display_width(text: str) -> int:
    width = 0
    for char in text:
        if combining(char):
            continue
        width += 2 if east_asian_width(char) in {"W", "F"} else 1
    return width


def clip_display(text: str, width: int) -> str:
    text = text.replace("\n", " ").replace("`", "'")
    if display_width(text) <= width:
        return text
    if width <= 3:
        return "." * width
    result = []
    used = 0
    limit = width - 3
    for char in text:
        char_width = 0 if combining(char) else (2 if east_asian_width(char) in {"W", "F"} else 1)
        if used + char_width > limit:
            break
        result.append(char)
        used += char_width
    return "".join(result) + "..."


def pad_display(text: str, width: int) -> str:
    text = clip_display(text, width)
    return text + " " * max(0, width - display_width(text))


def table_row(values: list[str], widths: list[int]) -> str:
    cells = [pad_display(value, width) for value, width in zip(values[:-1], widths[:-1], strict=True)]
    cells.append(clip_display(values[-1], widths[-1]))
    return "  ".join(cells)


def fit_table(
    header: str,
    columns: list[str],
    rows: list[list[str]],
    widths: list[int],
    *,
    max_chars: int = MAX_DISCORD_TEXT,
) -> str:
    table_lines = [table_row(columns, widths), table_row(["-" * width for width in widths], widths)]
    shown = 0
    for row in rows:
        line = table_row(row, widths)
        remaining = len(rows) - shown - 1
        suffix = f"\n```\n… 외 {remaining}개 (검색어를 넣어 범위를 줄일 수 있어요)" if remaining else "\n```"
        candidate = header + "\n```text\n" + "\n".join(table_lines + [line]) + suffix
        if len(candidate) > max_chars:
            break
        table_lines.append(line)
        shown += 1
    hidden = len(rows) - shown
    result = header + "\n```text\n" + "\n".join(table_lines) + "\n```"
    if hidden:
        result += f"\n… 외 {hidden}개 (검색어를 넣어 범위를 줄일 수 있어요)"
    return result[:max_chars]


def text_attachment(filename: str, text: str) -> discord.File:
    safe_name = "".join(char if char.isalnum() or char in "._-" else "_" for char in filename)
    if not safe_name.endswith(".txt"):
        safe_name += ".txt"
    return discord.File(BytesIO(text.encode("utf-8")), filename=safe_name)


async def send_text_or_attachment(
    interaction: discord.Interaction,
    text: str,
    *,
    filename: str,
    attachment_text: str | None = None,
):
    if len(text) <= MAX_DISCORD_TEXT and attachment_text is None:
        await interaction.response.send_message(text, ephemeral=True)
        return
    file = text_attachment(filename, attachment_text or text)
    summary = text if len(text) <= MAX_DISCORD_TEXT else text[:1500] + "\n\n전체 내용은 첨부 파일로 보냈어요."
    await interaction.response.send_message(summary, file=file, ephemeral=True)
