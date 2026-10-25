"""Thread/channel renames without hitting Discord's limit (2 renames per 10 minutes).

rename_soon() keeps only the latest wanted name per channel and applies it as soon as
the window allows, so fast status changes never block the bot or get lost.
"""
from __future__ import annotations

import asyncio
import time

import discord

from .core import log

WINDOW = 330   # seconds between renames of one channel (2 per 10 min, with margin)
_last: dict[int, float] = {}
_wanted: dict[int, str] = {}
_tasks: dict[int, asyncio.Task] = {}


async def _apply(ch: discord.abc.GuildChannel | discord.Thread):
    try:
        wait = _last.get(ch.id, 0) + WINDOW - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        name = _wanted.pop(ch.id, None)
        if name and name != ch.name:
            await ch.edit(name=name[:100])
            _last[ch.id] = time.monotonic()
    except discord.NotFound:
        _wanted.pop(ch.id, None)
    except discord.HTTPException as e:
        log.warning("rename %s: %s", ch.id, e)
    finally:
        _tasks.pop(ch.id, None)


def rename_soon(ch: discord.abc.GuildChannel | discord.Thread, name: str) -> None:
    if ch.name == name and ch.id not in _wanted:
        return
    _wanted[ch.id] = name
    if ch.id not in _tasks:
        _tasks[ch.id] = asyncio.create_task(_apply(ch))
