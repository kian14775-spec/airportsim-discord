"""Discord's newer message layout (Components V2): one tidy card per message with a banner
on top, headed sections, thin dividers and the buttons/menus inside the card.

    view = card_view(banner="support", accent=ui.BRAND, parts=[
        title("Support", "Pick a topic below."),
        divider(),
        section("Game help", "Stuck or confused in game"),
        row(SomeSelect()),
    ])
    await post_card("support", channel, view)

Banners are PNGs in assets/banners, sent as attachments with the message (attachment://).
"""
from __future__ import annotations


import hashlib

import discord

from . import db
from .core import log
from .paths import ROOT

BANNERS = ROOT / "assets" / "banners"


def banner_file(name: str) -> discord.File | None:
    p = BANNERS / f"{name}.png"
    return discord.File(p, filename=f"{name}.png") if p.exists() else None


def banner(name: str) -> discord.ui.MediaGallery | None:
    if not (BANNERS / f"{name}.png").exists():
        return None
    return discord.ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{name}.png"))


def title(text: str, intro: str | None = None) -> discord.ui.TextDisplay:
    return discord.ui.TextDisplay(f"## {text}" + (f"\n{intro}" if intro else ""))


def text(body: str) -> discord.ui.TextDisplay:
    return discord.ui.TextDisplay(body)


def section(heading: str, body: str) -> discord.ui.TextDisplay:
    return discord.ui.TextDisplay(f"### {heading}\n{body}")


def small(body: str) -> discord.ui.TextDisplay:
    return discord.ui.TextDisplay(f"-# {body}")


def divider(big: bool = False) -> discord.ui.Separator:
    return discord.ui.Separator(spacing=discord.SeparatorSpacing.large if big else discord.SeparatorSpacing.small)


def row(*items: discord.ui.Item) -> discord.ui.ActionRow:
    return discord.ui.ActionRow(*items)


def container(*parts, accent: int | None = None, banner_name: str | None = None) -> discord.ui.Container:
    items = [banner(banner_name)] if banner_name and banner(banner_name) else []
    items += [p for p in parts if p is not None]
    return discord.ui.Container(*items, accent_colour=accent)


class CardView(discord.ui.LayoutView):
    def __init__(self, *containers: discord.ui.Container, timeout: float | None = None):
        super().__init__(timeout=timeout)
        for c in containers:
            self.add_item(c)


def banners_hash(names) -> str:
    """Changes when any banner image changes, so cards re-upload new artwork by themselves."""
    h = hashlib.md5()
    for n in names:
        p = BANNERS / f"{n}.png"
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()


def files_for(*names: str) -> list[discord.File]:
    return [f for f in (banner_file(n) for n in names if n) if f]


async def post_card(name: str, channel, view: discord.ui.LayoutView, banners: tuple[str, ...] = ()):
    """Post once, then edit in place (keeps the banner attachments). Older plain-embed versions of the
    same panel are replaced, because a message can't switch to the new layout by editing."""
    if channel is None:
        log.warning("card %s: channel missing", name)
        return None
    row_ = db.panel(name)
    if row_ and row_["channel_id"] == channel.id:
        try:
            msg = await channel.fetch_message(row_["message_id"])
            if msg.flags.components_v2:
                key, now = f"card_art:{name}", banners_hash(banners)
                if banners and db.kv_get(key) != now:      # new banner artwork: swap the attachments
                    await msg.edit(view=view, attachments=files_for(*banners))
                    db.kv_set(key, now)
                else:
                    await msg.edit(view=view)
                return msg
            if msg.author.id == channel.guild.me.id:
                await msg.delete()            # the old embed version of this panel (the bot's own post)
        except discord.NotFound:
            pass
    msg = await channel.send(view=view, files=files_for(*banners), allowed_mentions=discord.AllowedMentions.none())
    db.set_panel(name, channel.id, msg.id)
    db.kv_set(f"card_art:{name}", banners_hash(banners))
    return msg


async def forum_card(key: str, forum: discord.ForumChannel, name: str, view: discord.ui.LayoutView,
                     banners: tuple[str, ...] = (), tags=None, pin: bool = True, lock: bool = True):
    """Keep one bot-owned forum post (guide, templates, board) as a card. An older plain post with the
    same job is replaced, since a post can't switch to the new layout by editing."""
    tid = db.kv_get(key)
    thread = None
    if tid:
        thread = forum.guild.get_thread(tid)
        if thread is None:
            try:
                thread = await forum.guild.fetch_channel(tid)
            except discord.HTTPException:
                thread = None
    if thread is not None:
        if thread.name != name and thread.owner_id == forum.guild.me.id:
            try:
                await thread.edit(name=name)
            except discord.HTTPException:
                pass
        try:
            starter = await thread.fetch_message(thread.id)
            if starter.flags.components_v2:
                art, now = f"card_art:{key}", banners_hash(banners)
                if banners and db.kv_get(art) != now:
                    await starter.edit(view=view, attachments=files_for(*banners))
                    db.kv_set(art, now)
                else:
                    await starter.edit(view=view)
                return thread
            if thread.owner_id == forum.guild.me.id:
                await thread.delete()          # old plain version, the bot's own post
        except discord.HTTPException as e:
            log.warning("forum card %s: %s", key, e)
            return thread
    made = await forum.create_thread(name=name, view=view, files=files_for(*banners), applied_tags=tags or [])
    db.kv_set(key, made.thread.id)
    db.kv_set(f"card_art:{key}", banners_hash(banners))
    if pin or lock:
        try:
            await made.thread.edit(pinned=pin, locked=lock)
        except discord.HTTPException as e:
            log.warning("forum card pin %s: %s", key, e)
    return made.thread
