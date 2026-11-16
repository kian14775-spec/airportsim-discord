"""Roblox Open Cloud: game bans (user restrictions) and the DiscordPerks DataStore.

Needs an API key made for the Airport Simulator group with these permissions on the
experience: user-restrictions read + write, DataStore read + write. Put it in .env as
ROBLOX_OPENCLOUD_KEY. Without it these features say "not set up" instead of failing.
"""
from __future__ import annotations

import json
import os

import aiohttp

from .core import cfg, http, log

BASE = "https://apis.roblox.com"


def _key() -> str | None:
    return os.environ.get("ROBLOX_OPENCLOUD_KEY") or None


def available() -> bool:
    return _key() is not None


def _universe() -> int:
    return cfg()["game"]["universe_id"]


async def _call(method: str, url: str, **kw) -> tuple[int, dict]:
    """(status, json). Status 0 when Roblox can't be reached, so callers see "no ban" / "failed"."""
    headers = {"x-api-key": _key(), "content-type": "application/json"}
    try:
        async with http().request(method, url, headers=headers, **kw) as r:
            try:
                data = await r.json(content_type=None)
            except (json.JSONDecodeError, ValueError):
                data = {}
            if r.status >= 400:
                log.warning("open cloud %s %s -> %s %s", method, url.split("?")[0], r.status, str(data)[:200])
            return r.status, data if isinstance(data, dict) else {}
    except (aiohttp.ClientError, TimeoutError) as e:
        log.warning("open cloud %s %s: %s", method, url.split("?")[0], e)
        return 0, {}


async def get_ban(user_id: int) -> dict | None:
    """The player's game ban, or None if not banned / unknown."""
    if not available():
        return None
    status, data = await _call("GET", f"{BASE}/cloud/v2/universes/{_universe()}/user-restrictions/{user_id}")
    ban = data.get("gameJoinRestriction") if status == 200 else None
    return ban if ban and ban.get("active") else None


async def unban(user_id: int) -> bool:
    if not available():
        return False
    status, _ = await _call(
        "PATCH",
        f"{BASE}/cloud/v2/universes/{_universe()}/user-restrictions/{user_id}?updateMask=gameJoinRestriction",
        json={"gameJoinRestriction": {"active": False}})
    return status == 200


async def set_perks(roblox_id: int, perks: dict) -> bool:
    if not available() or not cfg()["perks"].get("enabled"):
        return False
    url = (f"{BASE}/datastores/v1/universes/{_universe()}/standard-datastores/datastore/entries/entry"
           f"?datastoreName={cfg()['perks']['datastore']}&entryKey={roblox_id}")
    status, _ = await _call("POST", url, data=json.dumps(perks))
    return status == 200
