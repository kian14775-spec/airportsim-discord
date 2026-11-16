"""Shared preview/apply used by the CLI and the /layout command."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import discord

from . import model
from .discord_backend import DiscordBackend, snapshot
from .executor import execute
from .planner import Plan, cat_ref, ch_ref, flag_names, plan, role_ref
from ..paths import DATA, STATE

ROOT = Path(__file__).resolve().parents[2]
LAYOUT_FILE = ROOT / "layout" / "server.yaml"
STATE_DIR = STATE
BACKUP_DIR = DATA / "backups"
BACKUPS_KEPT = 10


def _state_file(guild_id: int) -> Path:
    return STATE_DIR / f"ids_{guild_id}.json"


# Features look ids up on every message, so the file is parsed once and again only when it
# changes on disk (mtime + size), not on every lookup.
_ids_cache: dict[Path, tuple[tuple[int, int], dict[str, int]]] = {}


def _ids(guild_id: int) -> dict[str, int]:
    """The cached ids (shared, read-only). load_state() gives a copy to change."""
    f = _state_file(guild_id)
    try:
        st = f.stat()
    except FileNotFoundError:
        return {}
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _ids_cache.get(f)
    if hit is None or hit[0] != stamp:
        hit = _ids_cache[f] = (stamp, json.loads(f.read_text()))
    return hit[1]


def load_state(guild_id: int) -> dict[str, int]:
    return dict(_ids(guild_id))


def save_state(guild_id: int, state: dict[str, int]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _state_file(guild_id).with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(_state_file(guild_id))
    _ids_cache.pop(_state_file(guild_id), None)


def channel_id(guild_id: int, key: str) -> int | None:
    """For features: the id of a layout channel, e.g. channel_id(g, 'inbox')."""
    return _ids(guild_id).get(f"ch:{key}")


def role_id(guild_id: int, key: str) -> int | None:
    return _ids(guild_id).get(f"role:{key}")


def make_plan(guild: discord.Guild, archive_extras: bool = False) -> tuple[model.Layout, Plan]:
    layout = model.load(LAYOUT_FILE)
    from .. import db   # party voice channels are the bot's own, not leftovers
    state, snap = load_state(guild.id), snapshot(guild)
    parties = {r["channel_id"] for r in db.q("SELECT channel_id FROM parties")}
    hub = snap.channels.get(state.get("ch:create_party"))
    if hub:   # also any other voice channel next to the hub (made by the bot running elsewhere)
        parties |= {c.id for c in snap.channels.values()
                    if c.type == "voice" and c.parent == hub.parent and c.id != hub.id}
    return layout, build_plan(layout, snap, state, load_applied(guild.id), archive_extras, parties)


def build_plan(layout: model.Layout, snap, state: dict, applied: dict, archive_extras: bool = False,
               ignore: set[int] | None = None) -> Plan:
    """keep_manual_edits + plan. No Discord calls, so the tests run it on a fake server."""
    record = applied_record(layout)   # the yaml's own values, taken before hand edits are adopted
    kept = keep_manual_edits(layout, snap, state, applied)
    p = plan(layout, snap, state, archive_extras=archive_extras, ignore=ignore)
    p.kept, p.record, p.snap = kept, record, snap
    return p


# ---- manual edits win: if someone renamed a channel (or changed a topic) by hand since the
# last apply, and the layout file still has the old value, keep the person's version.
# applied_<guild>.json holds the LAYOUT FILE's values at the last apply (never the adopted hand
# edits), so a hand edit stays kept on every later apply until the yaml itself changes that field.
def _applied_file(guild_id: int) -> Path:
    return STATE_DIR / f"applied_{guild_id}.json"


def load_applied(guild_id: int) -> dict:
    f = _applied_file(guild_id)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def applied_record(layout: model.Layout) -> dict:
    out = {}
    for c in layout.categories:
        if not c.top:
            out[f"cat:{c.key}"] = {"name": c.name}
        for ch in c.channels:
            out[f"ch:{ch.key}"] = {"name": ch.name, "topic": ch.topic}
    return out


def save_applied(guild_id: int, record: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _applied_file(guild_id).with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_applied_file(guild_id))


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


# ---- backup: before every apply, the live values of everything the plan is about to change
def backup_of(guild_id: int, p: Plan) -> dict:
    """Names, topics, overwrites (with target type + bits), role settings and, when the plan
    reorders, every position. Enough to put a bad apply back by hand."""
    snap = p.snap
    roles, chans = [], []
    for a in p.changes:
        if a.op == "edit_role":
            roles.append(p.matches[role_ref(a.key)])
        elif a.op == "edit_category":
            chans.append(p.matches[cat_ref(a.key)])
        elif a.op == "edit_channel":
            chans.append(p.matches[ch_ref(a.key)])
        elif a.op == "archive":
            chans += a.data["ids"]
        elif a.op == "remove_category":
            chans.append(a.data["id"])

    def overwrite(ch, t: int, bits: tuple[int, int]) -> dict:
        member = t in ch.member_targets
        role = None if member else snap.roles.get(t)
        return {"id": t, "type": "member" if member else "role", "name": role.name if role else None,
                "allow": flag_names(bits[0]), "deny": flag_names(bits[1]), "bits": list(bits)}

    def channel(cid: int) -> dict:
        ch = snap.channels[cid]
        return {"id": ch.id, "name": ch.name, "type": ch.type, "parent": ch.parent, "position": ch.position,
                "topic": ch.topic, "slowmode": ch.slowmode, "tags": ch.tags, "require_tag": ch.require_tag,
                "layout": ch.layout, "overwrites": [overwrite(ch, t, b) for t, b in ch.overwrites.items()]}

    reorder = any(a.op == "reorder" for a in p.changes)
    return {
        "guild": guild_id,
        "made": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "plan": [a.text for a in p.changes],
        "roles": [{"id": r.id, "name": r.name, "permissions": r.permissions, "mentionable": r.mentionable,
                   "color": r.color, "hoist": r.hoist} for r in (snap.roles[i] for i in dict.fromkeys(roles))],
        "channels": [channel(c) for c in dict.fromkeys(chans) if c in snap.channels],
        "order": [{"id": c.id, "name": c.name, "parent": c.parent, "position": c.position}
                  for c in snap.channels.values()] if reorder else [],
    }


def write_backup(guild_id: int, p: Plan) -> Path:
    """DATA/backups/layout_<utc time>.json (the volume on Railway); only the newest 10 are kept."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    f, n = BACKUP_DIR / f"layout_{stamp}.json", 1
    while f.exists():
        f, n = BACKUP_DIR / f"layout_{stamp}_{n}.json", n + 1
    f.write_text(json.dumps(backup_of(guild_id, p), indent=1, ensure_ascii=False), encoding="utf-8")
    for old in sorted(BACKUP_DIR.glob("layout_*.json"))[:-BACKUPS_KEPT]:
        old.unlink(missing_ok=True)
    return f


def describe(p: Plan) -> str:
    lines = []
    for a in p.changes:
        lines += [a.text] + ["    " + d for d in a.details]
    lines = lines or ["Server already matches the layout."]
    lines += ["! " + k for k in p.kept]
    if p.errors:
        lines = ["CAN'T APPLY YET:"] + ["! " + e for e in p.errors] + [""] + lines
    old = [c for c in p.extras if c.type != "category"]
    if old and not any(a.op == "archive" for a in p.actions):
        lines += ["", f"Not in the layout ({len(old)}), left alone: " + ", ".join("#" + c.name for c in old),
                  "(apply --archive-extras moves them into a hidden ARCHIVE category)"]
    if p.changes and not p.errors:
        lines += ["", "Apply first saves the current values of all of the above to backups/layout_<time>.json."]
    return "\n".join(lines)


async def apply(guild: discord.Guild, archive_extras: bool = False, log=print) -> Plan:
    layout, p = make_plan(guild, archive_extras)
    await run_plan(guild.id, layout, p, DiscordBackend(guild), log=log)
    return p


async def run_plan(guild_id: int, layout: model.Layout, p: Plan, backend, log=print) -> Path:
    """Backup, apply, record. Used by apply() and by the tests (with a fake backend)."""
    if p.errors:
        raise RuntimeError("\n".join(p.errors))
    saved = write_backup(guild_id, p)
    log(f"(old values saved to backups/{saved.name})")
    state = load_state(guild_id)
    try:
        await execute(p, layout, backend, state, log=log)
        save_applied(guild_id, p.record)
    finally:
        save_state(guild_id, state)
    return saved
