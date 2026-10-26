"""Mod logs (replaces Circle): message edits/deletes, role and nickname changes, kicks,
and channel/role changes, written to the #logs threads as short tidy cards.

Noise left out on purpose: bots, the ticket inbox, the logs channel itself, and the
lounge voice channels the bot makes and removes.
"""
from __future__ import annotations

import discord
from discord.ext import commands

from .. import db, ui
from ..core import channel, log_staff


def _skip_channel(guild: discord.Guild, ch) -> bool:
    ignore = {getattr(channel(guild, k), "id", None) for k in ("inbox", "logs")}
    parent = getattr(ch, "parent_id", None)
    return getattr(ch, "id", None) in ignore or parent in ignore


def _is_lounge(guild: discord.Guild, ch) -> bool:
    hub = channel(guild, "create_party")
    return bool(hub and isinstance(ch, discord.VoiceChannel) and ch.category_id == hub.category_id and ch.id != hub.id) \
        or bool(db.one("SELECT 1 FROM parties WHERE channel_id=?", ch.id))


def _quote(text: str, limit: int = 900) -> str:
    text = (text or "").strip() or "*(no text)*"
    return "> " + text[:limit].replace("\n", "\n> ")


async def _card(guild, kind: str, title: str, body: str, tone: str = "neutral"):
    await log_staff(guild, "", kind=kind, embed=ui.card(title, body, tone=tone, footer=None))


class ModLog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---- messages ----
    @commands.Cog.listener()
    async def on_raw_message_delete(self, p: discord.RawMessageDeleteEvent):
        g = self.bot.get_guild(p.guild_id) if p.guild_id else None
        if not g:
            return
        ch = g.get_channel_or_thread(p.channel_id)
        m = p.cached_message
        if not ch or _skip_channel(g, ch) or (m and m.author.bot):
            return
        if m:
            files = f"\n-# {len(m.attachments)} attachment(s)" if m.attachments else ""
            await _card(g, "Messages", "Message deleted",
                        f"{m.author.mention} in {ch.mention}\n{_quote(m.content)}{files}", "bad")
        else:
            await _card(g, "Messages", "Message deleted", f"In {ch.mention} (older message, text not available)", "bad")

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, p: discord.RawBulkMessageDeleteEvent):
        g = self.bot.get_guild(p.guild_id) if p.guild_id else None
        ch = g.get_channel_or_thread(p.channel_id) if g else None
        if ch and not _skip_channel(g, ch):
            await _card(g, "Messages", "Messages purged", f"{len(p.message_ids)} messages in {ch.mention}", "bad")

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not after.guild or after.author.bot or before.content == after.content or _skip_channel(after.guild, after.channel):
            return
        await _card(after.guild, "Messages", "Message edited",
                    f"{after.author.mention} in {after.channel.mention} · [jump]({after.jump_url})\n"
                    f"**Before**\n{_quote(before.content, 450)}\n**After**\n{_quote(after.content, 450)}", "wait")

    # ---- members ----
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        added = [r for r in after.roles if r not in before.roles]
        removed = [r for r in before.roles if r not in after.roles]
        lines = []
        if added:
            lines.append("Added " + ", ".join(r.mention for r in added))
        if removed:
            lines.append("Removed " + ", ".join(r.mention for r in removed))
        if before.nick != after.nick:
            lines.append(f"Nickname **{before.nick or before.name}** → **{after.nick or after.name}**")
        if lines:
            await _card(after.guild, "Members", f"{after.display_name}", f"{after.mention}\n" + "\n".join(lines))

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        try:   # a kick shows up in the audit log within a few seconds
            async for e in member.guild.audit_logs(limit=5, action=discord.AuditLogAction.kick):
                if e.target and e.target.id == member.id and (discord.utils.utcnow() - e.created_at).total_seconds() < 15:
                    await _card(member.guild, "Moderation", "Member kicked",
                                f"{member} by {e.user.mention}" + (f"\n{_quote(e.reason, 300)}" if e.reason else ""),
                                "bad")
                    return
        except discord.HTTPException:
            pass

    # ---- server structure ----
    @commands.Cog.listener()
    async def on_guild_channel_create(self, ch):
        if not _is_lounge(ch.guild, ch):
            await _card(ch.guild, "Moderation", "Channel created", f"{ch.mention} ({ch.type.name})")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, ch):
        if not _is_lounge(ch.guild, ch) and "lounge" not in ch.name.lower():
            await _card(ch.guild, "Moderation", "Channel deleted", f"#{ch.name} ({ch.type.name})", "bad")

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        await _card(role.guild, "Moderation", "Role created", role.mention)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        await _card(role.guild, "Moderation", "Role deleted", f"@{role.name}", "bad")

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        changes = []
        if before.name != after.name:
            changes.append(f"Name **{before.name}** → **{after.name}**")
        if before.permissions != after.permissions:
            changes.append("Permissions changed")
        if before.colour != after.colour:
            changes.append(f"Colour {before.colour} → {after.colour}")
        if changes:
            await _card(after.guild, "Moderation", "Role updated", f"{after.mention}\n" + "\n".join(changes))


async def setup(bot):
    await bot.add_cog(ModLog(bot))
