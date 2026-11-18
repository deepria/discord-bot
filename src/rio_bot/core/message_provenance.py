"""Separate Discord dialogue from roleplay narration without losing scene order."""

import re

_SINGLE_LINE_QUOTE = re.compile(r"^ {0,3}>[ \t]+(.*)$")
_BLOCK_QUOTE = re.compile(r"^ {0,3}>>>[ \t]?(.*)$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def message_segments(content: str) -> list[dict[str, str]]:
    """Parse quote markup as narration, except inside fenced code blocks.

    A >>> block consumes the rest of the message. Segment order is significant;
    the message author is the narrator, not necessarily the actor in the scene.
    """
    segments: list[dict[str, str]] = []
    in_block = False
    fence = ""
    for line in str(content or "").splitlines():
        kind, value = "dialogue", line
        marker = _FENCE.match(line)
        if in_block:
            kind = "narration"
        elif fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = ""
        elif marker:
            fence = marker[1]
        else:
            block = _BLOCK_QUOTE.match(line)
            single = _SINGLE_LINE_QUOTE.match(line)
            if block:
                in_block = True
                kind, value = "narration", block[1]
            elif single:
                kind, value = "narration", single[1]
        if segments and segments[-1]["kind"] == kind:
            segments[-1]["content"] += "\n" + value
        else:
            segments.append({"kind": kind, "content": value})
    return [{**s, "content": s["content"].strip()} for s in segments if s["content"].strip()]


def split_inline_quotes(content: str) -> tuple[str, str]:
    """Compatibility helper: return dialogue and narration separately.

    Scene narration must never enter persistent memory as a user's factual statement.
    """
    segments = message_segments(content)
    return tuple("\n".join(s["content"] for s in segments if s["kind"] == kind)
                 for kind in ("dialogue", "narration"))


__all__ = ["message_segments", "split_inline_quotes"]
