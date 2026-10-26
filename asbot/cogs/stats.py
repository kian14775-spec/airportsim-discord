"""Staff #stats: a live card pinned on top (every 5 min) and a daily report at midnight UTC.

The report compares start-of-day snapshots, so "visits today" or "group members today" are
real daily gains. Discord joins/leaves and peak players are counted as they happen.
"""
from __future__ import annotations

import datetime as dt

import discord
from discord.ext import commands, tasks

from .. import cards, db, ui
from ..core import cfg, channel, game_stats, game_votes, group_info, log
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


def report_card(d: dt.date, start: dict, end: dict, partial: bool = False) -> discord.Embed:
    diff = lambda k: (end.get(k) - start.get(k)) if end.get(k) is not None and start.get(k) is not None else None
    key = f"{d:%Y-%m-%d}"
    joins, leaves = db.kv_get(f"joins:{key}", 0), db.kv_get(f"leaves:{key}", 0)
    t = ticket_numbers(d)
    title = f"{'Today so far' if partial else 'Daily report'} · {d:%a %d %b}"
    discord_block = ui.kv(("Joined", f"{joins}"), ("Left", f"{leaves}"), ("Net", signed(joins - leaves)),
                          ("Members", f"{end.get('members', 0):,}"), ("Verified", str(t["verified"])))
    roblox_block = ui.kv(("Visits", signed(diff("visits"))), ("Group members", signed(diff("group"))),
                         ("Favourites", signed(diff("favorites"))),
                         ("Peak players", f"{db.kv_get(f'peak:{key}', 0):,}"),
                         ("Like ratio", f"{end['likes']}%" if end.get("likes") is not None else "-"))
    support_block = ui.kv(("Opened", str(t["opened"])), ("Handled", str(t["closed"])),
                          ("Still open", str(t["open_now"])),
                          ("First reply", f"{t['first_reply_min']} min" if t["first_reply_min"] is not None else "-"),
                          ("Rating", t["rating"]), ("Applications", str(t["apps"])))
    parts = [cards.title(title), cards.divider(),
             cards.section("Discord", discord_block), cards.divider(),
             cards.section("Roblox", roblox_block), cards.divider(),
             cards.section("Support", support_block)]
    if not start:
        parts.append(cards.small("First day: Roblox gains start counting from tomorrow"))
    return cards.CardView(cards.container(*parts, accent=ui.BRAND), timeout=600 if partial else None)


class Stats(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.live = None
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
    async def tick(self):
        g = self.guild
        out = channel(g, "stats") if g else None
        if not out:
            return
        d = today()
        key, ykey = f"{d:%Y-%m-%d}", f"{d - dt.timedelta(days=1):%Y-%m-%d}"
        snap = await snapshot(g)
        if db.kv_get(f"snap:{key}") is None:          # first tick of a new UTC day
            db.kv_set(f"snap:{key}", snap)
            if not db.kv_get(f"reported:{ykey}") and db.kv_get(f"snap:{ykey}") is not None:
                try:
                    await out.send(view=report_card(d - dt.timedelta(days=1), db.kv_get(f"snap:{ykey}", {}), snap))
                except discord.HTTPException as e:
                    log.warning("daily report: %s", e)
                db.kv_set(f"reported:{ykey}", True)
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
        accent = ui.BAD if urgent else (ui.WAIT if need else ui.GOOD)
        inbox = channel(self.guild, "inbox")
        tickets_line = f"{open_n} open" + (f" · **{need} need a reply**" if need else " · all answered")
        if urgent:
            tickets_line += f" · 🔴 {urgent} urgent"
        body = ui.kv(("Tickets", tickets_line), ("Applications waiting", str(apps)),
                     ("In game", f"{snap.get('playing') or 0:,} playing · peak today {db.kv_get(f'peak:{key}', 0):,}"),
                     ("Today", f"{db.kv_get(f'joins:{key}', 0)} joined · {db.kv_get(f'leaves:{key}', 0)} left"))
        totals = ui.kv(("Members", f"{snap.get('members', 0):,}"), ("Roblox group", f"{snap.get('group') or 0:,}"),
                       ("Visits", f"{snap.get('visits') or 0:,}"))
        view = cards.CardView(cards.container(
            cards.title("Right now"), cards.text(body), cards.divider(), cards.section("Totals", totals),
            cards.row(discord.ui.Button(label="Open the inbox", url=inbox.jump_url)) if inbox else None,
            cards.small(f"Updated <t:{int(db.now())}:R> · daily report posts at midnight UTC"),
            accent=accent, banner_name="stats"))
        msg = await cards.post_card("staff_summary", out, view, banners=("stats",))
        if msg and not msg.pinned:
            try:
                await msg.pin()
            except discord.HTTPException:
                pass

    @tick.before_loop
    async def _ready(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Stats(bot))
