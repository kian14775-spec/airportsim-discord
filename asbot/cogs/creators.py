"""Creator pipeline + the #creators-room card.

Creators press "Link my YouTube" (a channel link, @handle or channel id). Every `poll_minutes` the bot
reads each linked channel's free RSS feed (no API key). A new video that mentions the game (keywords
in the title or description) is posted in #socials with credit; PR team members get points.
Videos that were already up when the channel was linked are never posted.
On the 1st of each month: "Top creator" in #socials (most shared videos last month).
Settings: config/bot.yaml `creators:`.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import re
import xml.etree.ElementTree as ET

import aiohttp
import discord
from discord.ext import commands, tasks

from .. import cards, db, points, ui
from ..core import cfg, channel, guarded, http, is_staff, log, log_staff, role
from .duty import page_view
from .events import PlanEventButton

db.conn().executescript("""
CREATE TABLE IF NOT EXISTS creators (user_id INTEGER PRIMARY KEY, yt_id TEXT NOT NULL, name TEXT NOT NULL,
    linked REAL NOT NULL, fails INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS yt_seen (video_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, at REAL NOT NULL,
    posted INTEGER NOT NULL DEFAULT 0, title TEXT NOT NULL DEFAULT '');
""")
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
      "media": "http://search.yahoo.com/mrss/"}
CHANNEL_ID = re.compile(r"\b(UC[\w-]{22})\b")
PAGE_ID = [re.compile(r'"externalId":"(UC[\w-]{22})"'), re.compile(r'youtube\.com/channel/(UC[\w-]{22})'),
           re.compile(r'"channelId":"(UC[\w-]{22})"')]
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"


def settings() -> dict:
    return cfg().get("creators", {})


def parse_feed(xml: str) -> tuple[str, list[dict]]:
    """(channel name, [{'id', 'title', 'url', 'about', 'published'}]) newest first."""
    root = ET.fromstring(xml)
    name = (root.findtext("a:title", default="", namespaces=NS) or "").strip()
    out = []
    for e in root.findall("a:entry", NS):
        vid = e.findtext("yt:videoId", default="", namespaces=NS)
        link = e.find("a:link", NS)
        out.append(dict(id=vid, title=(e.findtext("a:title", default="", namespaces=NS) or "").strip(),
                        url=link.get("href") if link is not None else f"https://www.youtube.com/watch?v={vid}",
                        about=e.findtext("media:group/media:description", default="", namespaces=NS) or "",
                        published=e.findtext("a:published", default="", namespaces=NS)))
    return name, [v for v in out if v["id"]]


def about_game(video: dict) -> bool:
    words = [k.lower() for k in settings().get("keywords", ["airport simulator"])]
    text = f"{video['title']} {video['about']}".lower()
    return any(w in text for w in words)


async def fetch_feed(yt_id: str) -> tuple[str, list[dict]] | None:
    try:
        async with http().get(FEED.format(yt_id)) as r:
            if r.status != 200:
                return None
            return parse_feed(await r.text())
    except (aiohttp.ClientError, TimeoutError, ET.ParseError) as e:
        log.warning("yt feed %s: %s", yt_id, e)
        return None


async def resolve(text: str) -> str | None:
    """A channel id from a link, an @handle or the id itself (the public channel page, no key)."""
    t = text.strip()
    if m := CHANNEL_ID.search(t):
        return m.group(1)
    if t.startswith("@"):
        url = f"https://www.youtube.com/{t}"
    elif t.startswith("http"):
        url = t
    elif "youtube.com" in t or "youtu.be" in t:
        url = "https://" + t
    else:
        url = f"https://www.youtube.com/@{t}"
    if not re.match(r"^https://(www\.|m\.)?youtube\.com/", url):
        return None
    try:
        async with http().get(url, headers={"Accept-Language": "en"}) as r:
            if r.status != 200:
                return None
            page = await r.text()
    except (aiohttp.ClientError, TimeoutError) as e:
        log.warning("yt resolve %s: %s", url, e)
        return None
    for rx in PAGE_ID:
        if m := rx.search(page):
            return m.group(1)
    return None


def can_link(member: discord.abc.User) -> bool:
    if not isinstance(member, discord.Member):
        return False
    star = role(member.guild, "star_program")
    return is_staff(member) or points.is_lead(member) or points.dept_of(member) == "pr" or bool(star and star in member.roles)


class LinkModal(discord.ui.Modal, title="Link your YouTube"):
    where = discord.ui.TextInput(label="Your channel", max_length=200,
                                 placeholder="https://www.youtube.com/@yourname  or  @yourname")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        yt = await resolve(self.where.value)
        feed = await fetch_feed(yt) if yt else None
        if not feed:
            return await interaction.followup.send("I couldn't find that channel. Paste the link from your channel "
                                                   "page (it looks like youtube.com/@name).", ephemeral=True)
        name, videos = feed
        u = interaction.user
        db.run("INSERT OR REPLACE INTO creators(user_id, yt_id, name, linked, fails) VALUES(?, ?, ?, ?, 0)",
               u.id, yt, name or yt, db.now())
        for v in videos:                                  # what's already up is never posted
            db.run("INSERT OR IGNORE INTO yt_seen(video_id, user_id, at, title) VALUES(?, ?, ?, ?)",
                   v["id"], u.id, db.now(), v["title"][:200])
        out = channel(interaction.guild, settings().get("post_channel", "socials"))
        await interaction.followup.send(
            f"Linked **{name}**. New videos about Airport Simulator will be shared in "
            f"{out.mention if out else '#socials'} with your name, within about {settings().get('poll_minutes', 30)} "
            "minutes of going up. Put \"Airport Simulator\" in the title or description so I spot them.", ephemeral=True)
        await log_staff(interaction.guild, f"{u.mention} linked YouTube **{name}** (`{yt}`)", kind="Members",
                        event="creator_linked")


class LinkButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Link my YouTube", style=discord.ButtonStyle.success, custom_id="asbot:creator:link")

    async def callback(self, interaction: discord.Interaction):
        if not can_link(interaction.user):
            return await interaction.response.send_message("This is for the PR team and Star Program creators.",
                                                           ephemeral=True)
        await interaction.response.send_modal(LinkModal())


class UnlinkButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Unlink", style=discord.ButtonStyle.secondary, custom_id="asbot:creator:unlink")

    async def callback(self, interaction: discord.Interaction):
        gone = db.changes("DELETE FROM creators WHERE user_id=?", interaction.user.id)
        await interaction.response.send_message("Unlinked. Your videos won't be shared any more." if gone
                                                else "You don't have a channel linked.", ephemeral=True)


class GuideButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="How this works", style=discord.ButtonStyle.secondary, custom_id="asbot:creator:guide")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(view=page_view("pr"), ephemeral=True)


class CreatorsCard(cards.CardView):
    """#creators-room, pinned."""

    def __init__(self, linked: list[str] | None = None):
        super().__init__(cards.container(
            cards.title("Creators and events", "Make videos, posts and events that bring players in."),
            cards.divider(),
            cards.section("Videos", "Link your YouTube once. New Airport Simulator videos are shared in #socials "
                                    "with your name, by themselves."),
            cards.section("Events", "Plan an event and the bot does the rest: the Discord event, the post, a "
                                    "reminder an hour before and a screenshot thread afterwards."),
            cards.small("Linked: " + (", ".join(linked) if linked else "nobody yet")) if linked is not None else None,
            cards.row(LinkButton(), UnlinkButton(), PlanEventButton(), GuideButton()),
            accent=ui.SLATE))


class Creators(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(CreatorsCard())
        self.poll.change_interval(minutes=max(10, int(settings().get("poll_minutes", 30))))
        self.poll.start()

    def cog_unload(self):
        self.poll.cancel()

    @property
    def guild(self) -> discord.Guild | None:
        return self.bot.get_guild(self.bot.guild_id)

    async def render(self):
        g = self.guild
        room = channel(g, "creators_room") if g else None
        if room is None:
            return
        linked = [f"<@{r['user_id']}> ({r['name']})" for r in db.q("SELECT * FROM creators ORDER BY linked")][:20]
        msg = await cards.post_card("creators_card", room, CreatorsCard(linked))
        if msg and not msg.pinned:
            try:
                await msg.pin()
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_ready(self):
        await self.render()

    @tasks.loop(minutes=30)
    @guarded
    async def poll(self):
        g = self.guild
        if g is None:
            return
        out = channel(g, settings().get("post_channel", "socials"))
        for c in db.q("SELECT * FROM creators"):
            feed = await fetch_feed(c["yt_id"])
            if feed is None:
                db.run("UPDATE creators SET fails=fails+1 WHERE user_id=?", c["user_id"])
                continue
            _, videos = feed
            for v in reversed(videos):                    # oldest new one first
                if not db.changes("INSERT OR IGNORE INTO yt_seen(video_id, user_id, at, title) VALUES(?, ?, ?, ?)",
                                  v["id"], c["user_id"], db.now(), v["title"][:200]):
                    continue
                if not about_game(v) or out is None:
                    continue
                try:
                    await out.send(f"New video from <@{c['user_id']}>: **{v['title']}**\n{v['url']}",
                                   allowed_mentions=discord.AllowedMentions.none())
                    db.run("UPDATE yt_seen SET posted=1 WHERE video_id=?", v["id"])
                except discord.HTTPException as e:
                    log.warning("creator post: %s", e)
                m = g.get_member(c["user_id"])
                if m:
                    points.award(m, "video", f"yt:{v['id']}", note=v["title"][:80])
            await asyncio.sleep(1)
        await self.monthly(g, out)
        await self.render()

    @poll.before_loop
    async def _ready(self):
        await self.bot.wait_until_ready()

    async def monthly(self, g: discord.Guild, out):
        now = dt.datetime.now(dt.timezone.utc)
        key = f"{now:%Y-%m}"
        if db.kv_get("creators:month") is None:
            db.kv_set("creators:month", key)
        if db.kv_get("creators:month") == key or out is None:
            return
        db.kv_set("creators:month", key)
        start = (now.replace(day=1) - dt.timedelta(days=1)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        rows = db.q("SELECT user_id, COUNT(*) n FROM yt_seen WHERE posted=1 AND at>=? AND at<? GROUP BY user_id "
                    "ORDER BY n DESC LIMIT 3", start.timestamp(), now.replace(day=1, hour=0, minute=0, second=0,
                                                                                 microsecond=0).timestamp())
        if not rows:
            return
        lines = "\n".join(f"**{i}.** <@{r['user_id']}> · {r['n']} video{'s' if r['n'] != 1 else ''}"
                          for i, r in enumerate(rows, 1))
        try:
            await out.send(view=cards.CardView(cards.container(
                cards.title(f"Top creators of {start:%B}", "Thanks for every video about Airport Simulator!"),
                cards.text(lines), accent=ui.BRAND)), allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as e:
            log.warning("top creators: %s", e)


async def setup(bot):
    await bot.add_cog(Creators(bot))
