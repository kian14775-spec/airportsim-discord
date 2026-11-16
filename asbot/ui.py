"""The bot's one visual style. Every message the bot sends is built here.

Rules: cards (cards.container) have NO coloured side stripe since 2026-11-16 (Kian: banners, headings
and dividers only). The colours below are for the old-style embeds (logs) and stay meaningful there:
green = done, amber = waiting, red = problem. One small icon per title at most. Footer = the game name.
"""
from __future__ import annotations

import discord

NEUTRAL = 0x2B2D31   # log embeds: blends into the chat
SKY = 0x4282BD       # the banners' blue
BRAND = SKY          # info
SLATE = SKY          # (old name)
GOOD = 0x3BA55C      # done, accepted, online
WAIT = 0xE0A33A      # waiting, pending, warnings
BAD = 0xE5484D       # problems, denied, down
QUIET = 0x80848E     # closed, archived

TONES = {"neutral": NEUTRAL, "brand": BRAND, "good": GOOD, "wait": WAIT, "bad": BAD, "quiet": QUIET}

FOOTER = "Airport Simulator"
_icon_url: str | None = None   # set once from the server icon


def set_icon(url: str | None) -> None:
    global _icon_url
    _icon_url = url


def card(title: str | None = None, body: str | None = None, *, tone: str | int = "neutral",
         fields: list[tuple[str, str, bool]] | None = None, footer: str | None = FOOTER,
         thumb: str | None = None, image: str | None = None) -> discord.Embed:
    colour = TONES[tone] if isinstance(tone, str) else tone
    e = discord.Embed(title=title, description=body, colour=discord.Colour(colour))
    for name, value, inline in fields or []:
        e.add_field(name=name, value=value or "-", inline=inline)
    if footer:
        e.set_footer(text=footer, icon_url=_icon_url)
    if thumb:
        e.set_thumbnail(url=thumb)
    if image:
        e.set_image(url=image)
    return e


def kv(*pairs: tuple[str, str]) -> str:
    """Neat two-column style lines: `Status` New · `Priority` Normal"""
    return "\n".join(f"**{k}** · {v}" for k, v in pairs)
