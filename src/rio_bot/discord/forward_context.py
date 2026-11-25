"""Project visible forward snapshots without crossing a Discord channel boundary."""

import asyncio

import discord


def is_forward(message):
    reference = getattr(message, "reference", None)
    return getattr(reference, "type", None) == discord.MessageReferenceType.forward


def _identity(author):
    if getattr(author, "id", None) is None:
        return None
    return {"user_id": str(author.id), "name": str(getattr(author, "display_name", ""))[:100]}


async def _original_author(source, request, reference):
    channel = request.channel
    if (getattr(reference, "channel_id", None) != channel.id
            or getattr(reference, "guild_id", None) not in {None, getattr(request.guild, "id", None)}):
        return None
    message_id = getattr(reference, "message_id", None)
    if message_id is None:
        return None
    if request.guild is not None:
        bot = request.guild.me
        if bot is None:
            return None
        for member in (request.author, bot):
            permissions = channel.permissions_for(member)
            if not (permissions.view_channel and permissions.read_message_history):
                return None
    elif (channel.type != discord.ChannelType.private
          or getattr(getattr(channel, "recipient", None), "id", None) != request.author.id):
        return None
    try:
        async with asyncio.timeout(1):
            original = await channel.fetch_message(message_id)
    except (TimeoutError, discord.HTTPException):
        return None
    if (original.id != message_id or original.channel.id != source.channel.id
            or original.webhook_id is not None):
        return None
    return _identity(original.author)


async def forward_context_rows(source, request, relation, *, limit=2):
    if limit < 1 or not is_forward(source):
        return []
    snapshots = getattr(source, "message_snapshots", None) or []
    author = None
    if len(snapshots) == 1:
        author = await _original_author(source, request, source.reference)
    # An empty or attachment-only snapshot still has a forwarder, but supplies no text evidence.
    rows = []
    for snapshot in snapshots[:limit] or [None]:
        content = getattr(snapshot, "content", "") or ""
        rows.append({
            "relation": relation,
            "forwarding_message_id": str(source.id),
            "forwarded_by": _identity(source.author),
            "original_author": author,
            "content": content[:1500],
            "truncated": len(content) > 1500 or len(snapshots) > limit,
        })
    return rows
