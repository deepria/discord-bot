"""Recognize short-lived boundary requests from the current speaker's own dialogue."""

import re
import unicodedata
from datetime import UTC, datetime, timedelta

from ..core.message_provenance import message_segments

BOUNDARY_WINDOW = timedelta(minutes=15)

_REQUESTS = {
    "stop_analysis": re.compile(
        r"(?:이제\s*|제발\s*)?"
        r"(?:그만\s*분석해(?:\s*줘|요)?|"
        r"분석(?:은|을)?\s*(?:그만(?:해(?:요)?|\s*해\s*줘)|하지\s*마(?:요|세요)?))"
    ),
    "stop_topic": re.compile(
        r"(?:이제\s*|제발\s*)?(?:그|이)\s*(?:얘기|이야기|주제)(?:는|를|은|을)?\s*"
        r"(?:그만(?:해(?:\s*줘|요)?|하자)?|(?:먼저\s*)?꺼내지\s*마(?:요|세요)?)"
    ),
}
_END = re.compile(r"[.!?。！？~…\s]*\Z")
_NEXT_CLAUSE = re.compile(r"[,，.!?。！？…](?:\s|$)")
_REPORTED_SPEECH = re.compile(r"[,，.!?。！？…\s]*(?:라고|라는|라며)")


def _boundary_kind(dialogue: str, *, standalone: bool = False) -> str | None:
    text = unicodedata.normalize("NFC", dialogue).strip()
    for kind, pattern in _REQUESTS.items():
        match = pattern.match(text)
        if match is None:
            continue
        suffix = text[match.end():]
        if _END.fullmatch(suffix) or (
            not standalone and _NEXT_CLAUSE.match(suffix) and not _REPORTED_SPEECH.match(suffix)
        ):
            return kind
    return None


def is_standalone_boundary(content: str) -> bool:
    """Recognize a bare request that carries no additional long-term evidence."""
    segments = message_segments(content)
    return len(segments) == 1 and segments[0]["kind"] == "dialogue" and bool(
        _boundary_kind(segments[0]["content"], standalone=True)
    )


def _recent(at: str, now: datetime) -> bool:
    try:
        timestamp = datetime.fromisoformat(at)
    except (ValueError, TypeError):
        return False
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return now - BOUNDARY_WINDOW <= timestamp <= now


def _kinds(segments):
    return {_boundary_kind(segment["content"]) for segment in segments
            if segment["kind"] == "dialogue"}


def conversation_boundaries(scope, content, segments, channel_rows, saved_turns) -> list[dict]:
    """Project only boundary types, with current-turn precedence and no text copying."""
    now = datetime.now(UTC)
    recent_kinds = set()
    for row in saved_turns:
        if _recent(row["created_at"], now):
            recent_kinds.update(_kinds(message_segments(row["content"])))
    speaker, channel = str(scope.user_id), str(scope.channel_id)
    for row in channel_rows:
        if (
            row.get("role") == "user"
            and row["author_id"] == speaker
            and row["channel_id"] == channel
            and row.get("context_kind") == "speaker_thread"
            and row.get("direct_trigger") is True
            and not row.get("context_truncated")
            and _recent(row["timestamp"], now)
        ):
            recent_kinds.update(_kinds(row["message_segments"]))
    current_kinds = _kinds(segments or message_segments(content))
    return [
        {"kind": kind, "requested_this_turn": kind in current_kinds}
        for kind in _REQUESTS if kind in current_kinds or kind in recent_kinds
    ]
