"""Deterministic Discord output guards; model instructions are not a security boundary."""
import re

# Use a full-width at-sign so the intended text stays readable but Discord cannot notify everyone
# or a role. Individual user mentions are safe to preserve when AllowedMentions permits users.
DISCORD_MENTION = re.compile(r"@(?:everyone|here)|<@&\d{1,20}>", re.IGNORECASE)


def neutralize_mentions(text: str) -> str:
    return DISCORD_MENTION.sub(lambda match: match.group(0).replace("@", "＠"), text)
