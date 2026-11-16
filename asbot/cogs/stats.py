"""Staff #stats: ONE live card (every 5 min) with yesterday's line and an "Open a report" menu.

Nothing is posted daily or weekly any more: reports (today so far, any of the last 7 days, the
support week) open privately from the menu. Reports compare start-of-day snapshots, so "visits
today" or "group members today" are real daily gains. Joins/leaves and peak players are counted live.
"""
from __future__ import annotations

import datetime as dt

import discord
from discord.ext import commands, tasks

from .. import cards, db, names, ui
from ..core import cfg, channel, game_stats, game_votes, group_info, guarded
from ..panels import drop_panel


def today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def day_bounds(d: dt.date) -> tuple[float, float]:
    start = dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp()
    return start, start + 86400


def signed(n: int | None) -> str:
    if n is None:
        return "-"
    return f"+{n:,}" if n > 0 else f"{n:,}"


async def snapshot(guild: discord.Guild) -> dict:
    g, votes, grp = await game_stats(), await game_votes(), await group_info(cfg()["game"]["group_id"])
    snap = {"members": guild.member_count}
    if g:
        snap.update(visits=g.get("visits"), favorites=g.get("favoritedCount"), playing=g.get("playing"))
    if votes:
        up, down = votes.get("upVotes", 0), votes.get("downVotes", 0)
        snap["likes"] = round(up / (up + down) * 100) if up + down else None
    if grp:
        snap["group"] = grp.get("memberCount")
    return snap


def ticket_numbers(d: dt.date) -> dict:
    a, b = day_bounds(d)
    one = lambda sql, *x: db.one(sql, *x)
    rated = one("SELECT AVG(rating) r, COUNT(rating) n FROM tickets WHERE closed BETWEEN ? AND ? AND rating IS NOT NULL",
                a, b)
    first = one("SELECT AVG(first_reply - created) f FROM tickets WHERE first_reply BETWEEN ? AND ?", a, b)["f"]
    return {
        "opened": one("SELECT COUNT(*) n FROM tickets WHERE created BETWEEN ? AND ?", a, b)["n"],
        "closed": one("SELECT COUNT(*) n FROM tickets WHERE closed BETWEEN ? AND ?", a, b)["n"],
        "open_now": one("SELECT COUNT(*) n FROM tickets WHERE status!='closed'")["n"],
        "first_reply_min": round(first / 60) if first else None,
        "rating": f"{rated['r']:.1f}★ ({rated['n']})" if rated["n"] else "no ratings",
        "verified": one("SELECT COUNT(*) n FROM links WHERE linked_at BETWEEN ? AND ?", a, b)["n"],
        "apps": one("SELECT COUNT(*) n FROM applications WHERE created BETWEEN ? AND ?", a, b)["n"],
    }


def short(n) -> str:
    """12,345,678 -> 12.3M so big totals fit the table columns."""
    if n is None:
        return "-"
    for size, unit in ((1_000_000_000, "B"), (1_000_000, "M"), (10_000, "K")):
        if abs(n) >= size:
            return f"{n / size:.1f}".rstrip("0").rstrip(".") + unit
    return f"{n:,}"


def table(*rows: list[tuple[str, object]]) -> str:
    """Small aligned table in a code block: labels left, numbers right, two per row.
    Narrow enough (41 chars) to fit on a phone without scrolling."""
    lines = ["   ".join(f"{label:<11}{str(value):>8}" for label, value in row).rstrip() for row in rows]
    return "```\n" + "\n".join(lines) + "\n```"


def report_card(d: dt.date, start: dict, end: dict, partial: bool = False) -> cards.CardView:
    diff = lambda k: (end.get(k) - start.get(k)) if end.get(k) is not None and start.get(k) is not None else None
    key = f"{d:%Y-%m-%d}"
    joins, leaves = db.kv_get(f"joins:{key}", 0), db.kv_get(f"leaves:{key}", 0)
    t = ticket_numbers(d)
    title = "Today so far" if partial else "Daily report"
    first = f"{t['first_reply_min']} min" if t["first_reply_min"] is not None else "-"
    rating = t["rating"].split(" ")[0] if t["rating"] != "no ratings" else "-"
    parts = [
        cards.title(title, f"{d:%A %d %B} · UTC day"),
        cards.divider(),
        cards.section("Discord", table([("Joined", joins), ("Left", leaves)],
                                       [("Net", signed(joins - leaves)), ("Verified", t["verified"])],
                                       [("Members", short(end.get("members", 0)))])),
        cards.divider(),
        cards.section("Roblox", table([("Visits", signed(diff("visits"))), ("Group", signed(diff("group")))],
                                      [("Favourites", signed(diff("favorites"))),
                                       ("Likes", f"{end['likes']}%" if end.get("likes") is not None else "-")],
                                      [("Peak", f"{db.kv_get(f'peak:{key}', 0):,}")])),
        cards.divider(),
        cards.section("Support", table([("Opened", t["opened"]), ("Handled", t["closed"])],
                                       [("Still open", t["open_now"]), ("Rating", rating)],
                                       [("1st reply", first), ("Apps", t["apps"])])),
    ]
    if not start:
        parts += [cards.divider(), cards.small("First day: Roblox gains start counting from tomorrow.")]
    return cards.CardView(cards.container(*parts, accent=ui.SLATE), timeout=600 if partial else None)


def weekly_card(end: dt.date) -> cards.CardView:
    """Monday report for the 7 UTC days before `end`: what support handled, what the Support Agent
    solved alone, the top problems, which problems have no instant answer yet, and the slowest replies."""
    a, b = day_bounds(end - dt.timedelta(days=7))[0], day_bounds(end - dt.timedelta(days=1))[1]
    rows = db.q("SELECT * FROM tickets WHERE created BETWEEN ? AND ?", a, b)
    cardd = {r["id"]: db.kv_get(f"tcard:{r['id']}", {}) for r in rows}
    agent = sum(1 for r in rows if cardd[r["id"]].get("solved_by_agent"))
    people = sum(1 for r in rows if r["first_reply"])
    first = [r["first_reply"] - (r["handed"] or r["created"]) for r in rows if r["first_reply"]]
    avg_first = f"{round(sum(first) / len(first) / 60)} min" if first else "-"
    rated = [r["rating"] for r in rows if r["rating"]]
    rating = f"{sum(rated) / len(rated):.1f}" if rated else "-"
    tops: dict[str, int] = {}
    for r in rows:
        lbl = cardd[r["id"]].get("first") or "no instant answer"
        tops[lbl] = tops.get(lbl, 0) + 1
    top = "\n".join(f"**{n}x** {lbl}" for lbl, n in sorted(tops.items(), key=lambda x: -x[1])[:5]) or "No tickets."
    gaps = [r for r in rows if not cardd[r["id"]].get("first")]
    gap_text = ("\n".join(f"`{r['id']:04d}` {(cardd[r['id']].get('problem') or '-')[:70]}" for r in gaps[:5])
                + "\n-# Turn the common ones into answers: `?answer words | fix` or **Save fix** on a ticket"
                if gaps else "Every ticket had an instant answer.")
    slow = sorted((r for r in rows if r["first_reply"]), key=lambda r: -(r["first_reply"] - (r["handed"] or r["created"])))
    slow_text = "\n".join(f"`{r['id']:04d}` {round((r['first_reply'] - (r['handed'] or r['created'])) / 60)} min"
                          + (f" · {names.who(r['claimed_by'])}" if r["claimed_by"] else "") for r in slow[:3]) or "-"
    team = db.q("SELECT claimed_by, COUNT(*) n FROM tickets WHERE status='closed' AND closed BETWEEN ? AND ? "
                "AND claimed_by IS NOT NULL GROUP BY claimed_by ORDER BY n DESC LIMIT 5", a, b)
    team_text = " · ".join(f"{names.who(r['claimed_by'])} {r['n']}" for r in team) or "-"
    parts = [
        cards.title("Weekly support report", f"{end - dt.timedelta(days=7):%d %b} to {end - dt.timedelta(days=1):%d %b}"),
        cards.divider(),
        cards.section("Tickets", table([("Opened", len(rows)), ("Agent fixed", agent)],
                                       [("To a person", people), ("1st reply", avg_first)],
                                       [("Rating", rating)])),
        cards.divider(),
        cards.section("Top problems", top),
        cards.section("No instant answer yet", gap_text),
        cards.divider(),
        cards.section("Slowest first replies", slow_text),
        cards.section("Closed by", team_text),
    ]
    return cards.CardView(cards.container(*parts, accent=ui.SLATE))


class ReportPick(discord.ui.Select):
    """On the live card: any report, shown only to the person who picks it."""

    def __init__(self):
        d = today()
        opts = [discord.SelectOption(label="Today so far", value="today", description="Updates as the day goes"),
                discord.SelectOption(label="Support · last 7 days", value="week",
                                     description="Tickets, top problems, missing answers, slowest replies")]
        for back in range(1, 8):
            day = d - dt.timedelta(days=back)
            if db.kv_get(f"snap:{day:%Y-%m-%d}") is not None or back == 1:
                opts.append(discord.SelectOption(label="Yesterday" if back == 1 else f"{day:%A %d %b}",
                                                 value=f"day:{day:%Y-%m-%d}", description="Daily report"))
        super().__init__(placeholder="Open a report (only you see it)", options=opts, custom_id="asbot:stats:report")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        v = self.values[0]
        if v == "today":
            view = await interaction.client.get_cog("Stats").so_far()
        elif v == "week":
            view = weekly_card(today())
        else:
            day = dt.date.fromisoformat(v[4:])
            start = db.kv_get(f"snap:{day:%Y-%m-%d}", {})
            end = db.kv_get(f"snap:{day + dt.timedelta(days=1):%Y-%m-%d}") or await snapshot(interaction.guild)
            view = report_card(day, start, end)
        await interaction.followup.send(view=view, ephemeral=True)


class Stats(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.live = None
        bot.add_view(cards.CardView(cards.container(cards.row(ReportPick()))))   # the menu keeps working after restarts
        self.tick.start()

    def cog_unload(self):
        self.tick.cancel()

    @property
    def guild(self) -> discord.Guild:
        return self.bot.get_guild(self.bot.guild_id)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        k = f"joins:{today():%Y-%m-%d}"
        db.kv_set(k, db.kv_get(k, 0) + 1)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        k = f"leaves:{today():%Y-%m-%d}"
        db.kv_set(k, db.kv_get(k, 0) + 1)

    async def so_far(self) -> cards.CardView:
        d = today()
        return report_card(d, db.kv_get(f"snap:{d:%Y-%m-%d}", {}), await snapshot(self.guild), partial=True)

    @tasks.loop(minutes=5)
    @guarded
    async def tick(self):
        g = self.guild
        out = channel(g, "stats") if g else None
        if not out:
            return
        d = today()
        snap = await snapshot(g)
        if db.kv_get(f"snap:{d:%Y-%m-%d}") is None:   # first tick of a new UTC day: the day's starting point
            db.kv_set(f"snap:{d:%Y-%m-%d}", snap)
        # No daily/weekly messages any more (Kian: one card that updates, not 100 reports): the live card
        # shows yesterday, and its "Open a report" menu shows any report privately.
        await self.update_live(out, snap)

    async def update_live(self, out, snap: dict):
        row = db.panel("staff_summary")
        if row and row["channel_id"] != out.id:      # the live card used to live in staff-chat
            await drop_panel(self.guild, "staff_summary")
        q = lambda sql, *a: db.one(sql, *a)["n"]
        open_n = q("SELECT COUNT(*) n FROM tickets WHERE status!='closed'")
        need = q("SELECT COUNT(*) n FROM tickets WHERE status='open'")
        urgent = q("SELECT COUNT(*) n FROM tickets WHERE status!='closed' AND priority='urgent'")
        apps = q("SELECT COUNT(*) n FROM applications WHERE status='pending'")
        key = f"{today():%Y-%m-%d}"
        inbox = channel(self.guild, "inbox")
        verified = db.one("SELECT COUNT(*) n FROM links WHERE linked_at>=?", day_bounds(today())[0])["n"]
        yd = today() - dt.timedelta(days=1)
        ykey, yt = f"{yd:%Y-%m-%d}", ticket_numbers(yd)
        yj, yl = db.kv_get(f"joins:{ykey}", 0), db.kv_get(f"leaves:{ykey}", 0)
        yesterday = (f"**Yesterday** · {signed(yj - yl)} members ({yj} joined, {yl} left) · {yt['closed']} tickets "
                     f"handled · peak {db.kv_get(f'peak:{ykey}', 0):,} playing")
        view = cards.CardView(cards.container(
            cards.title("Right now", f"Updated <t:{int(db.now())}:R> · refreshes every 5 minutes"),
            cards.divider(),
            cards.section("Tickets", table([("Open", open_n), ("Need reply", need)],
                                           [("Urgent", urgent), ("Apps", apps)])),
            cards.divider(),
            cards.section("Game", table([("Playing", f"{snap.get('playing') or 0:,}"),
                                         ("Peak today", f"{db.kv_get(f'peak:{key}', 0):,}")],
                                        [("Visits", short(snap.get("visits"))),
                                         ("Likes", f"{snap['likes']}%" if snap.get("likes") is not None else "-")])),
            cards.divider(),
            cards.section("Community", table([("Joined", db.kv_get(f"joins:{key}", 0)),
                                              ("Left", db.kv_get(f"leaves:{key}", 0))],
                                             [("Verified", verified), ("Members", short(snap.get("members")))],
                                             [("Group", short(snap.get("group")))])),
            cards.divider(),
            cards.text(yesterday),
            cards.row(ReportPick()),
            cards.row(discord.ui.Button(label="Open the inbox", url=inbox.jump_url)) if inbox else None,
            banner_name="stats"))
        msg = await cards.post_card("staff_summary", out, view, banners=("stats",))
        await cards.pin_quietly(msg)

    @tick.before_loop
    async def _ready(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Stats(bot))
