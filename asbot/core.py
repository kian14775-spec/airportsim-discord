"""Shared helpers: settings, layout lookups, staff checks, embeds, Roblox web API."""
from __future__ import annotations

import functools
import logging
import os
import re
import shutil
from functools import lru_cache
from pathlib import Path

import aiohttp
import discord
import yaml

from .layout import sync
from .paths import DATA

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("asbot")
MIN_FREE_MB = 50   # below this the bot stops saving files (ticket screenshots, log image copies)


def guarded(fn):
    """For tasks.loop bodies: an error is logged and the loop runs again next time.
    discord.py ends a loop for good on anything but a network error (a 5xx, a 403, sqlite...)."""
    @functools.wraps(fn)   # keeps the name: tasks.Loop stores its bound copy under it
    async def run(*args, **kw):
        try:
            return await fn(*args, **kw)
        except Exception:
            log.exception("loop %s failed, it runs again next time", fn.__qualname__)
    return run


def disk_ok(size: int = 0, what: str = "file") -> bool:
    """Room on the data volume for `size` more bytes while keeping MIN_FREE_MB free. Callers skip saving if not."""
    try:
        free = shutil.disk_usage(DATA).free
    except OSError:
        return True
    if free - size < MIN_FREE_MB * 1024 * 1024:
        log.warning("disk: %d MB free, not saving %s", free // 1048576, what)
        return False
    return True


@lru_cache(maxsize=1)
def cfg() -> dict:
    with open(ROOT / "config" / "bot.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def embed(title: str | None = None, description: str | None = None, *, colour: discord.Colour | None = None,
          tone: str = "neutral", **kw) -> discord.Embed:
    """Every bot message goes through ui.card, so they all share one look."""
    from . import ui
    return ui.card(title, description, tone=colour.value if colour else tone, **kw)


def channel(guild: discord.Guild, key: str):
    cid = sync.channel_id(guild.id, key)
    return guild.get_channel(cid) if cid else None


def role(guild: discord.Guild, key: str) -> discord.Role | None:
    rid = sync.role_id(guild.id, key)
    return guild.get_role(rid) if rid else None


def role_named(guild: discord.Guild, name: str) -> discord.Role | None:
    return discord.utils.get(guild.roles, name=name)


def staff_roles(guild: discord.Guild) -> list[discord.Role]:
    roles = [role(guild, "staff")] + [role_named(guild, n) for n in cfg().get("staff_role_names", [])]
    return [r for r in roles if r]


def is_staff(member: discord.abc.User) -> bool:
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator:
        return True
    ids = {r.id for r in staff_roles(member.guild)}
    return any(r.id in ids for r in member.roles)


def staff_only():
    async def pred(interaction: discord.Interaction) -> bool:
        if is_staff(interaction.user):
            return True
        await interaction.response.send_message("Staff only.", ephemeral=True)
        return False
    return discord.app_commands.check(pred)


# Three log channels (layout keys): #alerts needs action, #actions is what staff did,
# #activity is what members did. Callers say what a log is about; this says where it goes.
LOG_CHANNELS = {"Security": "log_security",
                "Moderation": "log_mod", "Members": "log_mod", "Server": "log_mod", "Bot": "log_mod",
                "Voice": "log_mod", "Tickets": "log_mod", "Applications": "log_mod",
                "Messages": "log_messages", "Activity": "log_messages", "Verifications": "log_messages"}
ALERT_HOURS = 24


def log_channel(guild: discord.Guild, kind: str) -> discord.TextChannel | None:
    ch = channel(guild, LOG_CHANNELS.get(kind, "log_mod"))
    return ch if isinstance(ch, discord.TextChannel) else None


def log_off(event: str | None) -> bool:
    """Is this log switched off? `logs: muted:` in config/bot.yaml, by event name."""
    return bool(event) and event in ((cfg().get("logs") or {}).get("muted") or [])


def note_alert(title: str | None) -> None:
    """Remember an #alerts post for the live status card."""
    from . import db
    now = db.now()
    kept = [a for a in db.kv_get("alerts:recent", []) if now - a[0] < ALERT_HOURS * 3600]
    db.kv_set("alerts:recent", kept + [[now, title or "Alert"]])


def log_card(title: str | None, body: str = "", *, tone: str = "neutral", user: discord.abc.User | None = None,
             by: discord.abc.Snowflake | None = None, reason: str | None = None, image: str | None = None,
             thumb: str | None = None) -> discord.Embed:
    """One look for every log line: who it's about on top (name + avatar), what happened,
    who did it and why, and their ids + the time at the bottom (searchable, copyable)."""
    from . import ui
    lines = [body] if body else []
    if by is not None:
        lines.append(f"-# by <@{by.id}>" + (f" · {reason[:300]}" if reason else ""))
    elif reason:
        lines.append(f"-# {reason[:300]}")
    e = ui.card(title, "\n".join(lines) or None, tone=tone, footer=None, image=image, thumb=thumb)
    ids = []
    if user is not None:
        e.set_author(name=f"{user.display_name} (@{user.name})" if user.display_name != user.name else user.name,
                     icon_url=user.display_avatar.url)
        ids.append(f"User {user.id}")
    if by is not None and (user is None or by.id != user.id):
        ids.append(f"By {by.id}")
    e.set_footer(text=" · ".join(ids) or None)
    e.timestamp = discord.utils.utcnow()
    return e


async def log_staff(guild: discord.Guild, text: str, kind: str = "Bot", event: str | None = None,
                    **kw) -> discord.Message | None:
    """Post to the log channel for `kind`, unless `event` is switched off. Plain text becomes a
    small card so every log channel reads the same; callers can still pass their own embed/view/files."""
    if log_off(event):
        return None
    target = log_channel(guild, kind)
    if target is None:
        log.warning("no log channel for %s (layout key %s)", kind, LOG_CHANNELS.get(kind))
        return None
    if text and "embed" not in kw and "embeds" not in kw:
        kw["embed"] = log_card(None, text)
        text = ""
    if kind == "Security":
        note_alert(getattr(kw.get("embed"), "title", None))
    from . import names
    for e in ([kw["embed"]] if kw.get("embed") else []) + list(kw.get("embeds") or []):
        await names.fill_embed(e)
    try:
        return await target.send(text or None, allowed_mentions=kw.pop("allowed_mentions", discord.AllowedMentions.none()),
                                 **kw)
    except discord.HTTPException as e:
        log.warning("could not write to %s (%s): %s", target, kind, e)
        return None


# ---- Roblox public web API (no key needed) ----
_session: aiohttp.ClientSession | None = None


def quiet(fn):
    """Roblox down, slow or answering with an HTML error page = None, never an exception."""
    @functools.wraps(fn)
    async def call(*args, **kw):
        try:
            return await fn(*args, **kw)
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError) as e:
            log.warning("roblox %s: %s", fn.__name__, e)
            return None
    return call


def http() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15),
                                         headers={"User-Agent": "AirportSimulatorBot/1.0"})
    return _session


@quiet
async def roblox_user_by_name(name: str) -> dict | None:
    async with http().post("https://users.roblox.com/v1/usernames/users",
                           json={"usernames": [name], "excludeBannedUsers": False}) as r:
        data = (await r.json()).get("data", [])
    return data[0] if data else None


@quiet
async def roblox_user(user_id: int) -> dict | None:
    async with http().get(f"https://users.roblox.com/v1/users/{user_id}") as r:
        return await r.json() if r.status == 200 else None


@quiet
async def roblox_headshot(user_id: int) -> str | None:
    url = (f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={user_id}"
           "&size=150x150&format=Png&isCircular=false")
    async with http().get(url) as r:
        data = (await r.json()).get("data", []) if r.status == 200 else []
    return data[0].get("imageUrl") if data else None


@quiet
async def roblox_group_rank(user_id: int, group_id: int) -> str | None:
    async with http().get(f"https://groups.roblox.com/v2/users/{user_id}/groups/roles") as r:
        data = (await r.json()).get("data", []) if r.status == 200 else []
    for g in data:
        if g["group"]["id"] == group_id:
            return g["role"]["name"]
    return None


@quiet
async def group_info(group_id: int) -> dict | None:
    async with http().get(f"https://groups.roblox.com/v1/groups/{group_id}") as r:
        return await r.json() if r.status == 200 else None


@quiet
async def game_votes() -> dict | None:
    uid = cfg()["game"]["universe_id"]
    async with http().get(f"https://games.roblox.com/v1/games/votes?universeIds={uid}") as r:
        data = (await r.json()).get("data", []) if r.status == 200 else []
    return data[0] if data else None


@quiet
async def game_stats() -> dict | None:
    uid = cfg()["game"]["universe_id"]
    async with http().get(f"https://games.roblox.com/v1/games?universeIds={uid}") as r:
        data = (await r.json()).get("data", []) if r.status == 200 else []
    return data[0] if data else None


def ensure_list(x):
    return x if isinstance(x, list) else [x]


NICK_AT = re.compile(r"@([A-Za-z0-9_]{3,20})")
NICK_FIRST = re.compile(r"^([A-Za-z0-9_]{3,20})\b")


async def linked_roblox(guild: discord.Guild, user: discord.abc.User) -> tuple[int, str, str] | None:
    """(roblox_id, roblox_name, how) for the account a member verified with.
    1. linked through this bot, 2. Bloxlink's API (needs BLOXLINK_KEY, free in Bloxlink's
    dashboard), 3. their server nickname (Bloxlink renames verified members) checked on Roblox."""
    from . import db
    link = db.one("SELECT * FROM links WHERE discord_id=?", user.id)
    if link:
        return link["roblox_id"], link["roblox_name"], "verified"
    key = os.getenv("BLOXLINK_KEY")
    if key:
        try:
            async with http().get(f"https://api.blox.link/v4/public/guilds/{guild.id}/discord-to-roblox/{user.id}",
                                  headers={"Authorization": key}) as r:
                data = await r.json() if r.status == 200 else {}
            rid = int(data.get("robloxID") or 0)
            if rid:
                info = await roblox_user(rid) or {}
                name = info.get("name") or str(rid)
                db.run("INSERT OR IGNORE INTO links VALUES(?, ?, ?, ?)", user.id, rid, name, db.now())
                return rid, name, "verified with Bloxlink"
        except (aiohttp.ClientError, ValueError, TimeoutError) as e:
            log.warning("bloxlink lookup: %s", e)
    member = guild.get_member(user.id)
    verified = role(guild, "verified")
    if member and verified and verified in member.roles and member.nick:
        m = NICK_AT.search(member.nick) or NICK_FIRST.search(member.nick)
        if m:
            try:
                found = await roblox_user_by_name(m.group(1))
            except (aiohttp.ClientError, TimeoutError):
                found = None
            if found:
                return found["id"], found["name"], "from their nickname"
    return None
