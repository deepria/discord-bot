from datetime import datetime

MAX_DISCORD_TEXT = 1900


def created_timestamp(row: dict) -> float | None:
    value = row.get("created_at")
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def created_label(row: dict) -> str:
    timestamp = created_timestamp(row)
    if timestamp is None:
        return "시간 미기록"
    return f"<t:{int(timestamp)}:R>"


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
    # Default: insertion/creation time, oldest first. Legacy rows without timestamps stay first.
    return sorted(
        rows,
        key=lambda item: (created_timestamp(row_of(item)) is not None,
                          created_timestamp(row_of(item)) or 0),
    )


def fit_list(header: str, entries: list[str], *, max_chars: int = MAX_DISCORD_TEXT) -> str:
    lines = [header]
    shown = 0
    for entry in entries:
        remaining = len(entries) - shown - 1
        suffix = f"\n… 외 {remaining}개" if remaining else ""
        candidate = "\n".join(lines + [entry]) + suffix
        if len(candidate) > max_chars:
            break
        lines.append(entry)
        shown += 1
    hidden = len(entries) - shown
    if hidden:
        lines.append(f"… 외 {hidden}개 (검색어를 넣어 범위를 줄일 수 있어요)")
    return "\n".join(lines)[:max_chars]
