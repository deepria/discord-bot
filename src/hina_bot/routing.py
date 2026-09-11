import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Scope:
    guild_id: int | None
    channel_id: int
    user_id: int
    public_at_capture: bool = False

    @property
    def realm(self):
        return f"guild:{self.guild_id}" if self.guild_id is not None else f"dm:{self.user_id}"

    @property
    def channel(self):
        return f"{self.realm}:channel:{self.channel_id}"

    @property
    def conversation(self):
        return f"{self.realm}:channel:{self.channel_id}:user:{self.user_id}"

    @property
    def user_note(self):
        return f"{self.realm}:user:{self.user_id}"


def trigger_text(message, bot_id: int, dm_always_reply: bool = False) -> str | None:
    if message.author.bot or message.webhook_id is not None:
        return None
    raw = message.content.lstrip()
    # Discord includes the replied-to author in mentions only when reply ping is enabled.
    # Do not infer a ping merely from message.reference.
    ping = any(user.id == bot_id for user in message.mentions)
    keyword = raw.startswith("히나야")
    if not (ping or keyword or (message.guild is None and dm_always_reply)):
        return None
    raw = re.sub(rf"<@!?{bot_id}>", "", raw).lstrip()
    if raw.startswith("히나야"):
        raw = raw[3:].lstrip(" \t\n,:：!！?？~")
    return raw.strip()


def chunks(text: str, limit: int = 1900):
    # Count UTF-16 units conservatively, including emoji; Discord limit is 2000.
    part, size = [], 0
    for char in re.findall(r"<a?:[^:<>\s]{1,32}:\d{1,20}>|.", text, flags=re.DOTALL):
        n = len(char.encode("utf-16-le")) // 2
        if size + n > limit:
            yield "".join(part)
            part, size = [], 0
        part.append(char)
        size += n
    if part:
        yield "".join(part)
