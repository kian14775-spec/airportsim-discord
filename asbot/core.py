"""Shared helpers: settings, layout lookups, staff checks, embeds, Roblox web API."""
from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from pathlib import Path

import aiohttp
import discord
import yaml

from .layout import sync

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("asbot")


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


# #logs has three threads; older names map onto them
LOG_THREADS = {"Verifications": "Members", "Joins and leaves": "Members", "Members": "Members",
               "Tickets": "Tickets and applications", "Applications": "Tickets and applications",
               "Moderation": "Moderation", "Bot": "Moderation", "Messages": "Messages"}


async def _log_thread(guild: discord.Guild, kind: str) -> discord.abc.Messageable | None:
    """#logs is split into one thread per kind, made on first use."""
    from . import db
    ch = channel(guild, "logs")
    if not isinstance(ch, discord.TextChannel):
        return None
    tid = db.kv_get(f"log_thread:{kind}")
    th = guild.get_thread(tid) if tid else None
    if th is None and tid:
        try:
            th = await guild.fetch_channel(tid)
        except discord.HTTPException:
            th = None
    if th is None:
        th = await ch.create_thread(name=kind, type=discord.ChannelType.public_thread,
                                    auto_archive_duration=10080)
        db.kv_set(f"log_thread:{kind}", th.id)
    return th


async def log_staff(guild: discord.Guild, text: str, kind: str = "Bot", **kw) -> None:
    try:
        target = await _log_thread(guild, LOG_THREADS.get(kind, "Moderation"))
        if target:
            await target.send(text or None, allowed_mentions=discord.AllowedMentions.none(), **kw)
    except discord.HTTPException as e:
        log.warning("could not write to #logs (%s): %s", kind, e)


# ---- Roblox public web API (no key needed) ----
_session: aiohttp.ClientSession | None = None


def http() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15),
                                         headers={"User-Agent": "AirportSimulatorBot/1.0"})
    return _session


async def roblox_user_by_name(name: str) -> dict | None:
    async with http().post("https://users.roblox.com/v1/usernames/users",
                           json={"usernames": [name], "excludeBannedUsers": False}) as r:
        data = (await r.json()).get("data", [])
    return data[0] if data else None


async def roblox_user(user_id: int) -> dict | None:
    async with http().get(f"https://users.roblox.com/v1/users/{user_id}") as r:
        return await r.json() if r.status == 200 else None


async def roblox_headshot(user_id: int) -> str | None:
    url = (f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={user_id}"
           "&size=150x150&format=Png&isCircular=false")
    async with http().get(url) as r:
        data = (await r.json()).get("data", []) if r.status == 200 else []
    return data[0].get("imageUrl") if data else None


async def roblox_group_rank(user_id: int, group_id: int) -> str | None:
    async with http().get(f"https://groups.roblox.com/v2/users/{user_id}/groups/roles") as r:
        data = (await r.json()).get("data", []) if r.status == 200 else []
    for g in data:
        if g["group"]["id"] == group_id:
            return g["role"]["name"]
    return None


async def group_info(group_id: int) -> dict | None:
    async with http().get(f"https://groups.roblox.com/v1/groups/{group_id}") as r:
        return await r.json() if r.status == 200 else None


async def game_votes() -> dict | None:
    uid = cfg()["game"]["universe_id"]
    async with http().get(f"https://games.roblox.com/v1/games/votes?universeIds={uid}") as r:
        data = (await r.json()).get("data", []) if r.status == 200 else []
    return data[0] if data else None


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
