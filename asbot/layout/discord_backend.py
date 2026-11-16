"""Snapshot + backend for a real Discord guild (discord.py)."""
from __future__ import annotations

import discord

from .planner import ChannelSnap, RoleSnap, Snapshot

_LAYOUTS = {discord.ForumLayoutType.gallery_view: "gallery", discord.ForumLayoutType.list_view: "list"}
_LAYOUT_ENUM = {"gallery": discord.ForumLayoutType.gallery_view, "list": discord.ForumLayoutType.list_view}


def _tag(spec: str) -> discord.ForumTag:
    """'Planned*' = a tag only staff can apply."""
    return discord.ForumTag(name=spec.rstrip("*"), moderated=spec.endswith("*"))


def _ctype(ch) -> str:
    if isinstance(ch, discord.CategoryChannel):
        return "category"
    if isinstance(ch, discord.ForumChannel):
        return "forum"
    if isinstance(ch, discord.VoiceChannel):
        return "voice"
    if isinstance(ch, discord.TextChannel):
        return "announcement" if ch.is_news() else "text"
    return "other"


def _is_role(target) -> bool:
    """Overwrite targets are Role / Member, or an Object typed Role / User when not cached."""
    return isinstance(target, discord.Role) or (isinstance(target, discord.Object) and target.type is discord.Role)


def snapshot(guild: discord.Guild) -> Snapshot:
    roles = {r.id: RoleSnap(id=r.id, name=r.name, color=r.color.value, hoist=r.hoist,
                            mentionable=r.mentionable, permissions=r.permissions.value,
                            managed=r.managed, locked=r >= guild.me.top_role)
             for r in guild.roles}
    chans = {}
    for ch in guild.channels:
        ow, members = {}, set()
        for target, o in ch.overwrites.items():
            allow, deny = o.pair()
            ow[target.id] = (allow.value, deny.value)
            if not _is_role(target):
                members.add(target.id)
        forum = isinstance(ch, discord.ForumChannel)
        chans[ch.id] = ChannelSnap(
            id=ch.id, name=ch.name, type=_ctype(ch), parent=ch.category_id, position=ch.position,
            overwrites=ow, topic=getattr(ch, "topic", None),
            slowmode=getattr(ch, "slowmode_delay", 0) or 0,
            tags=[t.name + ("*" if t.moderated else "") for t in ch.available_tags] if forum else [],
            require_tag=ch.flags.require_tag if forum else False,
            layout=_LAYOUTS.get(ch.default_layout) if forum else None,
            member_targets=members,
        )
    return Snapshot(everyone_id=guild.default_role.id, roles=roles, channels=chans,
                    community="COMMUNITY" in guild.features)


class DiscordBackend:
    def __init__(self, guild: discord.Guild, reason: str = "layout sync"):
        self.guild, self.reason = guild, reason
        self.everyone_id = guild.default_role.id
        self.made: dict[int, object] = {}   # roles/sections made this run (discord.py may not cache them yet)

    def _get(self, cid: int | None):
        return (self.guild.get_channel(cid) or self.made.get(cid)) if cid else None

    def _target(self, tid: int, existing: dict[int, object]):
        """An overwrite that is already on the channel reuses its own target, so a member overwrite
        stays a member overwrite and a role one a role one (the layout leaves those it doesn't own as is)."""
        return (existing.get(tid) or self.guild.get_role(tid) or self.made.get(tid)
                or self.guild.get_member(tid) or discord.Object(tid))

    def _overwrites(self, ow: dict[int, tuple[int, int]], ch=None):
        existing = {t.id: t for t in ch.overwrites} if ch is not None else {}
        return {self._target(t, existing): discord.PermissionOverwrite.from_pair(discord.Permissions(a),
                                                                                 discord.Permissions(d))
                for t, (a, d) in ow.items()}

    async def create_role(self, name, color, hoist, mentionable, permissions) -> int:
        kw = {"name": name, "reason": self.reason}
        if color is not None:
            kw["colour"] = discord.Colour(color)
        if hoist is not None:
            kw["hoist"] = hoist
        if mentionable is not None:
            kw["mentionable"] = mentionable
        if permissions is not None:
            kw["permissions"] = discord.Permissions(permissions)
        role = await self.guild.create_role(**kw)
        self.made[role.id] = role
        return role.id

    async def edit_role(self, role_id, fields) -> None:
        kw = dict(fields)
        if "color" in kw:
            kw["colour"] = discord.Colour(kw.pop("color"))
        if "permissions" in kw:
            kw["permissions"] = discord.Permissions(kw["permissions"])
        await self.guild.get_role(role_id).edit(reason=self.reason, **kw)

    async def create_category(self, name, overwrites) -> int:
        cat = await self.guild.create_category(name, overwrites=self._overwrites(overwrites), reason=self.reason)
        self.made[cat.id] = cat
        return cat.id

    async def create_channel(self, spec, parent_id, overwrites) -> int:
        cat = self._get(parent_id)
        ow = self._overwrites(overwrites)
        if spec.type == "voice":
            ch = await self.guild.create_voice_channel(spec.name, category=cat, overwrites=ow, reason=self.reason)
        elif spec.type == "forum":
            kw = {}
            if spec.layout:
                kw["default_layout"] = _LAYOUT_ENUM[spec.layout]
            ch = await self.guild.create_forum(
                spec.name, category=cat, overwrites=ow, topic=spec.topic or "",
                available_tags=[_tag(t) for t in spec.tags or []],
                reason=self.reason, **kw)
            if spec.require_tag:
                await ch.edit(require_tag=True, reason=self.reason)
        else:
            ch = await self.guild.create_text_channel(
                spec.name, category=cat, overwrites=ow, topic=spec.topic or "",
                slowmode_delay=spec.slowmode, news=spec.type == "announcement", reason=self.reason)
        self.made[ch.id] = ch
        return ch.id

    async def edit_channel(self, channel_id, ctype, fields) -> None:
        ch = self._get(channel_id)
        kw = {}
        for k, v in fields.items():
            if k == "name":
                kw["name"] = v
            elif k == "overwrites":
                kw["overwrites"] = self._overwrites(v, ch)
            elif k == "parent":
                kw["category"] = self._get(v)
            elif k == "topic":
                kw["topic"] = v or ""
            elif k == "slowmode":
                kw["slowmode_delay"] = v
            elif k == "type":
                kw["type"] = discord.ChannelType.news if v == "announcement" else discord.ChannelType.text
            elif k == "require_tag":
                kw["require_tag"] = v
            elif k == "layout":
                kw["default_layout"] = _LAYOUT_ENUM[v]
            elif k == "tags":
                keep = {t.name: t for t in ch.available_tags}   # keep ids so posts keep their tags
                tags = []
                for n in v:
                    t = keep.get(n.rstrip("*"))
                    if t is None:
                        t = _tag(n)
                    else:
                        t.moderated = n.endswith("*")
                    tags.append(t)
                kw["available_tags"] = tags
        await ch.edit(reason=self.reason, **kw)

    async def delete_category(self, category_id) -> None:
        live = await self.guild.fetch_channels()   # fresh from Discord, not the cache
        if any(getattr(c, "category_id", None) == category_id for c in live):
            raise RuntimeError(f"section {category_id} still has channels, not removing it")
        cat = next((c for c in live if c.id == category_id), None)
        if isinstance(cat, discord.CategoryChannel):
            await cat.delete(reason=self.reason)

    async def reorder(self, order) -> None:
        # Discord only allows one parent change per bulk call, so move strays one by one first
        for i, _, parent in order:
            ch = self._get(i)
            if ch is not None and not isinstance(ch, discord.CategoryChannel) and ch.category_id != parent:
                await ch.edit(category=self._get(parent), reason=self.reason)
        payload = [{"id": i, "position": p} for i, p, _ in order]
        await self.guild._state.http.bulk_channel_update(self.guild.id, payload, reason=self.reason)
