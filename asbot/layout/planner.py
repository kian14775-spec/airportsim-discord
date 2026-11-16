"""Compares the layout with a snapshot of the server and lists the changes.

Pure code: no Discord calls, so it can be tested offline. The executor turns the
plan into real API calls (or fake ones in the tests).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from discord import Permissions

from .model import EVERYONE, CategorySpec, ChannelSpec, Layout, Overwrites, RoleSpec, norm

ARCHIVE_KEY = "_archive"
ARCHIVE_NAME = "ARCHIVE"
TEXTLIKE = {"text", "announcement"}
TOP = "_top"   # "parent" value for channels that sit above every section


# ---- snapshot of the live server (built from discord.py or by the fake backend) ----
@dataclass
class RoleSnap:
    id: int
    name: str
    color: int = 0
    hoist: bool = False
    mentionable: bool = False
    permissions: int = 0
    managed: bool = False      # bot/booster roles: never touched
    locked: bool = False       # above the bot's own role: it can't edit it


@dataclass
class ChannelSnap:
    id: int
    name: str
    type: str                  # category, text, announcement, forum, voice, other
    parent: int | None = None
    position: int = 0
    overwrites: dict[int, tuple[int, int]] = field(default_factory=dict)
    topic: str | None = None
    slowmode: int = 0
    tags: list[str] = field(default_factory=list)
    require_tag: bool = False
    layout: str | None = None
    member_targets: set[int] = field(default_factory=set)   # overwrite ids that are members, not roles


@dataclass
class Snapshot:
    everyone_id: int
    roles: dict[int, RoleSnap]
    channels: dict[int, ChannelSnap]
    community: bool = True     # announcement + forum channels need Community on


@dataclass
class Action:
    op: str        # create_role, edit_role, create_category, edit_category,
                   # create_channel, edit_channel, archive, reorder
    key: str
    text: str      # one line for the preview
    data: dict = field(default_factory=dict)
    details: list[str] = field(default_factory=list)   # extra preview lines (exact permission changes)


@dataclass
class Plan:
    actions: list[Action]
    matches: dict[str, int]          # state key ("role:staff", "cat:start", "ch:chat") -> id
    extras: list[ChannelSnap]        # live channels the layout doesn't mention
    errors: list[str]
    kept: list[str] = field(default_factory=list)       # manual edits kept (sync.keep_manual_edits)
    record: dict = field(default_factory=dict)          # the yaml's names/topics, saved after an apply
    snap: Snapshot | None = None                        # the server as planned against (for the backup)

    @property
    def changes(self) -> list[Action]:
        return [a for a in self.actions if a.op != "reorder" or a.data.get("needed")]


def role_ref(key: str) -> str:
    return f"role:{key}"


def cat_ref(key: str) -> str:
    return f"cat:{key}"


def ch_ref(key: str) -> str:
    return f"ch:{key}"


def _group(ctype: str) -> str:
    """Discord always lists voice below text inside a category."""
    return "voice" if ctype == "voice" else "text"


def _compatible(want: str, have: str) -> bool:
    if want in TEXTLIKE:
        return have in TEXTLIKE
    return want == have


class _Matcher:
    def __init__(self, snap: Snapshot, state: dict[str, int]):
        self.snap, self.state, self.used = snap, state, set()

    def take(self, ref: str, pool, names: list[str], ok=lambda x: True):
        sid = self.state.get(ref)
        if sid in pool and sid not in self.used and ok(pool[sid]):
            self.used.add(sid)
            return pool[sid]
        wanted = [norm(n) for n in names]
        for want in wanted:                          # exact name first, then `was` names in order
            for obj in sorted(pool.values(), key=lambda o: getattr(o, "position", 0)):
                if obj.id not in self.used and norm(obj.name) == want and ok(obj):
                    self.used.add(obj.id)
                    return obj
        return None


# discord.py lists a few flags under their old names; show the ones the yaml and Discord use
_YAML_NAME = {"read_messages": "view_channel", "external_emojis": "use_external_emojis",
              "external_stickers": "use_external_stickers"}


def flag_names(bits: int) -> list[str]:
    return [_YAML_NAME.get(name, name) for name, on in Permissions(bits) if on]


def _bits_text(allow: int, deny: int) -> str:
    parts = [f"{label} {' '.join(flag_names(bits))}" for label, bits in (("allow", allow), ("deny", deny)) if bits]
    return "; ".join(parts) or "no permissions"


def _delta(label: str, old: int, new: int) -> str:
    moves = ["+" + f for f in flag_names(new & ~old)] + ["-" + f for f in flag_names(old & ~new)]
    return f"{label} {' '.join(moves)}" if moves else ""


def _ow_changes(want: Overwrites, have: dict[str, tuple[int, int]], names: dict[str, str]) -> list[str]:
    """One line per target whose overwrite changes: who, and exactly which bits move.
    '+x' = x is added to that list, '-x' = x is taken off it."""
    order = list(names)
    lines = []
    for ref in sorted(set(want) | set(have), key=lambda r: order.index(r) if r in order else len(order)):
        old, new, who = have.get(ref), want.get(ref), names.get(ref, ref)
        if old == new:
            continue
        if old is None:
            lines.append(f"{who}: add ({_bits_text(*new)})")
        elif new is None:
            lines.append(f"{who}: remove ({_bits_text(*old)})")
        else:
            lines.append(f"{who}: " + "; ".join(p for p in (_delta("allow", old[0], new[0]),
                                                            _delta("deny", old[1], new[1])) if p))
    return lines


def _ow_text(want: Overwrites, have: dict[str, tuple[int, int]], names: dict[str, str]) -> str:
    return "perms (" + ", ".join(names[r] for r in names if want.get(r) != have.get(r)) + ")"


def plan(layout: Layout, snap: Snapshot, state: dict[str, int] | None = None,
         archive_extras: bool = False, ignore: set[int] | None = None) -> Plan:
    """`ignore`: channel ids the bot manages itself (party voice channels)."""
    ignore = ignore or set()
    state = state or {}
    m = _Matcher(snap, state)
    actions: list[Action] = []
    errors: list[str] = []
    matches: dict[str, int] = {}

    # ---- roles ----
    role_key_by_id: dict[int, str] = {snap.everyone_id: EVERYONE}
    all_roles = {i: r for i, r in snap.roles.items() if i != snap.everyone_id}
    editable_roles = {i: r for i, r in all_roles.items() if not r.managed}
    to_create: list[RoleSpec] = []
    for spec in layout.roles:
        if spec.external:   # Discord's / another bot's role (Server Booster...): referenced, never made or changed
            have = m.take(role_ref(spec.key), all_roles, [spec.name, *spec.was])
            if have is None:
                errors.append(f"Role @{spec.name} is marked external but is not on the server. The layout never "
                              "makes external roles: fix its name in layout/server.yaml or make the role first.")
            else:
                matches[role_ref(spec.key)] = have.id
                role_key_by_id[have.id] = spec.key
            continue
        have = m.take(role_ref(spec.key), editable_roles, [spec.name, *spec.was])
        if have is None:
            wanted = {norm(n) for n in [spec.name, *spec.was]}
            clash = next((r for r in all_roles.values() if r.managed and norm(r.name) in wanted), None)
            if clash:   # would otherwise make a duplicate of a role Discord or a bot owns
                errors.append(f"Role @{clash.name} belongs to Discord or a bot, so the layout can't make or edit "
                              f"it. Mark it `external: true` in layout/server.yaml.")
            else:
                to_create.append(spec)
            continue
        matches[role_ref(spec.key)] = have.id
        role_key_by_id[have.id] = spec.key
        diff = {}
        for f in ("name", "mentionable", "permissions"):   # colours + hoist: tools/roles_style.py (gradients)
            want = getattr(spec, f)
            if want is not None and want != getattr(have, f):
                diff[f] = want
        if diff and have.locked:
            errors.append(f"Role @{have.name} is above the bot's role. Drag the bot's role above it "
                          f"(Server Settings > Roles) so it can set: {', '.join(diff)}.")
        elif diff:
            actions.append(Action("edit_role", spec.key, f"~ role @{have.name}: {', '.join(diff)}", diff))
    # created bottom-up so they end up in layout order
    for spec in reversed(to_create):
        actions.append(Action("create_role", spec.key, f"+ role @{spec.name}", {"spec": spec}))

    # The layout owns only the overwrites it names: @everyone, its own (non-external) roles, and any
    # target this channel lists (e.g. an external role in perms_extra). Every other overwrite (Server
    # Booster, Admin, other bots, single members) is passed along untouched in "_keep" and written back
    # as it is, because Discord replaces a channel's whole overwrite list on edit.
    owned = {EVERYONE} | {s.key for s in layout.roles if not s.external}
    names = {EVERYONE: "@everyone", **{s.key: "@" + s.name for s in layout.roles}}

    def split_overwrites(ch: ChannelSnap, want: Overwrites):
        have, keep = {}, {}
        for t, bits in ch.overwrites.items():
            key = role_key_by_id.get(t)
            if key is not None and (key in owned or key in want):
                have[key] = bits
            else:
                keep[t] = bits
        return have, keep

    def ow_details(diff: dict, want: Overwrites, have_ow, keep, ch: ChannelSnap) -> list[str]:
        """Preview lines for a perms change; also hands the untouched overwrites to the executor."""
        if "overwrites" not in diff:
            return []
        diff["_keep"] = keep
        lines = _ow_changes(want, have_ow, names)
        if keep:
            lines.append("left as they are: " + ", ".join(
                f"@{snap.roles[t].name}" if t in snap.roles and t not in ch.member_targets else f"member {t}"
                for t in sorted(keep)))
        return lines

    needs_community = []

    # ---- categories and channels ----
    cats = {i: c for i, c in snap.channels.items() if c.type == "category"}
    chans = {i: c for i, c in snap.channels.items() if c.type != "category"}
    desired_cat_ids: list[str] = []
    for cspec in layout.categories:
        if cspec.top:
            continue
        desired_cat_ids.append(cat_ref(cspec.key))
        have = m.take(cat_ref(cspec.key), cats, [cspec.name, *cspec.was])
        if have is None:
            actions.append(Action("create_category", cspec.key, f"+ category {cspec.name}", {"spec": cspec}))
        else:
            matches[cat_ref(cspec.key)] = have.id
            diff, notes = {}, []
            have_ow, keep = split_overwrites(have, cspec.overwrites)
            if have.name != cspec.name:
                diff["name"] = cspec.name
                notes.append(f"rename '{have.name}'")
            if have_ow != cspec.overwrites:
                diff["overwrites"] = cspec.overwrites
                notes.append(_ow_text(cspec.overwrites, have_ow, names))
            if diff:
                details = ow_details(diff, cspec.overwrites, have_ow, keep, have)
                actions.append(Action("edit_category", cspec.key, f"~ category {cspec.name}: " + ", ".join(notes),
                                      diff, details))

    for cspec in layout.categories:
        parent_id = None if cspec.top else matches.get(cat_ref(cspec.key))
        for spec in cspec.channels:
            if spec.type in ("announcement", "forum"):
                needs_community.append(spec.name)
            # prefer a same-named channel that already sits in this category
            same_parent = {i: c for i, c in chans.items()
                           if (c.parent is None if cspec.top else parent_id is not None and c.parent == parent_id)}
            ok = lambda c, t=spec.type: _compatible(t, c.type)
            have = (m.take(ch_ref(spec.key), chans, [], ok)
                    or m.take("", same_parent, [spec.name, *spec.was], ok)
                    or m.take("", chans, [spec.name, *spec.was], ok))
            if have is None:
                actions.append(Action("create_channel", spec.key,
                                      f"+ {spec.type} #{spec.name} in {cspec.name}",
                                      {"spec": spec, "parent": None if cspec.top else cspec.key}))
                continue
            matches[ch_ref(spec.key)] = have.id
            have_ow, keep = split_overwrites(have, spec.overwrites)
            diff, notes = _channel_diff(spec, have, parent_id, cspec, have_ow, names)
            if diff:
                details = ow_details(diff, spec.overwrites, have_ow, keep, have)
                actions.append(Action("edit_channel", spec.key, f"~ #{spec.name}: " + ", ".join(notes),
                                      {**diff, "_type": spec.type}, details))

    if needs_community and not snap.community:
        errors.append("Turn on Community (Server Settings > Enable Community) first: "
                      "announcement and forum channels need it (" + ", ".join(needs_community) + ").")

    # ---- leftovers ----
    managed_ids = set(matches.values())
    archive_id = state.get(cat_ref(ARCHIVE_KEY))
    extras = sorted((c for c in snap.channels.values()
                     if c.id not in managed_ids and c.id not in ignore
                     and not (archive_id and (c.id == archive_id or c.parent == archive_id))),
                    key=lambda c: (c.type != "category", c.position))
    movable = [c for c in extras if c.type != "category"]
    if archive_extras and movable:
        actions.append(Action("archive", ARCHIVE_KEY,
                              f"> move {len(movable)} old channel(s) into hidden {ARCHIVE_NAME}: "
                              + ", ".join("#" + c.name for c in movable),
                              {"ids": [c.id for c in movable]}))

    # Sections the bot made earlier that end up empty (their channels moved) are removed.
    # Only bot-made ones (their id is in the state file); a section people made is never touched.
    bot_made = {v: k for k, v in state.items() if k.startswith("cat:") and k != cat_ref(ARCHIVE_KEY)}
    for c in extras:
        if c.type != "category" or c.id not in bot_made:
            continue
        kids = [x for x in snap.channels.values() if x.type != "category" and x.parent == c.id]
        leaving = all(k.id in managed_ids or (archive_extras and k.id not in ignore) for k in kids)
        if leaving and not any(k.id in ignore for k in kids):
            actions.append(Action("remove_category", bot_made[c.id], f"- remove empty section {c.name}",
                                  {"id": c.id}))

    # ---- order ----
    needed = any(a.op in ("create_category", "create_channel") for a in actions) or \
        _order_differs(layout, snap, matches)
    removals = [a for a in actions if a.op == "remove_category"]
    actions = [a for a in actions if a.op != "remove_category"]
    actions.append(Action("reorder", "", "> put categories and channels in layout order",
                          {"needed": needed or bool(removals)}))
    actions += removals
    return Plan(actions=actions, matches=matches, extras=extras, errors=errors)


def _channel_diff(spec: ChannelSpec, have: ChannelSnap, parent_id, cspec: CategorySpec,
                  have_ow: dict[str, tuple[int, int]], names: dict[str, str]):
    diff, notes = {}, []
    if have.name != spec.name:
        diff["name"] = spec.name
        notes.append(f"rename '{have.name}'")
    if cspec.top:
        if have.parent is not None:
            diff["parent"] = TOP
            notes.append("move to the top")
    elif have.parent != parent_id or parent_id is None:
        diff["parent"] = cspec.key
        notes.append(f"move to {cspec.name}")
    if spec.type in TEXTLIKE and have.type != spec.type:
        diff["type"] = spec.type
        notes.append(f"make {spec.type}")
    if spec.type != "voice":
        if (spec.topic or None) != (have.topic or None):
            diff["topic"] = spec.topic
            notes.append("topic")
    if spec.type in TEXTLIKE and spec.slowmode != have.slowmode:
        diff["slowmode"] = spec.slowmode
        notes.append(f"slowmode {spec.slowmode}s")
    if spec.type == "forum":
        if spec.tags is not None and spec.tags != have.tags:
            diff["tags"] = spec.tags
            notes.append("tags")
        if spec.require_tag is not None and spec.require_tag != have.require_tag:
            diff["require_tag"] = spec.require_tag
            notes.append("require tag" if spec.require_tag else "tag optional")
        if spec.layout is not None and spec.layout != have.layout:
            diff["layout"] = spec.layout
            notes.append(f"{spec.layout} view")
    if have_ow != spec.overwrites:
        diff["overwrites"] = spec.overwrites
        notes.append(_ow_text(spec.overwrites, have_ow, names))
    return diff, notes


def _order_differs(layout: Layout, snap: Snapshot, matches: dict[str, int]) -> bool:
    def ordered(ids):
        return sorted(ids, key=lambda i: (snap.channels[i].position, i))

    want_cats = [matches[cat_ref(c.key)] for c in layout.categories if not c.top]
    have_cats = [i for i in ordered([c.id for c in snap.channels.values() if c.type == "category"])
                 if i in set(want_cats)]
    if want_cats != have_cats:
        return True
    for c in layout.categories:
        pid = None if c.top else matches[cat_ref(c.key)]
        for grp in ("text", "voice"):
            want = [matches[ch_ref(s.key)] for s in c.channels if _group(s.type) == grp]
            have = [i for i in ordered([x.id for x in snap.channels.values()
                                        if x.parent == pid and x.type != "category"
                                        and _group(x.type) == grp]) if i in set(want)]
            if want != have:
                return True
    return False


def desired_order(layout: Layout, ids: dict[str, int]) -> list[tuple[int, int, int | None]]:
    """(id, position, parent_id) for every managed category and channel."""
    out: list[tuple[int, int, int | None]] = []
    ci = 0
    for c in layout.categories:
        if c.top:
            out += [(ids[ch_ref(s.key)], n, None) for n, s in enumerate(c.channels)]
            continue
        cid = ids[cat_ref(c.key)]
        out.append((cid, ci, None))
        ci += 1
        pos = {"text": 0, "voice": 0}
        for s in c.channels:
            g = _group(s.type)
            out.append((ids[ch_ref(s.key)], pos[g], cid))
            pos[g] += 1
    return out
