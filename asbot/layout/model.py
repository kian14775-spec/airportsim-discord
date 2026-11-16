"""Reads layout/server.yaml into plain specs the planner can compare."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from discord import Permissions

CHANNEL_TYPES = {"text", "announcement", "forum", "voice"}
FORUM_LAYOUTS = {"gallery", "list"}
EVERYONE = "@everyone"

# {ref: (allow_bits, deny_bits)}; ref = "@everyone" or a role key
Overwrites = dict[str, tuple[int, int]]


class LayoutError(ValueError):
    pass


@dataclass
class RoleSpec:
    key: str
    name: str
    color: int | None = None          # None = leave as is
    hoist: bool | None = None
    mentionable: bool | None = None
    permissions: int | None = None    # None = leave as is
    was: list[str] = field(default_factory=list)
    external: bool = False            # Discord's / another bot's role: found by name, never made or changed


@dataclass
class ChannelSpec:
    key: str
    name: str
    type: str
    overwrites: Overwrites
    topic: str | None = None
    slowmode: int = 0
    tags: list[str] | None = None
    require_tag: bool | None = None
    layout: str | None = None
    was: list[str] = field(default_factory=list)


@dataclass
class CategorySpec:
    key: str
    name: str
    overwrites: Overwrites
    channels: list[ChannelSpec]
    was: list[str] = field(default_factory=list)
    top: bool = False      # channels above every section, no header


@dataclass
class Layout:
    roles: list[RoleSpec]
    categories: list[CategorySpec]
    presets: dict[str, Overwrites]
    groups: dict[str, list[str]] = field(default_factory=dict)


SMALL_CAPS = str.maketrans("ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡʏᴢ", "abcdefghijklmnopqrstuvwyz")


def norm(name: str) -> str:
    """'│ Information' / '︱ɪɴꜰᴏʀᴍᴀᴛɪᴏɴ' -> 'information'. Used to match channels by name."""
    return re.sub(r"[^a-z0-9-]", "", name.translate(SMALL_CAPS).lower())


def perm_bits(flags: list[str] | None, where: str) -> int:
    flags = flags or []
    bad = [f for f in flags if f not in Permissions.VALID_FLAGS]
    if bad:
        raise LayoutError(f"{where}: unknown permission(s) {bad}")
    return Permissions(**{f: True for f in flags}).value


def _was(raw: dict) -> list[str]:
    was = raw.get("was") or []
    return [was] if isinstance(was, str) else list(was)


def _parse_roles(raw_roles: list[dict]) -> list[RoleSpec]:
    roles, seen = [], set()
    for r in raw_roles or []:
        key = r.get("key") or norm(r["name"])
        if key in seen:
            raise LayoutError(f"duplicate role key '{key}'")
        seen.add(key)
        external = bool(r.get("external"))
        if external and any(k in r for k in ("color", "hoist", "mentionable", "permissions")):
            raise LayoutError(f"role {key}: external roles are only referenced, "
                              "so color/hoist/mentionable/permissions can't be set")
        color = r.get("color")
        roles.append(RoleSpec(
            key=key,
            name=r["name"],
            color=int(color.lstrip("#"), 16) if isinstance(color, str) else color,
            hoist=r.get("hoist"),
            mentionable=r.get("mentionable"),
            permissions=perm_bits(r["permissions"], f"role {key}") if "permissions" in r else None,
            was=_was(r),
            external=external,
        ))
    return roles


def _parse_groups(raw: dict, roles: list[RoleSpec]) -> dict[str, list[str]]:
    keys = {r.key for r in roles}
    for g, members in (raw or {}).items():
        if g in keys:
            raise LayoutError(f"group '{g}' has the same key as a role")
        bad = [k for k in members if k not in keys]
        if bad:
            raise LayoutError(f"group {g}: unknown role key(s) {bad}")
    return {g: list(m) for g, m in (raw or {}).items()}


def _rules(targets: dict, roles: list[RoleSpec], groups: dict[str, list[str]], where: str) -> Overwrites:
    """{target: {allow: [...], deny: [...]}} -> {ref: (allow_bits, deny_bits)}. A target is
    @everyone, a group, a role key or a role name."""
    by_name = {norm(r.name): r.key for r in roles}
    keys = {r.key for r in roles}
    ow: Overwrites = {}
    for target, rule in (targets or {}).items():
        if target == EVERYONE:
            refs = [EVERYONE]
        elif target in groups:
            refs = groups[target]
        elif target in keys:
            refs = [target]
        elif norm(target) in by_name:
            refs = [by_name[norm(target)]]
        else:
            raise LayoutError(f"{where}: role '{target}' is not in roles or groups")
        here = f"{where} / {target}"
        rule = rule or {}
        allow, deny = perm_bits(rule.get("allow"), here), perm_bits(rule.get("deny"), here)
        if allow & deny:
            raise LayoutError(f"{here}: a permission can't be in both allow and deny")
        for ref in refs:
            ow[ref] = (allow, deny)
    return ow


def _parse_presets(raw: dict, roles: list[RoleSpec], groups: dict[str, list[str]]) -> dict[str, Overwrites]:
    return {pname: _rules(targets, roles, groups, f"preset {pname}") for pname, targets in (raw or {}).items()}


def merge_extra(base: Overwrites, extra: Overwrites) -> Overwrites:
    """`perms_extra` on top of a preset: per target, the extra allow/deny bits win; the preset's
    other bits stay. A target the preset doesn't name gets exactly the extra bits."""
    out = dict(base)
    for ref, (allow, deny) in extra.items():
        a0, d0 = out.get(ref, (0, 0))
        out[ref] = ((a0 & ~deny) | allow, (d0 & ~allow) | deny)
    return out


def _preset(presets: dict[str, Overwrites], name: str | None, where: str) -> Overwrites:
    if name is None:
        return {}
    if name not in presets:
        raise LayoutError(f"{where}: unknown perms preset '{name}'")
    return dict(presets[name])


def parse(data: dict) -> Layout:
    roles = _parse_roles(data.get("roles"))
    groups = _parse_groups(data.get("groups"), roles)
    presets = _parse_presets(data.get("presets"), roles, groups)
    cats, keys = [], {"category": set(), "channel": set()}

    def claim(key: str, kind: str = "channel"):
        if key in keys[kind]:
            raise LayoutError(f"duplicate {kind} key '{key}'")
        keys[kind].add(key)

    for c in data.get("categories") or []:
        top = bool(c.get("top"))
        ckey = c.get("key") or ("_top" if top else norm(c["name"]))
        claim(ckey, "category")
        cat_ow = _preset(presets, c.get("perms"), f"category {ckey}")
        chans = []
        for ch in c.get("channels") or []:
            key = ch.get("key") or norm(ch["name"])
            claim(key)
            ctype = ch.get("type", "text")
            if ctype not in CHANNEL_TYPES:
                raise LayoutError(f"channel {key}: type must be one of {sorted(CHANNEL_TYPES)}")
            layout = ch.get("layout")
            if layout is not None and layout not in FORUM_LAYOUTS:
                raise LayoutError(f"channel {key}: layout must be gallery or list")
            if ctype != "forum" and any(k in ch for k in ("tags", "require_tag", "layout")):
                raise LayoutError(f"channel {key}: tags/require_tag/layout are forum-only")
            ow = _preset(presets, ch["perms"], f"channel {key}") if "perms" in ch else dict(cat_ow)
            if ch.get("perms_extra"):
                ow = merge_extra(ow, _rules(ch["perms_extra"], roles, groups, f"channel {key} perms_extra"))
            chans.append(ChannelSpec(
                key=key,
                name=ch["name"],
                type=ctype,
                overwrites=ow,
                topic=ch.get("topic"),
                slowmode=int(ch.get("slowmode", 0)),
                tags=list(ch["tags"]) if "tags" in ch else None,
                require_tag=ch.get("require_tag"),
                layout=layout,
                was=_was(ch),
            ))
        cats.append(CategorySpec(key=ckey, name=c.get("name") or "(top)", overwrites=cat_ow, channels=chans,
                                 was=_was(c), top=top))
    return Layout(roles=roles, categories=cats, presets=presets, groups=groups)


def load(path: str | Path) -> Layout:
    with open(path, encoding="utf-8") as f:
        return parse(yaml.safe_load(f) or {})
