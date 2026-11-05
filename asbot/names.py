"""Real names for staff cards. Discord only turns <@id> in an embed into a name if the viewer's
app already knows that person, so anyone who left shows as a raw number that opens "You don't
have access to this link". Cards write the name as text instead, and it stays readable forever.
Names of everyone who was ever here live in the `names` table, so they survive a leave."""
from __future__ import annotations

import logging
import re
import time

import discord

from . import db

log = logging.getLogger("asbot")
client: discord.Client | None = None   # set by the modlog cog

db.conn().executescript("""
CREATE TABLE IF NOT EXISTS names (id INTEGER PRIMARY KEY, display TEXT NOT NULL, handle TEXT NOT NULL, seen REAL NOT NULL);
""")

MENTION = re.compile(r"<@!?(\d+)>")
UNSAFE = re.compile(r"[\[\]()\n]")
LOOKS_LIKE_LINK = re.compile(r"\S\.\S|://")   # Discord refuses a masked link whose text looks like a web address
MAX_LOOKUPS = 5                               # per card, so a mass-ping can't flood the API
URL = "https://discord.com/users/{}"
UPSERT = ("INSERT INTO names VALUES(?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET display=excluded.display, "
          "handle=excluded.handle, seen=excluded.seen")


def _row(u) -> tuple:
    return u.id, u.display_name, u.name, time.time()


def remember(user: discord.abc.User) -> None:
    db.run(UPSERT, *_row(user))


def remember_all(users) -> None:
    c = db.conn()
    with c:
        c.executemany(UPSERT, [_row(u) for u in users])


def _member(uid: int) -> discord.Member | None:
    for g in (client.guilds if client else []):
        if (m := g.get_member(uid)):
            return m
    return None


def clean(name: str) -> str:
    return discord.utils.escape_markdown(UNSAFE.sub(" ", name).strip()) or "?"


def known(uid: int) -> bool:
    return _member(uid) is not None or db.one("SELECT 1 FROM names WHERE id=?", uid) is not None


def who(uid: int, link: bool = True) -> str:
    """How a person reads on a staff card: a profile link while they're here, bold text once
    they've gone (the link would only open Discord's no-access box), their id if never seen."""
    m = _member(uid)
    if m is None:
        row = db.one("SELECT display FROM names WHERE id=?", uid)
        return f"**{clean(row['display'])}**" if row else f"user `{uid}`"
    name = clean(m.display_name)
    return f"[{name}]({URL.format(uid)})" if link and not LOOKS_LIKE_LINK.search(name) else f"**{name}**"


def swap(text: str, limit: int | None = None) -> str:
    """Every <@id> in a card -> who(id). Falls back to plain names if links would run over `limit`."""
    out = MENTION.sub(lambda m: who(int(m[1])), text)
    if limit and len(out) > limit:
        out = MENTION.sub(lambda m: who(int(m[1]), link=False), text)
    return out[:limit] if limit else out


async def fill(text: str, limit: int | None = None) -> str:
    """swap(), asking Discord first about anyone never seen (left before this table existed)."""
    missing = {int(i) for i in MENTION.findall(text)}
    for uid in [u for u in missing if not known(u)][:MAX_LOOKUPS]:
        try:
            remember(await client.fetch_user(uid))
        except (discord.HTTPException, AttributeError) as e:
            log.info("no name for %s: %s", uid, e)
    return swap(text, limit)


async def fill_embed(e: discord.Embed) -> None:
    if e.description:
        e.description = await fill(e.description, 4096)
    for i, f in enumerate(e.fields):
        if MENTION.search(f.value or ""):
            e.set_field_at(i, name=f.name, value=await fill(f.value, 1024), inline=f.inline)
