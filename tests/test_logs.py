"""Offline tests for the three log channels, the quiet rules and the member timeline:
python tests/test_logs.py"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ASBOT_DATA", tempfile.mkdtemp())   # keep the test database out of the real one

import discord  # noqa: E402

from asbot import core, db, timeline, ui  # noqa: E402
from asbot.cogs import auditlog  # noqa: E402
from asbot.cogs.guard import status_parts  # noqa: E402
from asbot.cogs.modlog import small_edit  # noqa: E402
from asbot.layout import model  # noqa: E402

A = discord.AuditLogAction


def audit(action, user_id=1, target_id=2, bot=False, reason=None):
    return NS(action=action, user_id=user_id, user=NS(bot=bot), target=NS(id=target_id), reason=reason)


def test_three_channels_in_the_layout():
    logs = next(c for c in model.load(ROOT / "layout" / "server.yaml").categories if c.key == "logs_cat")
    assert {c.key: c.name for c in logs.channels} == {"log_security": "alerts", "log_mod": "actions", "log_messages": "activity"}
    assert set(core.LOG_CHANNELS.values()) == {c.key for c in logs.channels}


def test_routing():
    want = {"Security": "log_security", "Moderation": "log_mod", "Members": "log_mod", "Server": "log_mod",
            "Tickets": "log_mod", "Voice": "log_mod", "Messages": "log_messages", "Activity": "log_messages"}
    for kind, key in want.items():
        assert core.LOG_CHANNELS[kind] == key, kind


def test_cut_list_is_off():
    for event in ("profile", "self_nick", "voice_moves", "verified", "ticket_opened", "message_pin", "invite_create",
                  "emoji_delete", "thread_update"):
        assert core.log_off(event), event
    for event in ("join", "leave", "boost", None, "ban", "role_update", "webhook_create", "channel_delete"):
        assert not core.log_off(event), event


def test_switched_off_logs_never_post():
    guild = mock.MagicMock()
    with mock.patch.object(core, "log_channel") as where:
        assert asyncio.run(core.log_staff(guild, "x", kind="Activity", event="profile")) is None
    where.assert_not_called()


def test_only_security_counts_as_an_alert():
    db.kv_set("alerts:recent", [])
    target = mock.AsyncMock()
    with mock.patch.object(core, "log_channel", return_value=target):
        asyncio.run(core.log_staff(mock.MagicMock(), "", kind="Moderation", embed=core.log_card("Banned")))
        asyncio.run(core.log_staff(mock.MagicMock(), "", kind="Security", embed=core.log_card("Lockdown on")))
    assert [a[1] for a in db.kv_get("alerts:recent")] == ["Lockdown on"]
    assert target.send.await_count == 2


def test_small_edits():
    assert small_edit("helo there everyone", "hello there everyone")
    assert small_edit("Thanks for the help", "thanks for the help!")
    assert not small_edit("i did it", "i never touched that")
    assert not small_edit("join my game", "join my game https://evil.example/x")
    assert not small_edit("hello there friend", "hello there friend <@123>")
    assert not small_edit("hello there friend", "hello there friend @everyone")
    assert small_edit("see https://a.example now", "see https://a.example now!")


def test_quiet_rules():
    assert auditlog.quiet(audit(A.message_pin), "Messages", "Message pinned")
    assert auditlog.quiet(audit(A.invite_create), "Server", "Invite created")
    assert auditlog.quiet(audit(A.member_role_update, bot=True), "Members", "Roles changed")          # Bloxlink
    assert auditlog.quiet(audit(A.member_update, user_id=2, target_id=2), "Members", "Nickname changed")   # their own
    assert not auditlog.quiet(audit(A.member_update), "Members", "Nickname changed")                  # by staff
    assert not auditlog.quiet(audit(A.member_role_update), "Members", "Roles changed")
    assert not auditlog.quiet(audit(A.ban, bot=True), "Moderation", "Banned")                         # bots banning still show
    assert not auditlog.quiet(audit(A.member_role_update, bot=True), "Security", "⚠️ Roles changed")   # dangerous always posts
    assert not auditlog.quiet(audit(A.role_update), "Server", "Role changed")


def test_timeline_text():
    assert auditlog.timeline_text(audit(A.ban, reason="spam"), "Banned", "<@2>") == "Banned by <@1> · spam"
    own = auditlog.timeline_text(audit(A.member_update, user_id=2), "Nickname changed", "<@2>\n**a** → **b**")
    assert own == "Nickname changed: **a** → **b**"


def test_timeline_store_and_prune():
    uid = 555
    timeline.add(uid, "member", "Joined")
    timeline.add(uid, "voice", "Voice: joined **lounge**")
    timeline.add(uid, "profile", "x" * 500)
    assert len(timeline.recent(uid)[0]["text"]) == 200
    assert "Joined" in timeline.lines(uid) and timeline.lines(uid).startswith("<t:")
    assert timeline.lines(999) == ""
    old = time.time() - 8 * 86400
    db.run("UPDATE memberlog SET created=? WHERE user_id=?", old, uid)
    timeline.prune()
    assert {r["kind"] for r in timeline.recent(uid)} == {"member", "profile"}   # voice goes after 7 days
    db.run("UPDATE memberlog SET created=? WHERE user_id=?", time.time() - 91 * 86400, uid)
    timeline.prune()
    assert not timeline.recent(uid)


def test_status_card():
    now = time.time()
    head, body, accent = status_parts(None, [], [], now)
    assert head == "All clear" and accent == ui.SLATE
    head, body, accent = status_parts(None, [[now - 90000, "Old"]], [], now)
    assert head == "All clear"
    head, body, accent = status_parts(None, [[now - 60, "Hacked account or scammer"]], [], now)
    assert head == "1 alert in the last 24 h" and accent == ui.WAIT and "Hacked" in body
    head, body, accent = status_parts({"until": now + 60, "reason": "Join wave"}, [[now, "Lockdown on"]], ["Role above mine"], now)
    assert head == "Lockdown on" and accent == ui.BAD and "Join wave" in body and "Role above mine" in body
    head, _, _ = status_parts(None, [], ["Role above mine"], now)
    assert head == "All clear"   # a setup note is listed but isn't an incident


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
