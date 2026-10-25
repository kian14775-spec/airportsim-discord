"""Shared preview/apply used by the CLI and the /layout command."""
from __future__ import annotations

import json
from pathlib import Path

import discord

from . import model
from .discord_backend import DiscordBackend, snapshot
from .executor import execute
from .planner import Plan, plan
from ..paths import DATA

ROOT = Path(__file__).resolve().parents[2]
LAYOUT_FILE = ROOT / "layout" / "server.yaml"
STATE_DIR = DATA / "state"


def _state_file(guild_id: int) -> Path:
    return STATE_DIR / f"ids_{guild_id}.json"


def load_state(guild_id: int) -> dict[str, int]:
    f = _state_file(guild_id)
    return json.loads(f.read_text()) if f.exists() else {}


def save_state(guild_id: int, state: dict[str, int]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _state_file(guild_id).with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(_state_file(guild_id))


def channel_id(guild_id: int, key: str) -> int | None:
    """For features: the id of a layout channel, e.g. channel_id(g, 'inbox')."""
    return load_state(guild_id).get(f"ch:{key}")


def role_id(guild_id: int, key: str) -> int | None:
    return load_state(guild_id).get(f"role:{key}")


def make_plan(guild: discord.Guild, archive_extras: bool = False) -> tuple[model.Layout, Plan]:
    layout = model.load(LAYOUT_FILE)
    from .. import db   # party voice channels are the bot's own, not leftovers
    state, snap = load_state(guild.id), snapshot(guild)
    parties = {r["channel_id"] for r in db.q("SELECT channel_id FROM parties")}
    hub = snap.channels.get(state.get("ch:create_party"))
    if hub:   # also any other voice channel next to the hub (made by the bot running elsewhere)
        parties |= {c.id for c in snap.channels.values()
                    if c.type == "voice" and c.parent == hub.parent and c.id != hub.id}
    kept = keep_manual_edits(layout, snap, state, load_applied(guild.id))
    p = plan(layout, snap, state, archive_extras=archive_extras, ignore=parties)
    p.kept = kept
    return layout, p


# ---- manual edits win: if someone renamed a channel (or changed a topic) by hand since the
# last apply, and the layout file still has the old value, keep the person's version.
def _applied_file(guild_id: int) -> Path:
    return STATE_DIR / f"applied_{guild_id}.json"


def load_applied(guild_id: int) -> dict:
    f = _applied_file(guild_id)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def save_applied(guild_id: int, layout: model.Layout) -> None:
    out = {}
    for c in layout.categories:
        if not c.top:
            out[f"cat:{c.key}"] = {"name": c.name}
        for ch in c.channels:
            out[f"ch:{ch.key}"] = {"name": ch.name, "topic": ch.topic}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _applied_file(guild_id).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


def keep_manual_edits(layout: model.Layout, snap, state: dict, applied: dict) -> list[str]:
    kept = []

    def adopt(spec, ref: str, fields):
        live = snap.channels.get(state.get(ref))
        prev = applied.get(ref)
        if not live or not prev:
            return
        for f in fields:
            have, was, want = getattr(live, f), prev.get(f), getattr(spec, f)
            if (have or None) != (was or None) and (want or None) == (was or None):
                setattr(spec, f, have)
                kept.append(f"kept manual change: {ref.split(':', 1)[1]} {f} = {have!r} (update the layout file)")

    for c in layout.categories:
        if not c.top:
            adopt(c, f"cat:{c.key}", ("name",))
        for ch in c.channels:
            adopt(ch, f"ch:{ch.key}", ("name", "topic") if ch.type != "voice" else ("name",))
    return kept


def describe(p: Plan) -> str:
    lines = [a.text for a in p.changes] or ["Server already matches the layout."]
    lines += ["! " + k for k in getattr(p, "kept", [])]
    if p.errors:
        lines = ["CAN'T APPLY YET:"] + ["! " + e for e in p.errors] + [""] + lines
    old = [c for c in p.extras if c.type != "category"]
    if old and not any(a.op == "archive" for a in p.actions):
        lines += ["", f"Not in the layout ({len(old)}), left alone: " + ", ".join("#" + c.name for c in old),
                  "(apply --archive-extras moves them into a hidden ARCHIVE category)"]
    return "\n".join(lines)


async def apply(guild: discord.Guild, archive_extras: bool = False, log=print) -> Plan:
    layout, p = make_plan(guild, archive_extras)
    if p.errors:
        raise RuntimeError("\n".join(p.errors))
    state = load_state(guild.id)
    try:
        await execute(p, layout, DiscordBackend(guild), state, log=log)
        save_applied(guild.id, layout)
    finally:
        save_state(guild.id, state)
    return p
