"""Offline tests for the 2026-11-16 audit fixes (loops, guard races, lockdown, db, disk budget, radar,
ticket rollback, Roblox errors): python tests/test_fixes.py"""
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

import aiohttp  # noqa: E402
import discord  # noqa: E402
from discord import app_commands  # noqa: E402
from discord.ext import tasks  # noqa: E402

from asbot import core, db, opencloud, transcripts  # noqa: E402
from asbot.cogs import guard, health, modlog, radar, tickets  # noqa: E402

MB = 1024 * 1024


async def nothing(*a, **k):
    return None


class Calls:
    """An async callable that counts its calls (and can wait a tick, so calls overlap)."""

    def __init__(self, result=None, pause=True):
        self.calls, self.result, self.pause = [], result, pause

    async def __call__(self, *a, **k):
        self.calls.append((a, k))
        if self.pause:
            await asyncio.sleep(0.01)
        return self.result


class FakeBot:
    guild_id = 1
    user = NS(id=999)

    def __init__(self):
        self.cogs, self.tree = {}, NS()

    def get_guild(self, _):
        return None

    def is_ready(self):
        return True

    def is_closed(self):
        return False

    latency = 0.1

    async def wait_until_ready(self):
        await asyncio.Event().wait()


# ---------- C1: loops survive errors, the watchdog restarts dead ones ----------
class Worker:
    def __init__(self):
        self.runs = 0

    @tasks.loop(seconds=0.01)
    @core.guarded
    async def job(self):
        self.runs += 1
        if self.runs == 1:
            raise RuntimeError("Discord said 503")

    @tasks.loop(seconds=0.01)
    async def bare(self):
        raise RuntimeError("Discord said 503")


async def _loops():
    w = Worker()
    with mock.patch.object(core.log, "exception") as logged:
        w.job.start()
        for _ in range(100):
            if w.runs >= 3:
                break
            await asyncio.sleep(0.01)
    assert w.runs >= 3 and w.job.is_running(), w.runs   # the first run raised, the loop kept going
    assert logged.call_count == 1
    w.job.cancel()
    with mock.patch("discord.ext.tasks._log"):          # an unguarded loop dies on the first error
        w.bare.start()
        await asyncio.sleep(0.05)
    assert not w.bare.is_running()

    bot = FakeBot()
    bot.get_guild = lambda _: NS(id=1)
    cog = health.Health(bot)
    cog.watchdog.cancel()
    bot.cogs = {"Health": cog, "Worker": w}
    posted = Calls(pause=False)
    with mock.patch.object(health, "log_staff", posted), mock.patch.object(health.log, "error"):
        await cog.revive_loops()
        assert w.bare.is_running() and len(posted.calls) == 1, posted.calls
        assert posted.calls[0][1]["kind"] == "Security"
        w.bare.cancel()
        await asyncio.sleep(0.01)
        await cog.revive_loops()                          # cancelled on purpose: left alone
        assert not w.bare.is_running() and len(posted.calls) == 1


def test_loop_errors_dont_stop_the_loop():
    asyncio.run(_loops())


def test_every_cog_loop_is_guarded():
    import importlib
    import pkgutil
    from discord.ext import commands
    import asbot.cogs
    found = []
    for mod in pkgutil.iter_modules(asbot.cogs.__path__):
        for cls in vars(importlib.import_module(f"asbot.cogs.{mod.name}")).values():
            if isinstance(cls, type) and issubclass(cls, commands.Cog):
                for name, loop in vars(cls).items():
                    if isinstance(loop, tasks.Loop):
                        found.append(name)
                        assert hasattr(loop.coro, "__wrapped__"), f"{cls.__name__}.{name} needs @guarded"
                        assert loop.coro.__name__ == name     # tasks.Loop stores its bound copy under this name
    assert len(found) >= 11, found


# ---------- H3/H4: one card per burst ----------
def member(uid):
    return NS(id=uid, name=f"user{uid}", display_name=f"User {uid}", mention=f"<@{uid}>",
              display_avatar=NS(url="https://cdn.example/a.png"), guild=NS(id=1, name="Server"),
              timeout=Calls(), send=Calls())


def message(author, ch, text="free nitro"):
    return NS(id=ch * 10, author=author, content=text, delete=Calls(),
              channel=NS(id=ch, mention=f"<#{ch}>"))


async def _dedupe():
    cog = guard.Guard(FakeBot())
    cards = Calls(pause=False)
    with mock.patch.object(guard, "log_staff", cards), mock.patch.object(guard, "channel", lambda *a: None), \
            mock.patch.object(guard, "alert_ping", lambda g: ("", discord.AllowedMentions.none())):
        hacker = member(50)
        a, b, c = (message(hacker, ch) for ch in (10, 11, 12))
        await asyncio.gather(*(cog.hacked(m, "@everyone with a link", [m]) for m in (a, b, c)))
        assert len(cards.calls) == 1 and len(hacker.timeout.calls) == 1, (cards.calls, hacker.timeout.calls)
        assert all(m.delete.calls for m in (a, b, c))    # the extra ones are still deleted

        spammer = member(60)
        cog.history[60].append((0, a, "x"))
        x, y = message(spammer, 20, "spam"), message(spammer, 21, "spam")
        await asyncio.gather(cog.punish(spammer, "6 messages in 6 s", [x]), cog.punish(spammer, "6 messages in 6 s", [y]))
        assert len(cards.calls) == 2 and len(cog.hits[60]) == 1, cog.hits[60]   # no 10 min -> 60 min jump
        assert spammer.timeout.calls[0][0][0] == dt.timedelta(minutes=guard.gs()["spam"]["timeout_minutes"])
        assert 60 not in cog.history and x.delete.calls and y.delete.calls

        cog.handling[(60, "punish")] -= guard.HANDLE_SECONDS   # a later burst counts again
        await cog.punish(spammer, "6 messages in 6 s", [message(spammer, 22)])
        assert len(cards.calls) == 3 and len(cog.hits[60]) == 2
    cog.tick.cancel()


def test_guard_handles_a_member_once():
    asyncio.run(_dedupe())


# ---------- H5: one lockdown ----------
async def _lockdown():
    db.run("DELETE FROM kv WHERE key='guard:lockdown'")
    cog = guard.Guard(FakeBot())
    cards = Calls(pause=False)
    g = NS(id=1, edit=Calls())
    with mock.patch.object(guard, "log_staff", cards), \
            mock.patch.object(guard, "alert_ping", lambda g: ("", discord.AllowedMentions.none())):
        first = asyncio.ensure_future(cog.start_lockdown(g, 30, "join wave", joined=[1, 2]))
        await asyncio.sleep(0)                            # first is now waiting on g.edit
        late = NS(id=77, bot=False, guild=g, created_at=discord.utils.utcnow())
        await cog.on_member_join(late)                    # a join during the edit joins this lockdown
        second = await cog.start_lockdown(g, 30, "join wave again")
        assert await first is True and second is False
    assert len(g.edit.calls) == 1 and len(cards.calls) == 1
    assert db.kv_get("guard:lockdown")["joined"] == [1, 2, 77]
    db.run("DELETE FROM kv WHERE key='guard:lockdown'")
    cog.tick.cancel()


def test_lockdown_starts_once():
    asyncio.run(_lockdown())


# ---------- M2: database ----------
def test_db_wal_and_transactions():
    assert db.one("PRAGMA journal_mode")[0] == "wal"
    assert db.one("PRAGMA synchronous")[0] == 1          # NORMAL
    db.conn().execute("CREATE TABLE IF NOT EXISTS t_fix (a INTEGER PRIMARY KEY)")
    with db.conn():
        db.conn().executemany("INSERT OR REPLACE INTO t_fix VALUES(?)", [(i,) for i in range(500)])
    assert not db.conn().in_transaction and db.one("SELECT COUNT(*) n FROM t_fix")["n"] == 500
    try:
        with db.conn():
            db.run("INSERT INTO t_fix VALUES(9999)")
            raise RuntimeError
    except RuntimeError:
        pass
    assert db.one("SELECT 1 FROM t_fix WHERE a=9999") is None   # all or nothing
    assert db.changes("UPDATE t_fix SET a=a WHERE a<10") == 10 and db.changes("UPDATE t_fix SET a=a WHERE a<0") == 0


# ---------- H2: disk budget ----------
def free(mb):
    return lambda path: NS(total=500 * MB, used=(500 - mb) * MB, free=mb * MB)


async def _archive_with(free_mb):
    att = NS(size=1000, filename="shot.png", save=Calls(pause=False))
    msg = NS(id=111, author=NS(id=5, display_name="Bob"), content="it broke", attachments=[att],
             created_at=discord.utils.utcnow())

    async def history(**kw):
        yield msg

    with mock.patch("shutil.disk_usage", free(free_mb)), mock.patch.object(core.log, "warning"):
        n = await transcripts.archive(9000 + free_mb, NS(history=history), bot_id=999)
    return n, len(att.save.calls)


def test_disk_budget():
    with mock.patch("shutil.disk_usage", free(40)), mock.patch.object(core.log, "warning") as warned:
        assert not core.disk_ok()
        assert warned.call_count == 1                            # skipping is logged
    with mock.patch("shutil.disk_usage", free(300)), mock.patch.object(core.log, "warning"):
        assert core.disk_ok(10 * MB) and not core.disk_ok(260 * MB)
    assert asyncio.run(_archive_with(10)) == (1, 0)          # text kept, screenshot skipped
    assert asyncio.run(_archive_with(300)) == (1, 1)
    assert transcripts.FILES_TOTAL == 200_000_000
    assert modlog.settings()["keep_images_mb"] <= modlog.CACHE_MB == 80   # config's 150 can't go past the budget
    asyncio.run(_log_cache())


async def _log_cache():
    cog = modlog.ModLog(FakeBot())
    await asyncio.sleep(0.01)                                    # the first prune counts the cache
    cog.prune.cancel()
    att = NS(content_type="image/png", size=1000, filename="a.png", save=Calls(pause=False))
    with mock.patch("shutil.disk_usage", free(10)), mock.patch.object(core.log, "warning"):
        await cog.keep(NS(id=5, attachments=[att]))
    assert not att.save.calls                                    # volume nearly full: no copy
    with mock.patch("shutil.disk_usage", free(300)):
        cog.cache_bytes = modlog.CACHE_MB * MB                   # cache full: room is made at once
        with mock.patch.object(cog, "prune_cache", wraps=cog.prune_cache) as pruned:
            await cog.keep(NS(id=6, attachments=[att]))
    assert len(att.save.calls) == 1 and pruned.call_count == 1 and cog.cache_bytes == 1000
    modlog.names.client = None                                   # ModLog set the fake bot as the name lookup


# ---------- M6/M7: radar ----------
def test_radar_old_incidents_and_double_fixed():
    old = db.run("INSERT INTO incidents(key, label, opened, tickets) VALUES('topic:bug', 'bugs', ?, '[]')",
                 db.now() - 3 * 86400)
    with mock.patch.object(radar, "refresh", Calls(pause=False)), mock.patch.object(radar, "post_new", Calls()):
        asyncio.run(radar.note_ticket(NS(id=1), 4242, "bug", 1, "the plane falls through the runway"))
    assert radar.incident(old)["tickets"] == "[]"         # 3 days old and never confirmed: doesn't absorb it

    iid = db.run("INSERT INTO incidents(key, label, status, opened, tickets, issue) VALUES('word:fall', 'fall', "
                 "'real', ?, '[]', 'Falling through')", db.now())

    def click():
        return NS(user=NS(id=1), guild=NS(id=1), client=NS(), response=NS(defer=Calls(), send_message=Calls()),
                  followup=NS(send=Calls(pause=False)))

    async def double():
        close_out = Calls(result=0)
        with mock.patch.object(radar, "is_staff", lambda u: True), mock.patch.object(radar, "refresh", Calls()), \
                mock.patch.object(radar, "close_out", close_out):
            b = radar.RadarButton("fixed", iid)
            i1, i2 = click(), click()
            await asyncio.gather(b.callback(i1), b.callback(i2))
        return close_out, i1, i2

    close_out, i1, i2 = asyncio.run(double())
    assert len(close_out.calls) == 1 and radar.incident(iid)["status"] == "fixed"
    assert len(i1.response.send_message.calls) + len(i2.response.send_message.calls) == 1   # "already handled"


# ---------- H1: a ticket without an inbox post is rolled back ----------
class FakeForum:
    available_tags = []

    def __init__(self):
        self.tries = 0

    async def create_thread(self, **kw):
        self.tries += 1
        raise RuntimeError("Discord said 500")


def test_ticket_open_rolls_back():
    forum = FakeForum()
    user = NS(id=31337, name="pilot", display_name="Pilot", created_at=discord.utils.utcnow(), send=Calls(pause=False))
    me = NS(guild=NS(id=1, get_member=lambda uid: None), blocked_reason=lambda u: None)
    inter = NS(user=user, followup=NS(send=Calls(pause=False)))
    with mock.patch.object(discord, "ForumChannel", FakeForum), mock.patch.object(tickets, "channel", lambda g, k: forum), \
            mock.patch.object(tickets.log, "exception"):
        asyncio.run(tickets.Tickets.open(me, inter, "other", "my plane is stuck", "", ""))
    assert forum.tries == 1
    assert tickets.open_ticket_for(user.id) is None             # not blocked: they can open a new one
    assert db.one("SELECT 1 FROM kv WHERE key LIKE 'tcard:%' AND value LIKE '%my plane is stuck%'") is None
    assert len(user.send.calls) == 2                             # the hello, then "didn't reach the team"
    assert "try again" in inter.followup.send.calls[-1][0][0]


# ---------- M15/M21: Roblox errors and failed staff checks ----------
class Down:
    def __getattr__(self, name):
        def call(*a, **k):
            raise aiohttp.ClientConnectionError("Roblox is down")
        return call


def test_roblox_errors_become_none():
    with mock.patch.object(core, "http", lambda: Down()), mock.patch.object(core.log, "warning"):
        assert asyncio.run(core.roblox_user_by_name("Builderman")) is None
        assert asyncio.run(core.game_stats()) is None
    with mock.patch.object(opencloud, "http", lambda: Down()), mock.patch.object(opencloud.log, "warning"), \
            mock.patch.dict(os.environ, {"ROBLOX_OPENCLOUD_KEY": "test-only"}):
        assert asyncio.run(opencloud.get_ban(1)) is None and asyncio.run(opencloud.unban(1)) is False


def test_failed_staff_check_replies_once():
    cog = health.Health.__new__(health.Health)
    sent = Calls(pause=False)
    inter = NS(command=None, response=NS(is_done=lambda: True, send_message=sent), followup=NS(send=sent))
    asyncio.run(cog.on_app_command_error(inter, app_commands.CheckFailure("Staff only.")))
    assert not sent.calls                                        # staff_only already said "Staff only."
    inter = NS(command=None, response=NS(is_done=lambda: False, send_message=sent), followup=NS(send=sent))
    asyncio.run(cog.on_app_command_error(inter, app_commands.MissingPermissions(["ban_members"])))
    assert sent.calls[-1][0] == ("You can't use that.",)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
