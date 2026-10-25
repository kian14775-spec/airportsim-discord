"""Player Hub forums: feedback, rp-servers and liveries.

Feedback: /suggest and /bug check for similar posts first, new posts get the New tag,
titles show live votes ("[+42] Add the A350"), and staff get a weekly top-10.
RP servers: /rp create makes one tidy post per owner; posts quiet for 30 days get a
"still active?" ping and are archived 7 days later.
Liveries: Showcase posts without an image get a reminder; during Livery of the Month
a pinned live top-5 post is kept up to date.
"""
from __future__ import annotations

import datetime as dt
import re

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .. import db
from ..core import channel, embed, log, staff_only
from ..renamer import rename_soon
from .community import UPVOTE, star

VOTE_PREFIX = re.compile(r"^\[[+-]?\d+\]\s*")
STALE_DAYS, ARCHIVE_AFTER_WARN_DAYS = 30, 7
STOP = {"the", "and", "for", "with", "add", "make", "please", "when", "game", "that", "this", "from", "into"}


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOP}


def similar(a: str, b: str) -> float:
    wa, wb = words(a), words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def base_title(name: str) -> str:
    return VOTE_PREFIX.sub("", name)


def tags(forum: discord.ForumChannel, *names: str) -> list[discord.ForumTag]:
    return [t for t in forum.available_tags if t.name in names][:5]


async def starter_of(thread: discord.Thread) -> discord.Message | None:
    try:
        return thread.starter_message or await thread.fetch_message(thread.id)
    except discord.HTTPException:
        return None


async def votes(thread: discord.Thread) -> int:
    msg = await starter_of(thread)
    if not msg:
        return 0
    return sum(max(0, r.count - (1 if r.me else 0)) for r in msg.reactions if str(r.emoji) == UPVOTE)


# ---------- feedback ----------
async def post_feedback(interaction: discord.Interaction, kind: str, title: str, body: str):
    forum = channel(interaction.guild, "feedback")
    made = await forum.create_thread(
        name=title, applied_tags=tags(forum, kind, "New"),
        content=f"{body}\n\n-# Posted by {interaction.user.mention}", allowed_mentions=discord.AllowedMentions.none())
    db.kv_set(f"feedback_author:{made.thread.id}", interaction.user.id)
    return made.thread


class PostAnyway(discord.ui.View):
    def __init__(self, kind, title, body):
        super().__init__(timeout=600)
        self.args = (kind, title, body)

    @discord.ui.button(label="Mine is different, post it", style=discord.ButtonStyle.primary)
    async def go(self, interaction: discord.Interaction, _):
        th = await post_feedback(interaction, *self.args)
        await interaction.response.edit_message(content=f"Posted: {th.mention}", embed=None, view=None)


class FeedbackModal(discord.ui.Modal):
    def __init__(self, kind: str):
        super().__init__(title="New suggestion" if kind == "Suggestion" else "Report a bug")
        self.kind = kind
        self.head = discord.ui.TextInput(label="Short title", max_length=80)
        self.body = discord.ui.TextInput(label="Details" if kind == "Suggestion" else "What happens, and where?",
                                         style=discord.TextStyle.paragraph, max_length=2000)
        self.add_item(self.head)
        self.add_item(self.body)

    async def on_submit(self, interaction: discord.Interaction):
        forum = channel(interaction.guild, "feedback")
        if not isinstance(forum, discord.ForumChannel):
            return await interaction.response.send_message("Feedback isn't set up yet.", ephemeral=True)
        title = self.head.value.strip()
        pool = list(forum.threads)
        async for th in forum.archived_threads(limit=100):
            pool.append(th)
        close = sorted(((similar(title, base_title(t.name)), t) for t in pool), key=lambda x: -x[0])
        close = [t for score, t in close if score >= 0.34][:3]
        if close:
            e = embed("Someone may have posted this already",
                      "Vote on one of these instead, so it rises to the top:\n" +
                      "\n".join(f"• {t.mention}" for t in close))
            return await interaction.response.send_message(embed=e, view=PostAnyway(self.kind, title, self.body.value),
                                                           ephemeral=True)
        th = await post_feedback(interaction, self.kind, title, self.body.value)
        await interaction.response.send_message(f"Posted: {th.mention}", ephemeral=True)


# ---------- rp servers ----------
class RpModal(discord.ui.Modal, title="Your airline / server"):
    name = discord.ui.TextInput(label="Name", max_length=60, placeholder="SkyLine Virtual")
    join = discord.ui.TextInput(label="How to join", max_length=300, placeholder="Private server link, Discord link...")
    fleet = discord.ui.TextInput(label="Fleet / airports", required=False, max_length=300)
    about = discord.ui.TextInput(label="About you", style=discord.TextStyle.paragraph, required=False, max_length=1000)

    def __init__(self, kind: str, hiring: bool, logo: discord.Attachment | None):
        super().__init__()
        self.kind, self.hiring, self.logo = kind, hiring, logo

    async def on_submit(self, interaction: discord.Interaction):
        forum = channel(interaction.guild, "rp_servers")
        if not isinstance(forum, discord.ForumChannel):
            return await interaction.response.send_message("rp-servers isn't set up yet.", ephemeral=True)
        e = embed(self.name.value, self.about.value or None)
        e.add_field(name="How to join", value=self.join.value, inline=False)
        if self.fleet.value:
            e.add_field(name="Fleet / airports", value=self.fleet.value, inline=False)
        e.add_field(name="Owner", value=interaction.user.mention)
        e.add_field(name="Hiring", value="Yes" if self.hiring else "No")
        if self.logo and (self.logo.content_type or "").startswith("image/"):
            e.set_thumbnail(url="attachment://" + self.logo.filename)
        files = [await self.logo.to_file()] if self.logo else []
        names = [self.kind] + (["Hiring"] if self.hiring else [])
        made = await forum.create_thread(name=self.name.value, embed=e, files=files, applied_tags=tags(forum, *names),
                                         allowed_mentions=discord.AllowedMentions.none())
        db.run("INSERT INTO rp_posts VALUES(?, ?, ?) ON CONFLICT(owner_id) DO UPDATE SET thread_id=excluded.thread_id, "
               "created=excluded.created", interaction.user.id, made.thread.id, db.now())
        await interaction.response.send_message(f"Your post is up: {made.thread.mention}", ephemeral=True)


class Forums(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.weekly.start()
        self.daily.start()
        self.contest_board.start()

    def cog_unload(self):
        for t in (self.weekly, self.daily, self.contest_board):
            t.cancel()

    @property
    def guild(self) -> discord.Guild:
        return self.bot.get_guild(self.bot.guild_id)

    @app_commands.command(description="Suggest something for the game")
    async def suggest(self, interaction: discord.Interaction):
        await interaction.response.send_modal(FeedbackModal("Suggestion"))

    @app_commands.command(description="Report a bug in the game")
    async def bug(self, interaction: discord.Interaction):
        await interaction.response.send_modal(FeedbackModal("Bug"))

    rp = app_commands.Group(name="rp", description="Your airline or private server post")

    @rp.command(name="create", description="Make (or replace) your post in rp-servers")
    @app_commands.choices(kind=[app_commands.Choice(name=k, value=k) for k in ("Airline", "Airport", "Cargo")])
    async def rp_create(self, interaction: discord.Interaction, kind: str, hiring: bool = False,
                        logo: discord.Attachment | None = None):
        row = db.one("SELECT thread_id FROM rp_posts WHERE owner_id=?", interaction.user.id)
        if row:
            old = self.guild.get_thread(row["thread_id"])
            if old and not old.archived:
                return await interaction.response.send_message(
                    f"You already have a post: {old.mention}. Edit it there, or archive it first.", ephemeral=True)
        await interaction.response.send_modal(RpModal(kind, hiring, logo))

    # ---------- events ----------
    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        fb, lv = channel(thread.guild, "feedback"), channel(thread.guild, "liveries")
        if fb and thread.parent_id == fb.id and "New" not in {t.name for t in thread.applied_tags}:
            try:   # posts made straight in the forum get New too
                await thread.edit(applied_tags=(thread.applied_tags + tags(fb, "New"))[:5])
            except discord.HTTPException:
                pass
        if lv and thread.parent_id == lv.id and "Showcase" in {t.name for t in thread.applied_tags}:
            msg = await starter_of(thread)
            if msg and not any((a.content_type or "").startswith("image/") for a in msg.attachments) \
                    and not any(e.image or e.thumbnail for e in msg.embeds):
                await thread.send(f"{thread.owner.mention if thread.owner else ''} Showcase posts need a picture of "
                                  "your livery. Add one so people can vote! Tell us the aircraft and airline too.")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, p: discord.RawReactionActionEvent):
        await self._vote_changed(p)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, p: discord.RawReactionActionEvent):
        await self._vote_changed(p)

    async def _vote_changed(self, p: discord.RawReactionActionEvent):
        if str(p.emoji) != UPVOTE or not self.guild:
            return
        th = self.guild.get_thread(p.channel_id)
        fb = channel(self.guild, "feedback")
        if not th or not fb or th.parent_id != fb.id or p.message_id != th.id:
            return
        n = await votes(th)
        rename_soon(th, (f"[+{n}] " if n else "") + base_title(th.name))

    # ---------- timers ----------
    @tasks.loop(hours=6)
    async def weekly(self):
        now = dt.datetime.now(dt.timezone.utc)
        week = f"{now:%G-W%V}"
        if now.weekday() != 0 or db.kv_get("digest_week") == week:
            return
        fb, staff = channel(self.guild, "feedback"), channel(self.guild, "staff_chat")
        if not fb or not staff:
            return
        rows = []
        for th in fb.threads:
            if "Done" not in {t.name for t in th.applied_tags}:
                rows.append((await votes(th), th))
        rows.sort(key=lambda x: -x[0])
        lines = [f"**{i}.** {th.mention} · {n} votes" for i, (n, th) in enumerate(rows[:10], 1)]
        await staff.send(embed=embed(f"Top feedback this week · {now:%d %b}",
                                     "\n".join(lines) or "No open feedback posts."))
        db.kv_set("digest_week", week)

    @tasks.loop(hours=24)
    async def daily(self):
        forum = channel(self.guild, "rp_servers")
        if not isinstance(forum, discord.ForumChannel):
            return
        for th in list(forum.threads):
            if th.flags.pinned:
                continue
            last = discord.utils.snowflake_time(th.last_message_id or th.id).timestamp()
            warned = db.one("SELECT warned FROM rp_stale WHERE thread_id=?", th.id)
            if warned and last > warned["warned"] + 60:   # the owner (or anyone) posted after the warning
                db.run("DELETE FROM rp_stale WHERE thread_id=?", th.id)
                continue
            if not warned and db.now() - last >= STALE_DAYS * 86400:
                await th.send(f"{th.owner.mention if th.owner else 'Owner'}: is this still active? Post anything "
                              f"here to keep it listed, otherwise it's archived in {ARCHIVE_AFTER_WARN_DAYS} days.")
                db.run("INSERT INTO rp_stale VALUES(?, ?)", th.id, db.now())
            elif warned and db.now() - warned["warned"] > ARCHIVE_AFTER_WARN_DAYS * 86400:
                await th.edit(archived=True)
                db.run("DELETE FROM rp_stale WHERE thread_id=?", th.id)

    @tasks.loop(hours=1)
    async def contest_board(self):
        start = db.kv_get("contest_start")
        forum = channel(self.guild, "liveries")
        if not start or not isinstance(forum, discord.ForumChannel):
            return
        since = dt.datetime.fromtimestamp(start, dt.timezone.utc)
        scores = []
        for th in forum.threads:
            if th.created_at and th.created_at >= since and "Showcase" in {t.name for t in th.applied_tags}:
                msg = await starter_of(th)
                if msg:
                    n = sum(max(0, r.count - (1 if r.me else 0)) for r in msg.reactions if str(r.emoji) == star())
                    scores.append((n, th))
        scores.sort(key=lambda x: -x[0])
        text = ("**Livery of the Month: live top 5**\nVote with " + star() + " on your favourite Showcase posts.\n\n" +
                ("\n".join(f"**{i}.** {th.mention} · {n} {star()}" for i, (n, th) in enumerate(scores[:5], 1))
                 or "No entries yet. Post yours with the Showcase tag!"))
        tid = db.kv_get("contest_board")
        th = self.guild.get_thread(tid) if tid else None
        try:
            if th is None:
                made = await forum.create_thread(name="🏆 Livery of the Month: live top 5", content=text,
                                                 applied_tags=tags(forum, "Showcase"))
                db.kv_set("contest_board", made.thread.id)
                await made.thread.edit(pinned=True, locked=True)
            else:
                msg = await starter_of(th)
                if msg and msg.content != text:
                    await msg.edit(content=text)
        except discord.HTTPException as e:
            log.warning("contest board: %s", e)

    @app_commands.command(description="Refresh the live Livery of the Month board now (staff)")
    @staff_only()
    async def contest_refresh(self, interaction: discord.Interaction):
        await interaction.response.send_message("Refreshing.", ephemeral=True)
        await self.contest_board()

    @weekly.before_loop
    @daily.before_loop
    @contest_board.before_loop
    async def _ready(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Forums(bot))
