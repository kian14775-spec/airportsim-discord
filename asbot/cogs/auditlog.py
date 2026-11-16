"""Audit log -> log channels: staff actions, with who did it and why. Discord sends each
audit log entry the moment it's made, so nothing is polled.

#alerts   bots added, webhooks, integrations, AutoMod rules, command permissions, and any
          change that hands out dangerous permissions or weakens server safety
#actions  everything else staff do: kicks, bans, timeouts, prunes, nicknames, roles, voice
          mutes and disconnects, channels, permissions, roles, server settings

Quiet on purpose (no post): what's listed in `logs: muted:` in config/bot.yaml, role and
nickname changes by other bots (Bloxlink on every verify) and nicknames members set
themselves. Anything about a member still goes on their timeline. A dangerous change always
posts. The bot's own actions are logged where they happen (with context), so they're skipped
here, except layout changes to channels, roles and the server. Message deletes are logged
by modlog.py (it has the text).
"""
from __future__ import annotations

import datetime as dt
import time
from enum import Enum

import discord
from discord.ext import commands

from .. import timeline
from ..core import channel, log_card, log_off, log_staff

A = discord.AuditLogAction
DANGER = ("administrator", "manage_guild", "manage_roles", "manage_channels", "manage_webhooks",
          "ban_members", "kick_members", "mention_everyone")
SAFETY = {"verification_level", "mfa_level", "explicit_content_filter", "vanity_url_code", "owner"}

TITLES = {
    A.guild_update: "Server settings changed", A.channel_create: "Channel created", A.channel_update: "Channel changed",
    A.channel_delete: "Channel deleted", A.overwrite_create: "Channel permissions added",
    A.overwrite_update: "Channel permissions changed", A.overwrite_delete: "Channel permissions removed",
    A.kick: "Kicked", A.member_prune: "Members pruned", A.ban: "Banned", A.unban: "Unbanned",
    A.member_update: "Member changed", A.member_role_update: "Roles changed", A.member_move: "Moved in voice",
    A.member_disconnect: "Disconnected from voice", A.bot_add: "Bot added", A.role_create: "Role created",
    A.role_update: "Role changed", A.role_delete: "Role deleted", A.invite_create: "Invite created",
    A.invite_update: "Invite changed", A.invite_delete: "Invite deleted", A.webhook_create: "Webhook created",
    A.webhook_update: "Webhook changed", A.webhook_delete: "Webhook deleted", A.emoji_create: "Emoji added",
    A.emoji_update: "Emoji changed", A.emoji_delete: "Emoji removed", A.message_pin: "Message pinned",
    A.message_unpin: "Message unpinned", A.integration_create: "Integration added",
    A.integration_update: "Integration changed", A.integration_delete: "Integration removed",
    A.sticker_create: "Sticker added", A.sticker_update: "Sticker changed", A.sticker_delete: "Sticker removed",
    A.scheduled_event_create: "Event created", A.scheduled_event_update: "Event changed",
    A.scheduled_event_delete: "Event deleted", A.thread_update: "Thread changed", A.thread_delete: "Thread deleted",
    A.automod_rule_create: "AutoMod rule created", A.automod_rule_update: "AutoMod rule changed",
    A.automod_rule_delete: "AutoMod rule deleted", A.app_command_permission_update: "Command permissions changed",
}
MOD = {A.kick, A.ban, A.unban, A.member_prune}
VOICE = {A.member_move, A.member_disconnect}
MESSAGES = {A.message_pin, A.message_unpin}
SECURITY = {A.bot_add, A.webhook_create, A.webhook_update, A.webhook_delete, A.integration_create,
            A.integration_update, A.integration_delete, A.automod_rule_create, A.automod_rule_update,
            A.automod_rule_delete, A.app_command_permission_update}
SKIP = {A.message_delete, A.message_bulk_delete, A.thread_create, A.automod_block_message, A.automod_flag_message,
        A.automod_timeout_member, A.automod_quarantine_user, A.creator_monetization_request_created,
        A.creator_monetization_terms_accepted}
OWN_KEEP = {A.channel_create, A.channel_update, A.channel_delete, A.overwrite_create, A.overwrite_update,
            A.overwrite_delete, A.role_create, A.role_update, A.role_delete, A.guild_update}
BAD = {A.kick, A.ban, A.member_prune, A.channel_delete, A.role_delete, A.webhook_create, A.bot_add,
       A.member_disconnect, A.thread_delete, A.emoji_delete, A.automod_rule_delete, A.integration_create}
GOOD = {A.unban}
HIDE = {"id", "position", "type", "flags", "permissions", "overwrites", "roles", "allow", "deny", "avatar", "icon",
        "splash", "banner", "discovery_splash", "image", "available_tags", "trigger", "actions", "format_type"}


def mention(x) -> str:
    if x is None:
        return "-"
    if isinstance(x, discord.Role):
        return "@everyone" if x.is_default() else x.mention
    if isinstance(x, (discord.abc.GuildChannel, discord.Thread, discord.Member, discord.User, discord.ClientUser)):
        return x.mention
    if isinstance(x, discord.Object):
        return {discord.Role: f"<@&{x.id}>", discord.Member: f"<@{x.id}>", discord.User: f"<@{x.id}>"}.get(
            x.type, f"`{x.id}`")
    if isinstance(x, discord.Invite):
        return f"`{x.code}`"
    if isinstance(x, discord.Guild):
        return "the server"
    if isinstance(x, (discord.Emoji, discord.PartialEmoji)):
        return str(x)
    return f"**{getattr(x, 'name', None) or x}**"


PERM_NAMES = {"read_messages": "view channel", "manage_guild": "manage server", "manage_emojis": "manage expressions",
              "external_emojis": "use external emojis", "use_application_commands": "use commands"}


def nice(perm: str) -> str:
    text = PERM_NAMES.get(perm) or perm.replace("_", " ")
    return f"**{text}**" if perm in DANGER else text


def value(v) -> str:
    if v is None:
        return "none"
    if isinstance(v, bool):
        return "on" if v else "off"
    if isinstance(v, dt.datetime):
        return f"<t:{int(v.timestamp())}:f>"
    if isinstance(v, dt.timedelta):
        return f"{int(v.total_seconds() // 3600)}h" if v.total_seconds() >= 3600 else f"{int(v.total_seconds())}s"
    if isinstance(v, Enum):
        return v.name.replace("_", " ")
    if isinstance(v, discord.Colour):
        return str(v)
    if isinstance(v, discord.Asset):
        return "image"
    if isinstance(v, (list, tuple, set)):
        return (", ".join(value(x) for x in v) or "none")[:300]
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return f"`{v[:200]}`" if v else "none"
    return mention(v)


def perm_diff(before: discord.Permissions | None, after: discord.Permissions | None) -> str:
    b, a = before or discord.Permissions.none(), after or discord.Permissions.none()
    added = [n for n, on in a if on and not getattr(b, n)]
    removed = [n for n, on in b if on and not getattr(a, n)]
    parts = (["+ " + ", ".join(map(nice, added))] if added else []) + (["− " + ", ".join(map(nice, removed))] if removed else [])
    return " · ".join(parts)


def overwrite_diff(ba: discord.Permissions | None, bd: discord.Permissions | None,
                   aa: discord.Permissions | None, ad: discord.Permissions | None) -> str:
    """One overwrite before -> after, as allowed / denied / reset lists."""
    none = discord.Permissions.none()
    ba, bd, aa, ad = ba or none, bd or none, aa or none, ad or none
    out = {"allowed": [], "denied": [], "reset": []}
    for name, _ in discord.Permissions.all():
        old = "allow" if getattr(ba, name) else "deny" if getattr(bd, name) else None
        new = "allow" if getattr(aa, name) else "deny" if getattr(ad, name) else None
        if old != new:
            out["allowed" if new == "allow" else "denied" if new == "deny" else "reset"].append(nice(name))
    return " · ".join(f"{k} {', '.join(v)}" for k, v in out.items() if v)


def overwrites_list_diff(before, after) -> list[str]:
    """channel_create/update carry the whole overwrites list; show what changed per role/member."""
    b = {t.id: (t, ow) for t, ow in (before or [])}
    a = {t.id: (t, ow) for t, ow in (after or [])}
    lines = []
    for tid in list(dict.fromkeys([*b, *a])):
        t = (a.get(tid) or b.get(tid))[0]
        (ba, bd), (aa, ad) = (b[tid][1].pair() if tid in b else (None, None)), (a[tid][1].pair() if tid in a else (None, None))
        d = overwrite_diff(ba, bd, aa, ad)
        if d:
            lines.append(f"For {mention(t)}: {d}")
    return lines


def dangerous(entry: discord.AuditLogEntry) -> bool:
    """Does this hand out dangerous permissions or weaken the server's safety settings?"""
    before, after, none = entry.changes.before, entry.changes.after, discord.Permissions.none()
    key = {A.role_update: "permissions", A.role_create: "permissions",
           A.overwrite_create: "allow", A.overwrite_update: "allow"}.get(entry.action)
    if key:
        new, old = getattr(after, key, None), getattr(before, key, None) or none
        return bool(new) and any(getattr(new, p) and not getattr(old, p) for p in DANGER)
    if entry.action == A.member_role_update:
        return any(isinstance(r, discord.Role) and any(getattr(r.permissions, p) for p in DANGER)
                   for r in getattr(after, "roles", None) or [])
    if entry.action == A.guild_update:
        return bool(SAFETY & {k for k, _ in after})
    return False


def describe(entry: discord.AuditLogEntry) -> tuple[str, str, str, discord.abc.User | None]:
    """(log kind, title, body, member the entry is about)."""
    act, t, before, after, extra = entry.action, entry.target, entry.changes.before, entry.changes.after, entry.extra
    title, lines, kind = TITLES.get(act, act.name.replace("_", " ").capitalize()), [], "Server"
    user = t if isinstance(t, (discord.Member, discord.User)) else None
    name = getattr(before, "name", None) or getattr(after, "name", None)

    if act in MOD:
        kind = "Moderation"
        if act == A.member_prune:
            lines.append(f"**{getattr(extra, 'members_removed', '?')}** members inactive for "
                         f"{getattr(extra, 'delete_member_days', '?')}+ days")
        else:
            lines.append(mention(t))
    elif act == A.member_update:
        kind = "Members"
        lines.append(mention(t))
        if hasattr(after, "timed_out_until") or hasattr(before, "timed_out_until"):
            kind, until = "Moderation", getattr(after, "timed_out_until", None)
            title = "Timed out" if until else "Timeout removed"
            if until:
                lines.append(f"Until <t:{int(until.timestamp())}:f> (<t:{int(until.timestamp())}:R>)")
        if hasattr(after, "mute") or hasattr(after, "deaf"):
            kind = "Voice" if kind == "Members" else kind
            for k, label in (("mute", "Server mute"), ("deaf", "Server deafen")):
                if hasattr(after, k):
                    lines.append(f"{label} {value(getattr(after, k))}")
        if hasattr(after, "nick") or hasattr(before, "nick"):
            title = "Nickname changed" if title == "Member changed" else title
            lines.append(f"**{getattr(before, 'nick', None) or '-'}** → **{getattr(after, 'nick', None) or '-'}**")
    elif act == A.member_role_update:
        kind = "Members"
        lines.append(mention(t))
        if getattr(after, "roles", None):
            lines.append("Added " + " ".join(mention(r) for r in after.roles))
        if getattr(before, "roles", None):
            lines.append("Removed " + " ".join(mention(r) for r in before.roles))
    elif act in VOICE:
        kind = "Voice"
        ch = getattr(extra, "channel", None)
        lines.append(f"{getattr(extra, 'count', 1)} member(s)" + (f" → **{ch.name}**" if getattr(ch, "name", None) else ""))
    elif act in MESSAGES:
        kind = "Messages"
        ch, mid = getattr(extra, "channel", None), getattr(extra, "message_id", None)
        lines.append(f"Message by {mention(t)} in {mention(ch)}"
                     + (f" · [jump](https://discord.com/channels/{entry.guild.id}/{ch.id}/{mid})" if ch and mid else ""))
    elif act in (A.overwrite_create, A.overwrite_update, A.overwrite_delete):
        lines.append(f"{mention(t)} · for {mention(extra)}")
        d = overwrite_diff(getattr(before, "allow", None), getattr(before, "deny", None),
                           getattr(after, "allow", None), getattr(after, "deny", None))
        if d:
            lines.append(d)
    else:
        if act in SECURITY:
            kind = "Security"
        deleted = entry.category == discord.AuditLogActionCategory.delete
        if act != A.guild_update:
            lines.append(f"**{name}**" if deleted and name else mention(t) if t is not None else "")
        if entry.category == discord.AuditLogActionCategory.update or act == A.guild_update:
            if hasattr(after, "permissions") or hasattr(before, "permissions"):
                d = perm_diff(getattr(before, "permissions", None), getattr(after, "permissions", None))
                if d:
                    lines.append(f"Permissions {d}")
            if hasattr(after, "overwrites") or hasattr(before, "overwrites"):
                lines += overwrites_list_diff(getattr(before, "overwrites", None), getattr(after, "overwrites", None))
            for k in dict.fromkeys([k for k, _ in before] + [k for k, _ in after]):
                if k in HIDE:
                    if k in ("avatar", "icon", "banner", "splash"):
                        lines.append(f"New {k}")
                    continue
                lines.append(f"{k.replace('_', ' ').capitalize()} {value(getattr(before, k, None))} → "
                             f"{value(getattr(after, k, None))}")
        elif act == A.invite_create:
            lines.append(f"`{getattr(after, 'code', '?')}` → {mention(getattr(after, 'channel', None))} · "
                         f"max uses {getattr(after, 'max_uses', 0) or '∞'} · "
                         f"expires {'never' if not getattr(after, 'max_age', 0) else value(dt.timedelta(seconds=after.max_age))}")
        elif act == A.role_create and getattr(after, "permissions", None):
            d = perm_diff(None, after.permissions)
            if d:
                lines.append(f"Permissions {d}")
    if dangerous(entry):
        kind = "Security"
        title = f"⚠️ {title}"
    return kind, title, "\n".join(x for x in lines if x), user


def quiet(entry: discord.AuditLogEntry, kind: str, title: str) -> bool:
    """Not worth a post: switched off, a bot changing roles or nicknames, or someone's own nickname."""
    if title.startswith("⚠️"):
        return False
    if log_off(entry.action.name):
        return True
    if kind != "Members":
        return False
    return getattr(entry.user, "bot", False) or entry.user_id == getattr(entry.target, "id", None)


def timeline_text(entry: discord.AuditLogEntry, title: str, body: str) -> str:
    """One timeline line: what changed (the body's first line is just the member), who, why."""
    detail = " · ".join(body.split("\n")[1:])
    own = entry.user_id == getattr(entry.target, "id", None)
    return title.removeprefix("⚠️ ") + (f": {detail}" if detail else "") + ("" if own else f" by <@{entry.user_id}>") \
        + (f" · {entry.reason}" if entry.reason else "")


class AuditLog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.recent: dict[tuple[A, int], float] = {}   # (action, target id) -> when it came in

    def saw(self, action: A, target_id: int, within: float = 15) -> bool:
        return time.time() - self.recent.get((action, target_id), 0) < within

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        now = time.time()
        self.recent = {k: v for k, v in self.recent.items() if now - v < 60}
        self.recent[(entry.action, getattr(entry.target, "id", 0))] = now
        if entry.action in SKIP:
            return
        own = entry.user_id == self.bot.user.id
        name = getattr(entry.changes.before, "name", None) or getattr(entry.changes.after, "name", None) \
            or getattr(entry.target, "name", "")
        counter = channel(entry.guild, "live_count")   # "Playing now: 1,234" is renamed every 10 min
        if own and (entry.action not in OWN_KEEP or "lounge" in str(name).lower()
                    or (counter and getattr(entry.target, "id", None) == counter.id)):
            return
        kind, title, body, user = describe(entry)
        target_id = getattr(entry.target, "id", None)
        if target_id and (user or entry.action in MOD) and entry.action not in MESSAGES and entry.action != A.member_prune:
            timeline.add(target_id, "staff", timeline_text(entry, title, body))
        if quiet(entry, kind, title):
            return
        if user is None and entry.action in MOD and isinstance(entry.target, discord.Object):
            try:
                user = await self.bot.fetch_user(entry.target.id)
            except discord.HTTPException:
                pass
        tone = "bad" if entry.action in BAD or title.startswith("⚠️") else "good" if entry.action in GOOD \
            else "wait" if title == "Timed out" else "neutral"
        await log_staff(entry.guild, "", kind=kind, embed=log_card(
            title, body, tone=tone, user=user, by=entry.user or discord.Object(entry.user_id), reason=entry.reason))


async def setup(bot):
    await bot.add_cog(AuditLog(bot))
