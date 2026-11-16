"""Keeping the server alive, for free (config/bot.yaml `pulse:`).

- Live player count: the locked voice channel `live_count` is renamed "Playing now: 1,234" (every 10 min).
- Verify reminder: one DM to members who still haven't verified after a day. Never twice. Older members
  get it too, a few at a time, so it never looks like spam.
- Question of the day in #general at `topic_hour` UTC, skipped when chat is already busy.
- Shot of the week: the most-reacted screenshot in #media each Sunday, re-posted with credit.
- Poll reminder: Wednesday, a note in #staff-chat if #dev-logs had no poll that week.
- Boost thank-you card in #general (unless Discord's own boost message already goes there).
"""
from __future__ import annotations

import datetime as dt

import discord
from discord.ext import commands, tasks

from .. import cards, db, ui
from ..core import cfg, channel, guarded, log, role
from ..renamer import rename_soon

db.conn().executescript("CREATE TABLE IF NOT EXISTS verify_nudges (user_id INTEGER PRIMARY KEY, at REAL NOT NULL, sent INTEGER NOT NULL);")
LEVELS = [(2, "level 1: a better server icon and stream quality"),
          (7, "level 2: the server banner and role icons"),
          (14, "level 3: an animated banner and a custom invite link")]


def settings() -> dict:
    return cfg().get("pulse", {})


def week_key(d: dt.datetime) -> str:
    return f"{d:%G-W%V}"


def boost_line(count: int) -> str:
    for need, perk in LEVELS:
        if count < need:
            left = need - count
            return f"{left} more boost{'s unlock' if left != 1 else ' unlocks'} {perk}."
    return "We're at the top level. Thank you!"


class Pulse(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tick.start()
        self.counter.start()

    def cog_unload(self):
        self.tick.cancel()
        self.counter.cancel()

    @property
    def guild(self) -> discord.Guild | None:
        return self.bot.get_guild(self.bot.guild_id)

    # ---------- "Playing now: 1,234" ----------
    @tasks.loop(minutes=10)
    @guarded
    async def counter(self):
        g = self.guild
        if g is None or not settings().get("live_count", True):
            return
        ch = channel(g, "live_count")
        players = getattr(self.bot.get_cog("News"), "players", None)
        if isinstance(ch, discord.VoiceChannel) and players is not None:
            rename_soon(ch, f"Playing now: {players:,}")

    # ---------- the slower rhythm ----------
    @tasks.loop(minutes=10)
    @guarded
    async def tick(self):
        g = self.guild
        if g is None:
            return
        now = dt.datetime.now(dt.timezone.utc)
        await self.verify_nudges(g)
        if now.hour >= int(settings().get("topic_hour", 18)) and db.kv_get("pulse:topic_day") != f"{now:%Y-%m-%d}":
            db.kv_set("pulse:topic_day", f"{now:%Y-%m-%d}")
            await self.daily_topic(g, now)
        if settings().get("shot_of_week", True) and now.weekday() == 6 and now.hour >= 17 \
                and db.kv_get("pulse:shot_week") != week_key(now):
            db.kv_set("pulse:shot_week", week_key(now))
            await self.shot_of_week(g, now)
        if settings().get("poll_reminder", True) and now.weekday() == 2 and now.hour >= 12 \
                and db.kv_get("pulse:poll_week") != week_key(now):
            db.kv_set("pulse:poll_week", week_key(now))
            await self.poll_reminder(g, now)

    @tick.before_loop
    @counter.before_loop
    async def _ready(self):
        await self.bot.wait_until_ready()

    async def verify_nudges(self, g: discord.Guild):
        hours = settings().get("verify_nudge_hours", 24)
        verified, welcome = role(g, "verified"), channel(g, "welcome")
        if not hours or verified is None or welcome is None:
            return
        done = {r["user_id"] for r in db.q("SELECT user_id FROM verify_nudges")}
        cutoff = discord.utils.utcnow() - dt.timedelta(hours=float(hours))
        todo = sorted((m for m in g.members if not m.bot and m.id not in done and verified not in m.roles
                       and m.joined_at and m.joined_at < cutoff), key=lambda m: m.joined_at, reverse=True)
        for m in todo[:int(settings().get("verify_nudge_per_run", 6))]:
            view = cards.CardView(cards.container(
                cards.title("You're almost in!", f"You joined the **{g.name}** Discord, but most channels stay hidden "
                                                 "until you verify. It takes 30 seconds."),
                cards.text(f"Open {welcome.mention} and press **Verify**. Then you can chat, share screenshots, "
                           "get help and vote on what comes next."),
                cards.row(discord.ui.Button(label="Go to verify", url=welcome.jump_url)), accent=ui.BRAND))
            try:
                await m.send(view=view)
                sent = 1
            except discord.HTTPException:
                sent = 0
            db.run("INSERT OR REPLACE INTO verify_nudges VALUES(?, ?, ?)", m.id, db.now(), sent)

    async def daily_topic(self, g: discord.Guild, now: dt.datetime):
        topics, chat = settings().get("topics") or [], channel(g, "chat")
        if not topics or not isinstance(chat, discord.TextChannel):
            return
        recent = [m async for m in chat.history(limit=12, after=now - dt.timedelta(minutes=30))]
        if len([m for m in recent if not m.author.bot]) >= 10:   # chat is already alive: don't interrupt
            return
        i = db.kv_get("pulse:topic_i", 0) % len(topics)
        db.kv_set("pulse:topic_i", i + 1)
        try:
            await chat.send(view=cards.CardView(cards.container(
                cards.small("Question of the day"), cards.text(f"### {topics[i]}"), accent=ui.BRAND)))
        except discord.HTTPException as e:
            log.warning("daily topic: %s", e)

    async def shot_of_week(self, g: discord.Guild, now: dt.datetime):
        media = channel(g, "media")
        out = channel(g, settings().get("shot_channel", "socials"))
        if not isinstance(media, discord.TextChannel) or out is None:
            return
        shots, replies = [], {}
        async for m in media.history(limit=400, after=now - dt.timedelta(days=7)):
            if m.reference and m.reference.message_id:         # members can't react in #media: replies count too
                replies[m.reference.message_id] = replies.get(m.reference.message_id, 0) + 1
            if not m.author.bot and any((a.content_type or "").startswith("image/") for a in m.attachments):
                shots.append(m)
        best, score = None, 0
        for m in shots:
            s = sum(r.count for r in m.reactions) + replies.get(m.id, 0)
            if s > score:
                best, score = m, s
        if best is None or score < 3:
            return
        att = next(a for a in best.attachments if (a.content_type or "").startswith("image/"))
        if att.size > 8 * 1024 * 1024:
            return
        try:
            f = await att.to_file()
            await out.send(view=cards.CardView(cards.container(
                cards.title("Shot of the week", f"By {best.author.mention} · {score} reactions and replies in {media.mention}"),
                discord.ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{f.filename}")),
                cards.row(discord.ui.Button(label="See the post", url=best.jump_url)), accent=ui.BRAND)),
                files=[f], allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as e:
            log.warning("shot of the week: %s", e)

    async def poll_reminder(self, g: discord.Guild, now: dt.datetime):
        dev, staff = channel(g, "devlogs"), channel(g, "staff_chat")
        if not isinstance(dev, discord.TextChannel) or staff is None:
            return
        async for m in dev.history(limit=50, after=now - dt.timedelta(days=7)):
            if m.poll is not None:
                return
        try:
            await staff.send(view=cards.CardView(cards.container(
                cards.text(f"**No poll in {dev.mention} this week yet.** Polls get people talking: next airport, "
                           "next plane, next feature. Make one with **+** > **Create poll** in that channel."),
                accent=ui.SLATE)))
        except discord.HTTPException as e:
            log.warning("poll reminder: %s", e)

    # ---------- boosts ----------
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.premium_since is not None or after.premium_since is None:
            return
        g, chat = after.guild, channel(after.guild, "chat")
        if chat is None:
            return
        if g.system_channel and g.system_channel.id == chat.id and g.system_channel_flags.premium_subscriptions:
            return                                       # Discord already thanks them in #general
        try:
            await chat.send(view=cards.CardView(cards.container(
                cards.text(f"**Thank you {after.mention} for boosting the server!**"),
                cards.small(f"{g.premium_subscription_count} boosts · {boost_line(g.premium_subscription_count)}"),
                accent=ui.BRAND)), allowed_mentions=discord.AllowedMentions(users=True))
        except discord.HTTPException as e:
            log.warning("boost thanks: %s", e)


async def setup(bot):
    await bot.add_cog(Pulse(bot))
