"""Offline tests for the 2026-11-16 systems: Staff Duty points, the board, tickets scoring, the bug loop,
events, creators, pulse and the staff guide. Nothing connects to Discord: python tests/test_systems.py"""
import asyncio
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ASBOT_DATA", tempfile.mkdtemp())   # keep the test database out of the real one

import discord  # noqa: E402
from discord.ext import commands  # noqa: E402

from asbot import db, points  # noqa: E402
from asbot.cogs import bugs, creators, duty, events, pulse, tickets  # noqa: E402

MON = dt.datetime(2026, 11, 16, tzinfo=dt.timezone.utc).timestamp()   # Monday of 2026-W47


def fresh():
    points._db()
    for t in ("duty_points", "duty_weeks", "duty_loa", "duty_on"):
        db.run(f"DELETE FROM {t}")


def who(uid, name="Someone"):
    return NS(id=uid, display_name=name, name=name.lower(), bot=False)


# ---------------- the ledger ----------------
def test_week_helpers():
    assert points.week_of(MON + 3600) == "2026-W47"
    assert points.week_of(MON - 1) == "2026-W46"            # Sunday 23:59:59 is still last week
    assert points.week_start("2026-W47") == MON
    assert points.prev_week("2026-W47") == "2026-W46"
    assert points.prev_week("2027-W01") == "2026-W53"       # 2026 has 53 ISO weeks
    assert "2026-W53" < "2027-W01" and "2026-W09" < "2026-W10"   # string order = time order (duty:closed check)


def test_award_counts_once_and_needs_a_team():
    fresh()
    u = who(1)
    assert points.award(u, "ticket_closed", "ticket:1", dept="mod", at=MON + 10) == 2
    assert points.award(u, "ticket_closed", "ticket:1", dept="mod", at=MON + 20) == 0    # same ref
    assert points.award(u, "ticket_closed", "ticket:2", at=MON + 30) == 0               # not in a team
    assert points.totals("2026-W47") == {1: 2}
    assert points.revoke("ticket:1") and points.totals("2026-W47") == {}


def test_daily_and_weekly_caps():
    fresh()
    u = who(2)
    got = [points.award(u, "mod_action", f"a:{i}", dept="mod", at=MON + 100 + i) for i in range(7)]
    assert sum(got) == 5 and got[-1] == 0                                # max 5 a day
    assert points.award(u, "mod_action", "a:next-day", dept="mod", at=MON + 86400 + 5) == 1
    days = [points.award(u, "chat_day", f"c:{d}", dept="mod", at=MON + d * 86400 + 50) for d in range(7)]
    assert sum(days) == 5                                                 # max 5 a week


def test_status_streaks_and_close_week():
    fresh()
    a, b, c = who(10, "Ann"), who(11, "Ben"), who(12, "Cat")
    depts = {"mod": {"name": "Moderation", "target": 12, "roles": []}}
    with mock.patch.object(points, "departments", lambda: depts), \
            mock.patch.object(points, "members_of", lambda g, d: [a, b, c]):
        for wk_i, week in enumerate(("2026-W47", "2026-W48")):
            base = points.week_start(week) + 3600
            for i in range(6):
                points.award(a, "ticket_closed", f"t:{week}:{i}", dept="mod", at=base + i)   # 12 = goal
            points.award(b, "ticket_closed", f"t:{week}:b", dept="mod", at=base)             # 2 = low
        db.run("INSERT INTO duty_loa VALUES(?, ?, ?, ?)", 12, MON, MON + 30 * 86400, "exams")
        r47 = {r["user_id"]: r["status"] for r in points.close_week(None, "2026-W47")}
        r48 = {r["user_id"]: r["status"] for r in points.close_week(None, "2026-W48")}
        assert r47 == r48 == {10: "green", 11: "red", 12: "leave"}
        assert points.streak(11, "2026-W48", "red") == 2
        assert points.streak(10, "2026-W48", "green") == 2
        assert points.streak(12, "2026-W48", "red") == 0                   # leave weeks never count
        points.close_week(None, "2026-W48")                                  # safe to run twice
        assert db.one("SELECT COUNT(*) n FROM duty_weeks WHERE week='2026-W48'")["n"] == 3
    assert (points.status(12, 12), points.status(6, 12), points.status(5, 12)) == ("green", "amber", "red")


def test_on_duty_expires():
    fresh()
    points.go_on_duty(20, hours=1)
    db.run("INSERT INTO duty_on VALUES(?, ?, ?)", 21, 0, 1)              # ran out long ago
    assert points.on_duty_ids() == [20]
    assert points.go_off_duty(20) and points.on_duty_ids() == []


def test_board_rows_line_up():
    fresh()
    people = [who(30, "Ann"), who(31, "A very long display name"), who(32, "Bo 🚀✈️"), who(33, "Cat")]
    wk = points.week_of()
    points.award(people[0], "ticket_closed", "t:x", dept="mod")
    points.set_leave(33, 5, "holiday")
    with mock.patch.object(points, "members_of", lambda g, d: people):
        text = duty.board_rows(None, "mod", wk)
    rows = text.strip("`\n").split("\n")
    assert len(rows) == 4 and rows[0].startswith("Ann")                  # most points first
    assert len({len(r) for r in rows}) == 1, rows                         # monospace columns line up
    assert "on a break" in rows[-1] or any("on a break" in r for r in rows)
    assert all(ord(ch) < 0x2000 for r in rows for ch in r if ch not in "█░")   # no wide emoji


# ---------------- tickets ----------------
def test_ticket_scores_only_when_the_member_replied():
    fresh()
    first = MON + 1000
    db.run("DELETE FROM relay_map")
    t = {"id": 77, "claimed_by": 40, "first_reply": first}
    member = NS(id=40)
    fake = NS(guild=NS(get_member=lambda uid: member))
    with mock.patch.object(points, "award") as award:
        tickets.Tickets.score_close(fake, t)                                 # nothing from the member after
        assert not award.called
        before = discord.utils.time_snowflake(dt.datetime.fromtimestamp(first - 60, dt.timezone.utc))
        db.run("INSERT INTO relay_map VALUES(?, 1, 2, 77, 'member')", before)
        tickets.Tickets.score_close(fake, t)                                 # their opening message only
        assert not award.called
        after = discord.utils.time_snowflake(dt.datetime.fromtimestamp(first + 60, dt.timezone.utc))
        db.run("INSERT INTO relay_map VALUES(?, 1, 3, 77, 'member')", after)
        tickets.Tickets.score_close(fake, t)
        award.assert_called_once_with(member, "ticket_closed", "ticket:77")
        award.reset_mock()
        tickets.Tickets.score_close(fake, {**t, "claimed_by": None})        # nobody handled it
        assert not award.called


# ---------------- events ----------------
def test_event_times():
    now = dt.datetime(2026, 11, 16, 15, 0, tzinfo=dt.timezone.utc)
    p = lambda s: events.parse_when(s, now)
    assert p("in 2h") == now + dt.timedelta(hours=2)
    assert p("in 1h 30m") == p("in 90m") == now + dt.timedelta(minutes=90)
    assert p("18:00") == now.replace(hour=18)
    assert p("14:00") == now.replace(hour=14) + dt.timedelta(days=1)        # already past today
    assert p("tomorrow 18:00") == now.replace(hour=18) + dt.timedelta(days=1)
    assert p("2026-11-20 18:00 UTC") == dt.datetime(2026, 11, 20, 18, tzinfo=dt.timezone.utc)
    for bad in ("in", "25:00", "2026-02-30 10:00", "noon", ""):
        assert p(bad) is None, bad


# ---------------- creators ----------------
FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
 <title>Plane Guy</title>
 <entry><yt:videoId>abc123</yt:videoId><title>NEW A350 in Airport Simulator!</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=abc123"/>
  <published>2026-11-15T10:00:00+00:00</published>
  <media:group><media:description>update video</media:description></media:group></entry>
 <entry><yt:videoId>def456</yt:videoId><title>My cat</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=def456"/>
  <media:group><media:description>not a game video</media:description></media:group></entry>
</feed>"""


def test_feed_and_keywords():
    name, vids = creators.parse_feed(FEED)
    assert name == "Plane Guy" and [v["id"] for v in vids] == ["abc123", "def456"]
    assert creators.about_game(vids[0]) and not creators.about_game(vids[1])
    assert creators.about_game({"title": "trip", "about": "played airport sim today"})


def test_resolve_without_network():
    cid = "UC" + "a" * 22
    assert asyncio.run(creators.resolve(f"https://www.youtube.com/channel/{cid}")) == cid
    assert asyncio.run(creators.resolve(cid)) == cid
    assert asyncio.run(creators.resolve("https://evil.example.com/@x")) is None     # only youtube.com is fetched


# ---------------- bugs ----------------
def test_bug_card_buttons_follow_the_state():
    db.run("DELETE FROM bugs")
    db.run("INSERT INTO bugs(thread_id, reporter_id, created) VALUES(5, 9, 0)")

    def buttons():
        return [c.custom_id for c in bugs.card_view(5).walk_children() if getattr(c, "custom_id", None)]
    assert buttons() == [f"asbot:bug:{a}:5" for a in ("repro", "norepro", "dup", "fixed")]
    db.run("UPDATE bugs SET state='fixed' WHERE thread_id=5")
    assert len(buttons()) == 4                                                 # still open until the update ships
    for closed in ("done", "dup"):
        db.run("UPDATE bugs SET state=? WHERE thread_id=5", closed)
        assert buttons() == []


# ---------------- pulse + guide ----------------
def test_boost_line():
    assert pulse.boost_line(0).startswith("2 more boosts unlock level 1")
    assert pulse.boost_line(6).startswith("1 more boost unlocks level 2")
    assert "top level" in pulse.boost_line(14)


def test_guide_pages_read_well():
    pages = duty.guide_pages()
    assert {"start", "duty", "breaks", "tickets", "qa", "pr", "leads"} <= set(pages)
    for key in pages:
        title, body = duty.page_text(key)
        assert title and body and "{" not in body, key
        assert len(body) < 1800, (key, len(body))
    assert "Moderation 12" in duty.page_text("duty")[1]


def test_every_cog_loads_and_registers():
    """All cogs on a bot that never logs in: persistent views need custom ids, commands must not clash."""
    import importlib
    spec = importlib.util.spec_from_file_location("botmain", ROOT / "bot.py")
    botmain = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(botmain)

    async def run():
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
        bot.guild_id = 1
        for cog in botmain.COGS:
            await bot.load_extension(cog)
        assert {"Duty", "Bugs", "Pulse", "Events", "Creators"} <= set(bot.cogs)
        names = [c.name for c in bot.tree.get_commands()]
        assert len(names) == len(set(names)) and len(names) <= 100
        for cog in list(bot.cogs):
            await bot.remove_cog(cog)
    asyncio.run(run())


def test_card_posts_once_when_rendered_twice_at_once():
    """2026-11-16 live bug: on_ready and the first poll both rendered the creators card -> 2 cards, 2 pins."""
    from asbot import cards
    db.run("DELETE FROM panels WHERE name='race_card'")
    sent = []

    class Chan:
        id = 77

        async def send(self, **kw):
            await asyncio.sleep(0.05)          # the gap where the second render slipped in
            sent.append(kw)
            return NS(id=900 + len(sent), pinned=False, flags=NS(components_v2=True))

        async def fetch_message(self, mid):
            return NS(id=mid, flags=NS(components_v2=True), edit=mock.AsyncMock())

    async def run():
        ch = Chan()
        await asyncio.gather(*(cards.post_card("race_card", ch, discord.ui.LayoutView()) for _ in range(3)))
    asyncio.run(run())
    assert len(sent) == 1, f"posted {len(sent)} cards"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
