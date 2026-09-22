"""Keep inline quoted material separate from the current speaker's own words."""

import re

_SINGLE_LINE_QUOTE = re.compile(r"^\s{0,3}>\s+(.*)$")
_BLOCK_QUOTE = re.compile(r"^\s{0,3}>>>\s?(.*)$")


def split_inline_quotes(content: str) -> tuple[str, str]:
    """Return direct user text and Discord Markdown quote text independently.

    Markdown quote blocks do not carry a reliable Discord author ID. Treating them as the
    current author would let an attributed third-party statement become that user's memory, so
    they deliberately have no owner and never enter the persistent turn payload.
    """
    direct, quoted = [], []
    in_block_quote = False
    for line in str(content or "").splitlines():
        block = _BLOCK_QUOTE.match(line)
        if block:
            in_block_quote = True
            if block.group(1):
                quoted.append(block.group(1))
            continue
        if in_block_quote:
            quoted.append(line)
            continue
        single = _SINGLE_LINE_QUOTE.match(line)
        if single:
            quoted.append(single.group(1))
        else:
            direct.append(line)
    return "\n".join(direct).strip(), "\n".join(quoted).strip()


__all__ = ["split_inline_quotes"]
