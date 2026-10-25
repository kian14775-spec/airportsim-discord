"""Bot-owned messages (welcome, roles, support, status...) that stay up to date.

ensure_panel posts the message once and edits it in place on every start, so
changing a text in config/bot.yaml and restarting updates the live message.
"""
from __future__ import annotations

import discord

from . import db
from .core import log


async def ensure_panel(name: str, channel: discord.abc.Messageable | None, *, embeds: list[discord.Embed],
                       view: discord.ui.View | None = None, pin: bool = False) -> discord.Message | None:
    if channel is None:
        log.warning("panel %s: channel missing (layout not applied?)", name)
        return None
    row = db.panel(name)
    if row and row["channel_id"] == channel.id:
        try:
            msg = await channel.fetch_message(row["message_id"])
            await msg.edit(embeds=embeds, view=view)
            return msg
        except discord.NotFound:
            pass
    msg = await channel.send(embeds=embeds, view=view)
    db.set_panel(name, channel.id, msg.id)
    if pin:
        try:
            await msg.pin()
        except discord.HTTPException:
            pass
    return msg
