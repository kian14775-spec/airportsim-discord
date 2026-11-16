"""Creator pipeline + the #creators-room card. Every platform, no API keys, no cost.

- Link my channels: YouTube, TikTok, Twitch and one more (Kick, Instagram or X). Shown on the card.
- YouTube posts itself: every `poll_minutes` the bot reads each linked channel's free RSS feed. A new
  video that mentions the game (keywords in title or description) is posted in #socials with credit.
  Videos already up when the channel was linked are never posted.
- Everything else has no free feed, so creators press Share a post (a TikTok, clip, reel, X post...)
  or Going live (Twitch, TikTok, YouTube or Kick) and paste the link. Same link twice is refused,
  `share_per_day` posts per 24 h, live once per `live_cooldown_hours`. Every share is logged for staff.
- Points: a posted video/post = the `video` rule (once per link). Going live gives no points.
- On the 1st of each month: "Top creators" in #socials (most posts last month).
Settings: config/bot.yaml `creators:`.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlparse

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
CREATE TABLE IF NOT EXISTS creator_links (user_id INTEGER NOT NULL, platform TEXT NOT NULL, url TEXT NOT NULL,
    PRIMARY KEY (user_id, platform));
CREATE TABLE IF NOT EXISTS creator_posts (url TEXT PRIMARY KEY, user_id INTEGER NOT NULL, platform TEXT NOT NULL,
    kind TEXT NOT NULL, at REAL NOT NULL);
INSERT OR IGNORE INTO creator_links(user_id, platform, url)
    SELECT user_id, 'youtube', 'https://www.youtube.com/channel/' || yt_id FROM creators;
""")
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
      "media": "http://search.yahoo.com/mrss/"}
CHANNEL_ID = re.compile(r"\b(UC[\w-]{22})\b")
PAGE_ID = [re.compile(r'"externalId":"(UC[\w-]{22})"'), re.compile(r'youtube\.com/channel/(UC[\w-]{22})'),
           re.compile(r'"channelId":"(UC[\w-]{22})"')]
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"

# key: (name, domains). Order = how they're listed on the card.
PLATFORMS = {"youtube": ("YouTube", ("youtube.com", "youtu.be")), "tiktok": ("TikTok", ("tiktok.com",)),
             "twitch": ("Twitch", ("twitch.tv",)), "kick": ("Kick", ("kick.com",)),
             "instagram": ("Instagram", ("instagram.com",)), "x": ("X", ("x.com", "twitter.com"))}
LIVE_ON = ("twitch", "tiktok", "youtube", "kick")
OTHER = ("kick", "instagram", "x")          # the 4th box in Link my channels
YT_VIDEO = re.compile(r"^[\w-]{11}$")


def settings() -> dict:
    return cfg().get("creators", {})


def pname(key: str) -> str:
    return PLATFORMS.get(key, (key.capitalize(),))[0]


def platform_of(url: str) -> str | None:
    """'tiktok', 'twitch', ... for an http(s) link on a platform we know, else None."""
    try:
        u = urlparse(url.strip())
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or not host:
        return None
    for key, (_, domains) in PLATFORMS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return key
    return None


def youtube_id(url: str) -> str | None:
    """The video id of a YouTube watch / youtu.be / shorts / live link."""
    u = urlparse(url.strip())
    host = (u.hostname or "").lower()
    if host.endswith("youtu.be"):
        vid = u.path.strip("/").split("/")[0]
    elif u.path.startswith(("/shorts/", "/live/", "/embed/")):
        vid = u.path.split("/")[2] if len(u.path.split("/")) > 2 else ""
    else:
        vid = (parse_qs(u.query).get("v") or [""])[0]
    return vid if YT_VIDEO.match(vid or "") else None


def clean_url(url: str) -> str:
    """One spelling per post (no tracking bits), so the same link can't be shared twice."""
    if (vid := youtube_id(url)) and platform_of(url) == "youtube":
        return f"https://www.youtube.com/watch?v={vid}"
    u = urlparse(url.strip())
    host = (u.hostname or "").lower()
    for pre in ("www.", "m."):
        host = host.removeprefix(pre)
    return f"https://{host}{u.path.rstrip('/')}"


def as_profile(platform: str, text: str) -> str | None:
    """A profile link from a link or a bare @name, for one platform."""
    t = text.strip()
    if not t:
        return None
    if not t.startswith("http"):
        name = t.lstrip("@").strip("/")
        if not re.fullmatch(r"[\w.\-]{2,40}", name):
            return None
        return {"tiktok": f"https://www.tiktok.com/@{name}", "twitch": f"https://www.twitch.tv/{name}",
                "kick": f"https://kick.com/{name}", "instagram": f"https://www.instagram.com/{name}",
                "x": f"https://x.com/{name}"}.get(platform)
    return t if platform_of(t) == platform else None


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


def links_of(user_id: int) -> dict[str, str]:
    return {r["platform"]: r["url"] for r in db.q("SELECT * FROM creator_links WHERE user_id=?", user_id)}


def creator_lines() -> list[str]:
    """'@name · YouTube, TikTok' per creator, for the card."""
    by: dict[int, list[str]] = {}
    for r in db.q("SELECT user_id, platform FROM creator_links"):
        by.setdefault(r["user_id"], []).append(r["platform"])
    order = list(PLATFORMS)
    return [f"<@{uid}> · " + ", ".join(pname(p) for p in sorted(ps, key=lambda k: order.index(k) if k in order else 99))
            for uid, ps in by.items()][:20]


async def _rerender(interaction: discord.Interaction):
    cog = interaction.client.get_cog("Creators")
    if cog:
        await cog.render()


def _out(guild: discord.Guild):
    return channel(guild, settings().get("post_channel", "socials"))


# ---------------------------------------------------------------- link
class LinkModal(discord.ui.Modal, title="Link my channels"):
    def __init__(self, have: dict[str, str]):
        super().__init__()
        other = next((have[k] for k in OTHER if k in have), "")
        self.yt = discord.ui.TextInput(label="YouTube", required=False, max_length=200, default=have.get("youtube", ""),
                                       placeholder="youtube.com/@yourname  (new videos post themselves)")
        self.tt = discord.ui.TextInput(label="TikTok", required=False, max_length=200, default=have.get("tiktok", ""),
                                       placeholder="@yourname or your profile link")
        self.tw = discord.ui.TextInput(label="Twitch", required=False, max_length=200, default=have.get("twitch", ""),
                                       placeholder="yourname or your channel link")
        self.ot = discord.ui.TextInput(label="Kick, Instagram or X (one link)", required=False, max_length=200,
                                       default=other, placeholder="Leave everything empty to unlink")
        for item in (self.yt, self.tt, self.tw, self.ot):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        u, have, saved, problems = interaction.user, links_of(interaction.user.id), [], []

        def put(platform: str, url: str | None):
            if url:
                db.run("INSERT OR REPLACE INTO creator_links(user_id, platform, url) VALUES(?, ?, ?)", u.id, platform, url)
                saved.append(pname(platform))
            else:
                db.run("DELETE FROM creator_links WHERE user_id=? AND platform=?", u.id, platform)

        # YouTube: resolve + read the feed only when it changed (what's already up is never posted)
        yt_text = self.yt.value.strip()
        if not yt_text:
            put("youtube", None)
            db.run("DELETE FROM creators WHERE user_id=?", u.id)
        elif yt_text == have.get("youtube"):
            saved.append("YouTube")
        else:
            yt = await resolve(yt_text)
            feed = await fetch_feed(yt) if yt else None
            if not feed:
                problems.append("YouTube: I couldn't find that channel. Paste the link from your channel page "
                                "(youtube.com/@name).")
            else:
                name, videos = feed
                db.run("INSERT OR REPLACE INTO creators(user_id, yt_id, name, linked, fails) VALUES(?, ?, ?, ?, 0)",
                       u.id, yt, name or yt, db.now())
                for v in videos:
                    db.run("INSERT OR IGNORE INTO yt_seen(video_id, user_id, at, title) VALUES(?, ?, ?, ?)",
                           v["id"], u.id, db.now(), v["title"][:200])
                put("youtube", yt_text if yt_text.startswith("http") else f"https://www.youtube.com/channel/{yt}")
        for platform, box in (("tiktok", self.tt), ("twitch", self.tw)):
            url = as_profile(platform, box.value)
            if box.value.strip() and not url:
                problems.append(f"{pname(platform)}: that doesn't look like a {pname(platform)} name or link.")
                continue
            put(platform, url)
        other = self.ot.value.strip()
        kind = platform_of(other) if other else None
        if other and kind not in OTHER:
            problems.append("Last box: paste a Kick, Instagram or X profile link.")
        else:
            for k in OTHER:
                put(k, other if k == kind else None)
        lines = [("Linked: " + ", ".join(saved)) if saved else "Nothing linked now."]
        if "YouTube" in saved:
            out = _out(interaction.guild)
            lines.append(f"New YouTube videos about Airport Simulator are shared in {out.mention if out else '#socials'} "
                         "by themselves. Put \"Airport Simulator\" in the title or description so I spot them.")
        if set(saved) - {"YouTube"}:
            lines.append("For everything else press **Share a post** or **Going live** on the card.")
        await interaction.followup.send("\n".join(lines + [f"- {p}" for p in problems]), ephemeral=True)
        await log_staff(interaction.guild, f"{u.mention} linked: {', '.join(saved) or 'nothing (unlinked)'}",
                        kind="Members", event="creator_linked")
        await _rerender(interaction)


class LinkButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Link my channels", style=discord.ButtonStyle.primary, custom_id="asbot:creator:link")

    async def callback(self, interaction: discord.Interaction):
        if not can_link(interaction.user):
            return await interaction.response.send_message("This is for the PR team and Star Program creators.",
                                                           ephemeral=True)
        await interaction.response.send_modal(LinkModal(links_of(interaction.user.id)))


# ---------------------------------------------------------------- share + live
def shared_today(user_id: int) -> int:
    return db.one("SELECT COUNT(*) n FROM creator_posts WHERE user_id=? AND kind='post' AND at>?",
                  user_id, db.now() - 86400)["n"]


def last_live(user_id: int) -> float:
    r = db.one("SELECT MAX(at) t FROM creator_posts WHERE user_id=? AND kind='live'", user_id)
    return (r["t"] or 0) if r else 0


class ShareModal(discord.ui.Modal, title="Share a post"):
    link = discord.ui.TextInput(label="Link", max_length=300, placeholder="A TikTok, YouTube video or short, clip, reel or X post")
    note = discord.ui.TextInput(label="What's it about? (optional)", required=False, max_length=100,
                                placeholder="New tower controls tutorial")

    async def on_submit(self, interaction: discord.Interaction):
        u, url = interaction.user, self.link.value.strip()
        kind = platform_of(url)
        if not kind:
            return await interaction.response.send_message(
                "That link isn't from YouTube, TikTok, Twitch, Kick, Instagram or X.", ephemeral=True)
        key = clean_url(url)
        vid = youtube_id(url) if kind == "youtube" else None
        if db.one("SELECT 1 FROM creator_posts WHERE url=?", key) or \
                (vid and db.one("SELECT 1 FROM yt_seen WHERE video_id=? AND posted=1", vid)):
            return await interaction.response.send_message("That one's already been shared.", ephemeral=True)
        cap = int(settings().get("share_per_day", 3))
        if shared_today(u.id) >= cap:
            return await interaction.response.send_message(f"That's {cap} shares today. Try again tomorrow.",
                                                           ephemeral=True)
        out = _out(interaction.guild)
        if out is None:
            return await interaction.response.send_message("The socials channel is missing. Staff have been told.",
                                                           ephemeral=True)
        note = self.note.value.strip()
        msg = await out.send(f"New on **{pname(kind)}** from {u.mention}" + (f": {note}" if note else "") + f"\n{key}",
                             allowed_mentions=discord.AllowedMentions.none())
        db.run("INSERT INTO creator_posts(url, user_id, platform, kind, at) VALUES(?, ?, ?, 'post', ?)",
               key, u.id, kind, db.now())
        if vid:
            db.run("INSERT OR REPLACE INTO yt_seen(video_id, user_id, at, title, posted) VALUES(?, ?, ?, ?, 1)",
                   vid, u.id, db.now(), note[:200])
        points.award(u, "video", f"post:{key}", note=(note or pname(kind))[:80])
        await interaction.response.send_message(f"Shared in {out.mention}. Thanks!", ephemeral=True)
        await log_staff(interaction.guild, f"{u.mention} shared a {pname(kind)} post in {out.mention}: {msg.jump_url}",
                        kind="Members", event="creator_post")


class LiveModal(discord.ui.Modal, title="Going live"):
    def __init__(self, default: str = ""):
        super().__init__()
        self.link = discord.ui.TextInput(label="Stream link", max_length=300, default=default,
                                         placeholder="twitch.tv/yourname, your TikTok, YouTube or Kick live link")
        self.note = discord.ui.TextInput(label="What are you doing? (optional)", required=False, max_length=100,
                                         placeholder="Running a full day at the new airport")
        self.add_item(self.link)
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction):
        u, url = interaction.user, self.link.value.strip()
        kind = platform_of(url)
        if kind not in LIVE_ON:
            return await interaction.response.send_message("Paste your Twitch, TikTok, YouTube or Kick link.",
                                                           ephemeral=True)
        hours = float(settings().get("live_cooldown_hours", 4))
        wait = last_live(u.id) + hours * 3600 - db.now()
        if wait > 0:
            return await interaction.response.send_message(
                f"You already went live recently. You can post again <t:{int(db.now() + wait)}:R>.", ephemeral=True)
        out = _out(interaction.guild)
        if out is None:
            return await interaction.response.send_message("The socials channel is missing. Staff have been told.",
                                                           ephemeral=True)
        note = self.note.value.strip()
        msg = await out.send(f"🔴 {u.mention} is live on **{pname(kind)}**" + (f": {note}" if note else "") +
                             f"\n{clean_url(url)}", allowed_mentions=discord.AllowedMentions.none())
        db.run("INSERT INTO creator_posts(url, user_id, platform, kind, at) VALUES(?, ?, ?, 'live', ?)",
               f"live:{u.id}:{int(db.now())}", u.id, kind, db.now())
        await interaction.response.send_message(f"Posted in {out.mention}. Have a good stream!", ephemeral=True)
        await log_staff(interaction.guild, f"{u.mention} went live on {pname(kind)}: {msg.jump_url}",
                        kind="Members", event="creator_live")


class ShareButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Share a post", style=discord.ButtonStyle.secondary, custom_id="asbot:creator:share")

    async def callback(self, interaction: discord.Interaction):
        if not can_link(interaction.user):
            return await interaction.response.send_message("This is for the PR team and Star Program creators.",
                                                           ephemeral=True)
        await interaction.response.send_modal(ShareModal())


class LiveButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Going live", style=discord.ButtonStyle.secondary, custom_id="asbot:creator:live")

    async def callback(self, interaction: discord.Interaction):
        if not can_link(interaction.user):
            return await interaction.response.send_message("This is for the PR team and Star Program creators.",
                                                           ephemeral=True)
        have = links_of(interaction.user.id)
        await interaction.response.send_modal(LiveModal(next((have[k] for k in LIVE_ON if k in have), "")))


class GuideButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="How this works", style=discord.ButtonStyle.secondary, custom_id="asbot:creator:guide")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(view=page_view("pr"), ephemeral=True)


class CreatorsCard(cards.CardView):
    """#creators-room, pinned."""

    def __init__(self, linked: list[str] | None = None):
        super().__init__(cards.container(
            cards.title("Creators and events", "Videos, clips, streams and events that bring players in."),
            cards.divider(),
            cards.section("Your content", "Press **Link my channels** once: YouTube, TikTok, Twitch, Kick, "
                                          "Instagram or X.\nNew YouTube videos about the game post themselves "
                                          "in #socials. For anything else press **Share a post**, or **Going live** "
                                          "when you stream."),
            cards.row(LinkButton(), ShareButton(), LiveButton()),
            cards.divider(),
            cards.section("Events", "Plan an event and the bot does the rest: the Discord event, the post, a "
                                    "reminder an hour before and a screenshot thread afterwards."),
            cards.row(PlanEventButton(), GuideButton()),
            cards.divider(),
            cards.small("Creators: " + (" · ".join(linked) if linked else "nobody linked yet")) if linked is not None
            else None))


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
        msg = await cards.post_card("creators_card", room, CreatorsCard(creator_lines()))
        await cards.pin_quietly(msg)

    @commands.Cog.listener()
    async def on_ready(self):
        await self.render()

    @tasks.loop(minutes=30)
    @guarded
    async def poll(self):
        g = self.guild
        if g is None:
            return
        out = _out(g)
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
                key = f"https://www.youtube.com/watch?v={v['id']}"
                if not about_game(v) or out is None or db.one("SELECT 1 FROM creator_posts WHERE url=?", key):
                    continue
                try:
                    await out.send(f"New on **YouTube** from <@{c['user_id']}>: **{v['title']}**\n{key}",
                                   allowed_mentions=discord.AllowedMentions.none())
                    db.run("UPDATE yt_seen SET posted=1 WHERE video_id=?", v["id"])
                    db.run("INSERT OR IGNORE INTO creator_posts(url, user_id, platform, kind, at) "
                           "VALUES(?, ?, 'youtube', 'post', ?)", key, c["user_id"], db.now())
                except discord.HTTPException as e:
                    log.warning("creator post: %s", e)
                m = g.get_member(c["user_id"])
                if m:
                    points.award(m, "video", f"yt:{v['id']}", note=v["title"][:80])
            await asyncio.sleep(1)
        await self.monthly(g, out)

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
        end = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        rows = db.q("SELECT user_id, COUNT(*) n FROM creator_posts WHERE kind='post' AND at>=? AND at<? "
                    "GROUP BY user_id ORDER BY n DESC LIMIT 3", start.timestamp(), end.timestamp())
        if not rows:
            return
        lines = "\n".join(f"**{i}.** <@{r['user_id']}> · {r['n']} post{'s' if r['n'] != 1 else ''}"
                          for i, r in enumerate(rows, 1))
        try:
            await out.send(view=cards.CardView(cards.container(
                cards.title(f"Top creators of {start:%B}", "Thanks for every video, clip and stream about "
                                                           "Airport Simulator!"),
                cards.divider(), cards.text(lines), accent=ui.BRAND)), allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as e:
            log.warning("top creators: %s", e)


async def setup(bot):
    await bot.add_cog(Creators(bot))
