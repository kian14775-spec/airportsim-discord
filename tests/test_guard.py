"""Offline tests for the guard (spam/scam decisions) and the audit log formatting:
python tests/test_guard.py"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ASBOT_DATA", tempfile.mkdtemp())   # keep the test database out of the real one

import discord  # noqa: E402
from discord.audit_logs import AuditLogDiff  # noqa: E402

from asbot.cogs import auditlog, guard  # noqa: E402
from asbot.cogs.guard import fake_host, near, scam_reason  # noqa: E402

A = discord.AuditLogAction


def test_fake_hosts():
    for h in ["dlscord-gift.com", "rob1ox.com.ge", "roblox.com.ge", "steamcommunlty.com", "robux-free.xyz",
              "d1scord.gg", "disc0rd-nitro.ru", "robiox.com", "dicsord.com", "web.roblox.com.login-check.ru"]:
        assert fake_host(h), h
    for h in ["discord.com", "discord.gg", "create.roblox.com", "youtube.com", "nitrotype.com", "freecodecamp.org",
              "roblox.fandom.com", "discordstatus.com", "rolimons.com", "ro.blox.com", "record.com", "global.com"]:
        assert not fake_host(h), h


def test_near():
    assert near("dicsord", "discord") and near("disord", "discord") and near("roblux", "roblox")
    assert not near("discord", "discord") and not near("record", "discord")


def test_scam_reasons():
    assert scam_reason("@everyone free nitro https://dlscord-gift.com/x") == ("@everyone with a link", True)
    assert scam_reason("@everyone update is out https://www.roblox.com/games/1") is None
    assert scam_reason("free robux https://www.roblox.com/games/1") == ("scam message", False)
    assert scam_reason("free robux here https://robux.gg/claim") == ("scam message", True)
    assert scam_reason("look https://rob1ox.com.ge/users/1/profile")[1] is False
    assert scam_reason("nice video https://youtube.com/watch?v=1") is None
    assert scam_reason("no links, free robux lol") is None
    assert scam_reason("@everyone https://x.com/y", can_ping_everyone=True) is None


class FakeBot:
    guild_id = 1
    user = NS(id=999)

    def get_guild(self, _):
        return None

    async def wait_until_ready(self):
        await asyncio.Event().wait()


def msg(author, ch, text, files=()):
    return NS(content=text, attachments=[NS(filename=f, size=10) for f in files], author=author, mentions=[],
              role_mentions=[], reference=None, guild=NS(id=1),
              channel=NS(id=ch, mention=f"<#{ch}>", permissions_for=lambda a: NS(mention_everyone=False)))


async def _decisions():
    cog = guard.Guard(FakeBot())
    calls = []

    async def punish(member, reason, msgs):
        calls.append(("punish", reason, len(msgs)))

    async def hacked(m, reason, msgs):
        calls.append(("hacked", reason, len(msgs)))

    cog.punish, cog.hacked = punish, hacked
    a, b, c, d = (NS(id=i) for i in (1, 2, 3, 4))
    for i in range(6):                                   # flood: 6 different messages at once
        await cog.check(msg(a, 10, f"message number {i}"))
    assert calls[-1][0] == "punish" and "6 messages" in calls[-1][1], calls
    for _ in range(3):                                   # repeats in one channel
        await cog.check(msg(b, 10, "buy my stuff please"))
    assert calls[-1] == ("punish", "the same message 3 times", 3), calls
    for ch in (10, 11, 12):                              # the same image in 3 channels = hacked
        await cog.check(msg(c, ch, "", files=["gift.png"]))
    assert calls[-1] == ("hacked", "the same message in 3 channels", 3), calls
    for ch in (10, 11, 12):                              # same text only = cross-posting, not hacked
        await cog.check(msg(d, ch, "how do i get a livery?"))
    assert calls[-1] == ("punish", "the same message in 3 channels", 3), calls
    n = len(calls)
    await cog.check(msg(NS(id=5), 10, "hello there"))     # normal chat: nothing
    await cog.check(msg(NS(id=5), 10, "how are you all"))
    assert len(calls) == n
    await cog.check(msg(NS(id=6), 10, "@everyone free nitro https://dlscord-gift.com"))
    assert calls[-1][0] == "hacked"
    cog.tick.cancel()


def test_guard_decisions():
    asyncio.run(_decisions())


def diff(**kw):
    d = AuditLogDiff()
    for k, v in kw.items():
        setattr(d, k, v)
    return d


def entry(action, before=None, after=None, target=None, extra=None, category=None):
    return NS(action=action, changes=NS(before=before or diff(), after=after or diff()), target=target, extra=extra,
              category=category, guild=NS(id=1), reason=None)


def test_perm_and_overwrite_diffs():
    P = discord.Permissions
    assert auditlog.perm_diff(P(send_messages=True), P(send_messages=True, ban_members=True)) == "+ **ban members**"
    assert auditlog.perm_diff(P(kick_members=True), P()) == "− **kick members**"
    d = auditlog.overwrite_diff(None, None, P(view_channel=True), P(send_messages=True))
    assert "allowed view channel" in d and "denied send messages" in d
    assert auditlog.overwrite_diff(P(view_channel=True), None, None, None) == "reset view channel"


def test_dangerous_changes():
    P = discord.Permissions
    assert auditlog.dangerous(entry(A.role_update, diff(permissions=P()), diff(permissions=P(administrator=True))))
    assert not auditlog.dangerous(entry(A.role_update, diff(permissions=P()), diff(permissions=P(send_messages=True))))
    assert auditlog.dangerous(entry(A.overwrite_create, after=diff(allow=P(mention_everyone=True), deny=P())))
    assert auditlog.dangerous(entry(A.guild_update, diff(verification_level=1), diff(verification_level=0)))
    assert not auditlog.dangerous(entry(A.guild_update, diff(name="a"), diff(name="b")))


def test_describe_routes():
    P = discord.Permissions
    kind, title, body, _ = auditlog.describe(entry(
        A.role_update, diff(permissions=P(), name="Mods"), diff(permissions=P(ban_members=True), name="Moderators"),
        target=discord.Object(5, type=discord.Role), category=discord.AuditLogActionCategory.update))
    assert kind == "Security" and title.startswith("⚠️") and "ban members" in body and "`Moderators`" in body, body
    kind, title, body, _ = auditlog.describe(entry(
        A.channel_delete, diff(name="old-logs"), target=discord.Object(7), category=discord.AuditLogActionCategory.delete))
    assert kind == "Server" and title == "Channel deleted" and "old-logs" in body
    kind, title, _, _ = auditlog.describe(entry(A.ban, target=discord.Object(8, type=discord.User)))
    assert (kind, title) == ("Moderation", "Banned")
    import datetime as dt
    until = discord.utils.utcnow() + dt.timedelta(hours=1)
    kind, title, _, _ = auditlog.describe(entry(A.member_update, diff(timed_out_until=None), diff(timed_out_until=until),
                                                target=discord.Object(9, type=discord.Member)))
    assert (kind, title) == ("Moderation", "Timed out")
    kind, title, body, _ = auditlog.describe(entry(A.member_update, diff(nick=None), diff(nick="Pilot"),
                                                   target=discord.Object(9, type=discord.Member)))
    assert (kind, title) == ("Members", "Nickname changed") and "Pilot" in body


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
