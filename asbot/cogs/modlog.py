"""Logs (replaces Circle and Dyno): what members did goes to #activity. Staff actions (bans,
roles, channels...) and who did them come from the audit log in auditlog.py.

#activity  deleted messages (text from the cache or a 7-day store, images kept for a few
           hours so they can be posted again), edits, purges (as a .txt), ghost pings,
           one-line joins and leaves, boosts
timeline   names, avatars, own nicknames and voice only go on the member's timeline
           (asbot/timeline.py), unless taken out of `logs: muted:` in config/bot.yaml

Left out on purpose: bots, the ticket inbox (the bot mirrors DMs there), the log channels
themselves and small edits (typos), as long as they don't add a link or a ping.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import difflib
import io
import re
import time

import discord
from discord.ext import commands, tasks

from .. import db, timeline
from ..core import cfg, channel, is_staff, log, log_card, log_staff
from ..paths import DATA

db.conn().executescript("""
CREATE TABLE IF NOT EXISTS msglog (id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL, author_id INTEGER NOT NULL,
    content TEXT NOT NULL, files TEXT NOT NULL DEFAULT '', mentions TEXT NOT NULL DEFAULT '', created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS msglog_created ON msglog(created);
""")

CACHE = DATA / "logcache"
LOG_KEYS = ("log_security", "log_mod", "log_messages")
MAX_FILE = 8 * 1024 * 1024        # bots can upload 10 MB per message on an unboosted server
GHOST_SECONDS = 120
SMALL_EDIT = 0.85                 # this similar (0-1) = a typo fix, not worth a post
LINK_OR_PING = re.compile(r"https?://\S+|<@[!&]?\d+>|@everyone|@here")


def settings() -> dict:
    return {"keep_messages_days": 7, "keep_images_hours": 12, "keep_images_mb": 150, **(cfg().get("logs") or {})}


def skip_channel(guild: discord.Guild, ch) -> bool:
    ignore = {getattr(channel(guild, k), "id", None) for k in ("inbox", *LOG_KEYS)} - {None}
    return getattr(ch, "id", None) in ignore or getattr(ch, "parent_id", None) in ignore


def quote(text: str | None, limit: int = 900) -> str:
    text = (text or "").strip()
    if not text:
        return "-# (no text)"
    if len(text) > limit:
        text = text[:limit] + "…"
    return "> " + text.replace("\n", "\n> ")


def when(ts: float) -> str:
    return f"<t:{int(ts)}:R>"


def mention_ids(m: discord.Message) -> str:
    """Who a message pinged, as 'u<id> r<id> e' (users, roles, everyone)."""
    users = [f"u{u.id}" for u in m.mentions if not u.bot and u.id != m.author.id]
    return " ".join(users + [f"r{r.id}" for r in m.role_mentions] + (["e"] if m.mention_everyone else []))


def show_mentions(ids: str) -> str:
    out = []
    for tok in ids.split():
        out.append("@everyone" if tok == "e" else f"<@{tok[1:]}>" if tok[0] == "u" else f"<@&{tok[1:]}>")
    return " ".join(out)


def small_edit(before: str, after: str) -> bool:
    """A typo fix: nearly the same text, and no link or ping that wasn't there before."""
    if set(LINK_OR_PING.findall(after)) - set(LINK_OR_PING.findall(before)):
        return False
    return difflib.SequenceMatcher(None, before.lower(), after.lower()).ratio() >= SMALL_EDIT


def safe_name(name: str) -> str:
    return re.sub(r"[^\w.\-]", "_", name)[-80:]


def kept_files(message_id: int) -> list:
    return sorted(CACHE.glob(f"{message_id}_*"))


def take_files(message_id: int) -> list[discord.File]:
    """Saved copies of a deleted message's images, up to what one upload can carry."""
    out, total = [], 0
    for p in kept_files(message_id):
        size = p.stat().st_size
        if total + size <= 9.5 * 1024 * 1024 and len(out) < 10:
            out.append(discord.File(p, filename=p.name.split("_", 2)[2]))
            total += size
    return out


class ModLog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.del_counts: dict[int, int] = {}     # audit entry id -> deletes it counted
        self.invites: dict[str, tuple[int, int | None]] = {}   # code -> (uses, inviter id)
        self.joins: list[float] = []
        CACHE.mkdir(parents=True, exist_ok=True)
        self.prune.start()

    def cog_unload(self):
        self.prune.cancel()

    @tasks.loop(minutes=10)
    async def prune(self):
        s = settings()
        db.run("DELETE FROM msglog WHERE created<?", time.time() - s["keep_messages_days"] * 86400)
        timeline.prune()
        files = sorted(CACHE.glob("*"), key=lambda p: p.stat().st_mtime)
        cutoff, cap = time.time() - s["keep_images_hours"] * 3600, s["keep_images_mb"] * 1024 * 1024
        total = sum(p.stat().st_size for p in files)
        for p in files:
            if p.stat().st_mtime < cutoff or total > cap:
                total -= p.stat().st_size
                p.unlink(missing_ok=True)

    @commands.Cog.listener()
    async def on_ready(self):
        g = self.bot.get_guild(self.bot.guild_id)
        if g:
            await self.load_invites(g)
            await self.deleter(g, 0, 0)   # remember today's counts so old entries are never blamed

    async def user(self, uid: int) -> discord.abc.User | discord.Object:
        try:
            return self.bot.get_user(uid) or await self.bot.fetch_user(uid)
        except discord.HTTPException:
            return discord.Object(uid)

    # ---------- messages ----------
    @commands.Cog.listener()
    async def on_message(self, m: discord.Message):
        if not m.guild or m.author.bot or skip_channel(m.guild, m.channel):
            return
        db.run("INSERT OR REPLACE INTO msglog VALUES(?, ?, ?, ?, ?, ?, ?)", m.id, m.channel.id, m.author.id,
               m.content, "\n".join(a.filename for a in m.attachments), mention_ids(m), m.created_at.timestamp())
        if m.attachments and settings()["keep_images_mb"] > 0:
            asyncio.create_task(self.keep(m))

    async def keep(self, m: discord.Message):
        for i, a in enumerate(m.attachments[:10]):
            if (a.content_type or "").split("/")[0] in ("image", "video") and a.size <= MAX_FILE:
                try:
                    await a.save(CACHE / f"{m.id}_{i}_{safe_name(a.filename)}")
                except (discord.HTTPException, OSError) as e:
                    log.warning("log cache %s: %s", m.id, e)

    async def deleter(self, guild: discord.Guild, author_id: int, channel_id: int):
        """Who deleted someone else's message, from the audit log (None = they did it
        themselves). Discord folds repeat deletes into one entry and raises its count, so
        counts are remembered between calls."""
        found = None
        try:
            async for e in guild.audit_logs(limit=10, action=discord.AuditLogAction.message_delete):
                count = getattr(e.extra, "count", 1) or 1
                seen, self.del_counts[e.id] = self.del_counts.get(e.id), count
                if found or getattr(e.target, "id", None) != author_id \
                        or getattr(getattr(e.extra, "channel", None), "id", None) != channel_id:
                    continue
                fresh = (discord.utils.utcnow() - e.created_at).total_seconds() < 20
                if (seen is None and fresh) or (seen is not None and count > seen):
                    found = e.user or discord.Object(e.user_id)
        except discord.HTTPException:
            pass
        return found

    @commands.Cog.listener()
    async def on_raw_message_delete(self, p: discord.RawMessageDeleteEvent):
        g = self.bot.get_guild(p.guild_id) if p.guild_id else None
        ch = g.get_channel_or_thread(p.channel_id) if g else None
        if not ch or skip_channel(g, ch):
            return
        m, row = p.cached_message, db.one("SELECT * FROM msglog WHERE id=?", p.message_id)
        if (m and m.author.bot) or (m is None and row is None):
            return
        author = m.author if m else (g.get_member(row["author_id"]) or await self.user(row["author_id"]))
        content = m.content if m else row["content"]
        names = [a.filename for a in m.attachments] if m else [n for n in row["files"].split("\n") if n]
        created = m.created_at.timestamp() if m else row["created"]
        pinged = mention_ids(m) if m else row["mentions"]
        await asyncio.sleep(1.5)   # the audit log entry lands a moment after the delete
        by = await self.deleter(g, author.id, ch.id)
        ghost = bool(pinged) and by is None and time.time() - created < GHOST_SECONDS
        body = f"<@{author.id}> in {ch.mention} · sent {when(created)}\n{quote(content)}"
        if ghost:
            body += f"\n**Pinged** {show_mentions(pinged)}"
        files = take_files(p.message_id)
        if names:
            body += f"\n-# {len(names)} file(s): {', '.join(names)[:200]}" + (" · saved copies below" if files else "")
        card = log_card("Ghost ping" if ghost else "Message deleted", body,
                        tone="wait" if ghost else "bad" if by else "neutral",
                        user=author if isinstance(author, (discord.User, discord.Member)) else None, by=by)
        timeline.add(author.id, "message", f"{'Ghost ping' if ghost else 'Message deleted'} in {ch.mention}"
                                           + (f" by <@{by.id}>" if by else ""))
        await log_staff(g, "", kind="Messages", embed=card, files=files)
        for f in kept_files(p.message_id):
            f.unlink(missing_ok=True)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, p: discord.RawBulkMessageDeleteEvent):
        g = self.bot.get_guild(p.guild_id) if p.guild_id else None
        ch = g.get_channel_or_thread(p.channel_id) if g else None
        if not ch or skip_channel(g, ch):
            return
        ids = sorted(p.message_ids)
        cached = {m.id: m for m in p.cached_messages}
        rows = {r["id"]: r for r in db.q(f"SELECT * FROM msglog WHERE id IN ({','.join('?' * len(ids))})", *ids)}
        lines = []
        for mid in ids:
            if mid in cached:
                m = cached[mid]
                files = "".join(f" [file: {a.filename}]" for a in m.attachments)
                lines.append(f"[{m.created_at:%Y-%m-%d %H:%M}] {m.author} ({m.author.id}): {m.content}{files}")
            elif mid in rows:
                r = rows[mid]
                stamp = dt.datetime.fromtimestamp(r["created"], dt.timezone.utc)
                lines.append(f"[{stamp:%Y-%m-%d %H:%M}] {r['author_id']}: {r['content']}")
            else:
                lines.append(f"[older] message {mid} (text not stored)")
            for f in kept_files(mid):
                f.unlink(missing_ok=True)
        await asyncio.sleep(1.5)
        by = None
        try:
            async for e in g.audit_logs(limit=3, action=discord.AuditLogAction.message_bulk_delete):
                if getattr(e.target, "id", None) == ch.id and (discord.utils.utcnow() - e.created_at).total_seconds() < 30:
                    by = e.user or discord.Object(e.user_id)
                    break
        except discord.HTTPException:
            pass
        txt = discord.File(io.BytesIO("\n".join(lines).encode("utf-8")),
                           filename=f"purge-{ch.name}-{dt.datetime.now(dt.timezone.utc):%Y%m%d-%H%M}.txt")
        await log_staff(g, "", kind="Messages", file=txt,
                        embed=log_card("Messages purged", f"**{len(ids)}** messages in {ch.mention} (all in the file)",
                                       tone="bad", by=by))

    @commands.Cog.listener()
    async def on_raw_message_edit(self, p: discord.RawMessageUpdateEvent):
        m = p.message
        if not m.guild or m.author.bot or skip_channel(m.guild, m.channel):
            return
        row = None if p.cached_message else db.one("SELECT content FROM msglog WHERE id=?", m.id)
        before = p.cached_message.content if p.cached_message else (row["content"] if row else None)
        if before is None or before == m.content:   # unknown old message, or only an embed loaded
            return
        db.run("UPDATE msglog SET content=?, mentions=? WHERE id=?", m.content, mention_ids(m), m.id)
        if small_edit(before, m.content):
            return
        timeline.add(m.author.id, "message", f"Edited a message in {m.channel.mention}")
        await log_staff(m.guild, "", kind="Messages", embed=log_card(
            "Message edited", f"{m.author.mention} in {m.channel.mention} · [jump]({m.jump_url})\n"
                              f"**Before**\n{quote(before, 450)}\n**After**\n{quote(m.content, 450)}", user=m.author))

    # ---------- members ----------
    async def load_invites(self, g: discord.Guild):
        try:
            self.invites = {i.code: (i.uses or 0, i.inviter.id if i.inviter else None) for i in await g.invites()}
        except discord.HTTPException:
            pass

    async def used_invite(self, g: discord.Guild) -> tuple[str, int | None, int] | None:
        old = dict(self.invites)
        await self.load_invites(g)
        for code, (uses, inviter) in self.invites.items():
            if uses > old.get(code, (0, None))[0]:
                return code, inviter, uses
        gone = [c for c in old if c not in self.invites]   # a last-use invite disappears when used
        return (gone[0], old[gone[0]][1], old[gone[0]][0] + 1) if len(gone) == 1 else None

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        self.invites[invite.code] = (invite.uses or 0, invite.inviter.id if invite.inviter else None)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        self.invites.pop(invite.code, None)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        g, now = member.guild, time.time()
        self.joins = [t for t in self.joins if now - t < 30] + [now]
        inv = await self.used_invite(g) if len(self.joins) <= 5 else None   # skip the lookups during a join wave
        young = (discord.utils.utcnow() - member.created_at).days < 7
        flagged = member.public_flags.spammer
        invite = f"Invite `{inv[0]}`" + (f" from <@{inv[1]}>" if inv[1] else "") + f" · {inv[2]} uses" if inv else None
        timeline.add(member.id, "member", "Joined" + (f" · {invite}" if invite else ""))
        if not (young or flagged or member.bot):   # a normal join is one line
            return await log_staff(g, "", kind="Activity", event="join", embed=log_card(
                None, f"**Joined** {member.mention} · member {g.member_count:,}"))
        lines = [f"{member.mention} · member {g.member_count:,}",
                 f"Account made {when(member.created_at.timestamp())}" + (" · **new account**" if young else "")]
        if invite:
            lines.append(invite)
        if flagged:
            lines.append("**Discord flags this account as a likely spammer**")
        if member.bot:
            lines.append("**Bot**")
        await log_staff(g, "", kind="Activity", event="join", embed=log_card(
            "Joined", "\n".join(lines), tone="wait" if young or flagged else "neutral", user=member))

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        timeline.add(member.id, "member", "Left")
        joined = f" · joined {when(member.joined_at.timestamp())}" if member.joined_at else ""
        if not is_staff(member):                   # a normal leave is one line
            return await log_staff(member.guild, "", kind="Activity", event="leave", embed=log_card(
                None, f"**Left** {member.mention}{joined}"))
        roles = [r.mention for r in reversed(member.roles) if not r.is_default()]
        await log_staff(member.guild, "", kind="Activity", event="leave", embed=log_card(
            "Staff member left", f"{member.mention}{joined}\nRoles " + " ".join(roles)[:900], tone="wait", user=member))

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User):
        g = self.bot.get_guild(self.bot.guild_id)
        if after.bot or not g or not g.get_member(after.id):
            return
        lines = []
        if before.name != after.name:
            lines.append(f"Username **{before.name}** → **{after.name}**")
        if before.global_name != after.global_name:
            lines.append(f"Display name **{before.global_name or '-'}** → **{after.global_name or '-'}**")
        avatar = before.avatar != after.avatar
        if avatar:
            lines.append("New avatar →")
        if lines:
            timeline.add(after.id, "profile", " · ".join(lines).removesuffix(" →"))
            await log_staff(g, "", kind="Activity", event="profile", embed=log_card(
                "Profile changed", f"{after.mention}\n" + "\n".join(lines), user=after,
                thumb=after.display_avatar.url if avatar else None))

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.premium_since != after.premium_since:
            timeline.add(after.id, "member", "Boosted the server" if after.premium_since else "Stopped boosting")
            await log_staff(after.guild, "", kind="Activity", event="boost", embed=log_card(
                "Boosted the server" if after.premium_since else "Stopped boosting", after.mention,
                tone="good" if after.premium_since else "neutral", user=after))
        if before.nick != after.nick:
            await asyncio.sleep(3)   # changes with an audit entry are handled by auditlog.py (with who did it)
            audit = self.bot.get_cog("AuditLog")
            if not (audit and audit.saw(discord.AuditLogAction.member_update, after.id)):
                change = f"**{before.nick or before.name}** → **{after.nick or after.name}**"
                timeline.add(after.id, "profile", f"Nickname {change}")
                await log_staff(after.guild, "", kind="Activity", event="self_nick", embed=log_card(
                    "Nickname changed", f"{after.mention}\n{change}", user=after))

    # ---------- voice ----------
    @commands.Cog.listener()
    async def on_voice_state_update(self, m: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if m.bot or before.channel == after.channel:
            return
        if before.channel is None:
            text = f"joined **{after.channel.name}**"
        elif after.channel is None:
            text = f"left **{before.channel.name}**"
        else:
            text = f"moved **{before.channel.name}** → **{after.channel.name}**"
        timeline.add(m.id, "voice", f"Voice: {text}")
        await log_staff(m.guild, "", kind="Activity", event="voice_moves", embed=log_card(None, f"{m.mention} {text}", user=m))


async def setup(bot):
    await bot.add_cog(ModLog(bot))
