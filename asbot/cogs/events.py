"""Event engine: "Plan an event" (creators-room card, /staff) -> a real Discord event with a cover,
a post in #announcements, a reminder in #general an hour before, the event started and ended on time,
then a "share your screenshots" post in #media. The host earns points when enough people were interested.
Times are typed in UTC ("2026-11-20 18:00", "tomorrow 18:00", "in 2h"); the preview shows them in
everyone's own time before anything is made. Settings: config/bot.yaml `events:`.
"""
from __future__ import annotations

import datetime as dt
import re

import discord
from discord.ext import commands, tasks

from .. import cards, db, points, ui
from ..cards import BANNERS
from ..core import cfg, channel, guarded, is_staff, log, log_staff, role

db.conn().executescript("""
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER, host_id INTEGER NOT NULL,
    title TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL, state TEXT NOT NULL DEFAULT 'planned',
    reminded INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
""")
IN = re.compile(r"^in\s+(?:(\d+)\s*h(?:ours?)?)?\s*(?:(\d+)\s*m(?:in(?:utes?)?)?)?$")
CLOCK = re.compile(r"^(?:(today|tomorrow)\s+)?(\d{1,2})[:.h](\d{2})$")
FULL = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})[ t]+(\d{1,2})[:.](\d{2})$")


def settings() -> dict:
    return cfg().get("events", {})


def parse_when(text: str, now: dt.datetime) -> dt.datetime | None:
    """UTC. 'in 2h', 'in 90m', 'in 1h 30m', '18:00' (next one), 'today 18:00', 'tomorrow 18:00',
    '2026-11-20 18:00'. None if it can't be read."""
    t = text.strip().lower().replace("utc", "").strip()
    if m := IN.match(t):
        if not (m.group(1) or m.group(2)):
            return None
        return now + dt.timedelta(hours=int(m.group(1) or 0), minutes=int(m.group(2) or 0))
    if m := CLOCK.match(t):
        h, mi = int(m.group(2)), int(m.group(3))
        if h > 23 or mi > 59:
            return None
        at = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if m.group(1) == "tomorrow":
            at += dt.timedelta(days=1)
        elif m.group(1) is None and at <= now:
            at += dt.timedelta(days=1)
        return at
    if m := FULL.match(t):
        try:
            return dt.datetime(*(int(x) for x in m.groups()), tzinfo=dt.timezone.utc)
        except ValueError:
            return None
    return None


def can_host(member: discord.abc.User) -> bool:
    if not isinstance(member, discord.Member):
        return False
    return is_staff(member) or points.is_lead(member) or points.dept_of(member) in settings().get("hosts", ["pr"])


def event_url(guild_id: int, event_id: int) -> str:
    return f"https://discord.com/events/{guild_id}/{event_id}"


class PlanModal(discord.ui.Modal, title="Plan an event"):
    name = discord.ui.TextInput(label="Name", max_length=100, placeholder="Rush hour at the airport")
    when = discord.ui.TextInput(label="Start, in UTC", max_length=40,
                                placeholder="tomorrow 18:00  ·  2026-11-20 18:00  ·  in 2h")
    length = discord.ui.TextInput(label="How long? (minutes)", max_length=3, default="60")
    about = discord.ui.TextInput(label="What happens? (optional)", style=discord.TextStyle.paragraph,
                                 max_length=800, required=False,
                                 placeholder="Join the group flight from the main airport. Bring your best livery!")

    async def on_submit(self, interaction: discord.Interaction):
        now = discord.utils.utcnow()
        start = parse_when(self.when.value, now)
        try:
            mins = int(self.length.value.strip())
        except ValueError:
            mins = 0
        if start is None:
            return await interaction.response.send_message(
                "I couldn't read that time. Try `tomorrow 18:00`, `2026-11-20 18:00` or `in 2h` (UTC).", ephemeral=True)
        if start < now + dt.timedelta(minutes=10):
            return await interaction.response.send_message("Pick a start at least 10 minutes from now.", ephemeral=True)
        if not 10 <= mins <= 600:
            return await interaction.response.send_message("Length: between 10 and 600 minutes.", ephemeral=True)
        plan = dict(name=self.name.value.strip(), start=start.timestamp(), mins=mins, about=self.about.value.strip())
        view = cards.CardView(cards.container(
            cards.title(plan["name"], f"Starts <t:{int(plan['start'])}:F> (that's your own time) · "
                                      f"<t:{int(plan['start'])}:R> · {mins} min"),
            cards.text(plan["about"] or "-# No details."),
            cards.small("Wrong time? Cancel and plan it again. The time you type is UTC."),
            cards.row(CreateButton(plan), CancelButton()), accent=ui.BRAND), timeout=900)
        await interaction.response.send_message(view=view, ephemeral=True)


class CancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Cancel", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=cards.CardView(cards.container(cards.text("Cancelled."))))


class CreateButton(discord.ui.Button):
    def __init__(self, plan: dict):
        super().__init__(label="Create the event", style=discord.ButtonStyle.success)
        self.plan = plan

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        g, p, host = interaction.guild, self.plan, interaction.user
        s = settings()
        start = dt.datetime.fromtimestamp(p["start"], dt.timezone.utc)
        end = start + dt.timedelta(minutes=p["mins"])
        link = cfg()["game"].get("link", "")
        cover = BANNERS / f"{s.get('banner', 'welcome')}.png"
        try:
            ev = await g.create_scheduled_event(
                name=p["name"], start_time=start, end_time=end, entity_type=discord.EntityType.external,
                privacy_level=discord.PrivacyLevel.guild_only, location=s.get("location", "Airport Simulator")[:100],
                description=((p["about"] + "\n\n") if p["about"] else "") + f"Hosted by {host.display_name}. {link}"[:1000],
                image=cover.read_bytes() if cover.exists() else discord.utils.MISSING,
                reason=f"Planned by {host}")
        except discord.HTTPException as e:
            log.warning("event create: %s", e)
            return await interaction.followup.send("Discord said no to the event"
                                                   + (": I need the **Manage Events** permission." if e.status == 403
                                                      else f" ({e.text or e.status})."), ephemeral=True)
        db.run("INSERT INTO events(event_id, host_id, title, start, end, created) VALUES(?, ?, ?, ?, ?, ?)",
               ev.id, host.id, p["name"], start.timestamp(), end.timestamp(), db.now())
        url = event_url(g.id, ev.id)
        out = channel(g, s.get("announce_channel", "announcements"))
        ping = role(g, s["announce_ping"]) if s.get("announce_ping") else None
        if out:
            try:
                await out.send(ping.mention if ping else None, view=cards.CardView(cards.container(
                    cards.title(p["name"], f"<t:{int(p['start'])}:F> · <t:{int(p['start'])}:R> · hosted by {host.mention}"),
                    cards.text(p["about"] or "Join us in game!"),
                    cards.small("Press Interested on the event and Discord reminds you when it starts."),
                    cards.row(discord.ui.Button(label="See the event", url=url)), accent=ui.BRAND)),
                    allowed_mentions=discord.AllowedMentions(roles=bool(ping)))
            except discord.HTTPException as e:
                log.warning("event announce: %s", e)
        await interaction.edit_original_response(view=cards.CardView(cards.container(
            cards.text(f"**{p['name']}** is planned.\nI'll remind #general an hour before, start and end it on time, "
                       "and ask for screenshots afterwards."),
            cards.row(discord.ui.Button(label="See the event", url=url)), accent=ui.GOOD)))
        await log_staff(g, f"Event planned by {host.mention}: **{p['name']}** <t:{int(p['start'])}:f>", kind="Server",
                        event="event_planned")


class PlanEventButton(discord.ui.Button):
    def __init__(self, persistent: bool = True):
        super().__init__(label="Plan an event", style=discord.ButtonStyle.primary,
                         custom_id="asbot:event:plan" if persistent else None)

    async def callback(self, interaction: discord.Interaction):
        if not can_host(interaction.user):
            return await interaction.response.send_message("Events are planned by the PR team and staff.", ephemeral=True)
        await interaction.response.send_modal(PlanModal())


class Events(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.run.start()

    def cog_unload(self):
        self.run.cancel()

    @property
    def guild(self) -> discord.Guild | None:
        return self.bot.get_guild(self.bot.guild_id)

    async def _event(self, g: discord.Guild, eid: int) -> discord.ScheduledEvent | None:
        try:
            return await g.fetch_scheduled_event(eid, with_counts=True)
        except discord.NotFound:
            return None
        except discord.HTTPException as e:
            log.warning("event %s: %s", eid, e)
            return None

    @tasks.loop(minutes=2)
    @guarded
    async def run(self):
        g = self.guild
        if g is None:
            return
        s, now = settings(), db.now()
        for row in db.q("SELECT * FROM events WHERE state IN ('planned', 'live')"):
            ev = await self._event(g, row["event_id"])
            if ev is None or ev.status == discord.EventStatus.cancelled:     # deleted or cancelled in Discord
                db.run("UPDATE events SET state='cancelled' WHERE id=?", row["id"])
                continue
            if ev.status == discord.EventStatus.completed:                    # someone ended it by hand
                db.run("UPDATE events SET state='done' WHERE id=?", row["id"])
                await self.recap(g, row, ev.user_count or 0)
                continue
            if row["state"] == "planned":
                if not row["reminded"] and row["start"] - now <= int(s.get("remind_before_min", 60)) * 60:
                    db.run("UPDATE events SET reminded=1 WHERE id=?", row["id"])
                    chat = channel(g, "chat")
                    if chat:
                        try:
                            await chat.send(view=cards.CardView(cards.container(
                                cards.text(f"**Starting <t:{int(row['start'])}:R>: {row['title']}**, hosted by "
                                           f"<@{row['host_id']}>."),
                                cards.row(discord.ui.Button(label="I'm interested", url=event_url(g.id, ev.id))),
                                accent=ui.BRAND)), allowed_mentions=discord.AllowedMentions.none())
                        except discord.HTTPException as e:
                            log.warning("event reminder: %s", e)
                if now >= row["start"]:
                    try:
                        if ev.status == discord.EventStatus.scheduled:
                            await ev.start(reason="Start time")
                    except discord.HTTPException as e:
                        log.warning("event start %s: %s", ev.id, e)
                    db.run("UPDATE events SET state='live' WHERE id=?", row["id"])
            elif now >= row["end"]:
                try:
                    if ev.status == discord.EventStatus.active:
                        await ev.end(reason="End time")
                except discord.HTTPException as e:
                    log.warning("event end %s: %s", ev.id, e)
                db.run("UPDATE events SET state='done' WHERE id=?", row["id"])
                await self.recap(g, row, ev.user_count or 0)

    @run.before_loop
    async def _ready(self):
        await self.bot.wait_until_ready()

    async def recap(self, g: discord.Guild, row, interested: int):
        out = channel(g, settings().get("recap_channel", "media"))
        if out:
            try:
                await out.send(view=cards.CardView(cards.container(
                    cards.text(f"**How was {row['title']}?** Share your screenshots and clips from the event here."),
                    cards.small(f"Thanks to <@{row['host_id']}> for hosting."), accent=ui.BRAND)),
                    allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as e:
                log.warning("event recap: %s", e)
        host = g.get_member(row["host_id"])
        need = int(settings().get("min_interested_for_points", 3))
        got = points.award(host, "event_hosted", f"event:{row['id']}", note=row["title"][:80]) \
            if host and interested >= need else 0
        if host:
            try:
                await host.send(view=cards.CardView(cards.container(
                    cards.title("Thanks for hosting!", f"**{row['title']}** is over. {interested} people were interested."
                                + (f" +{got} points." if got else
                                   f" Events with {need}+ interested people give points.")),
                    accent=ui.GOOD)))
            except discord.HTTPException:
                pass


async def setup(bot):
    await bot.add_cog(Events(bot))
