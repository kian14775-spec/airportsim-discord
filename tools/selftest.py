"""Live self-test against the real server (run while the bot runs; safe, staff-only side effects).

Checks: who can see what (unverified vs verified vs staff), the bot's role height,
inbox ticket post + tag changes + close, party voice create/remove, panels present.
The test ticket post and party channel are deleted again at the end.

Layout ids come from the same place the bot reads them (asbot/paths.py STATE, via sync.load_state):
the volume on Railway; on the PC seed/state, the copy that ships to the bot.
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import discord  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from asbot import db  # noqa: E402
from asbot.layout import sync  # noqa: E402
from asbot.paths import STATE  # noqa: E402

results: list[tuple[bool, str]] = []


def check(ok: bool, text: str):
    results.append((ok, text))


def perms_for(ch: discord.abc.GuildChannel, roles: list[discord.Role]) -> discord.Permissions:
    """Effective permissions for an imaginary member holding `roles` (Discord's algorithm)."""
    g = ch.guild
    base = discord.Permissions(g.default_role.permissions.value)
    for r in roles:
        base.value |= r.permissions.value
    if base.administrator:
        return discord.Permissions.all()
    ow = ch.overwrites
    e = ow.get(g.default_role)
    if e:
        a, d = e.pair()
        base.value = (base.value & ~d.value) | a.value
    allow = deny = 0
    for r in roles:
        o = ow.get(r)
        if o:
            a, d = o.pair()
            allow |= a.value
            deny |= d.value
    base.value = (base.value & ~deny) | allow
    return base


async def run(client: discord.Client):
    g = client.get_guild(int(os.environ["GUILD_ID"]))
    state = sync.load_state(g.id)
    print(f"layout ids: {STATE / f'ids_{g.id}.json'} ({len(state)} keys)")
    if not state:
        check(False, f"no layout ids in {STATE} (see asbot/paths.py)")
        return
    ch = lambda k: g.get_channel(state.get(f"ch:{k}"))
    traveler = g.get_role(state["role:verified"])
    unverified = discord.utils.get(g.roles, name="Unverified")
    mod = g.get_role(state["role:moderator"])
    public = ["welcome", "announcements", "devlogs", "socials", "chat", "media", "liveries", "rp_servers", "support", "feedback",
              "create_party"]
    staff = ["staff_chat", "stats", "inbox", "applications", "log_security", "log_mod", "log_messages"]

    for key in public + staff:
        c = ch(key)
        if c is None:
            check(False, f"#{key} missing")
            continue
        unv = perms_for(c, [unverified] if unverified else [])
        ver = perms_for(c, [traveler])
        stf = perms_for(c, [traveler, mod])
        if key == "welcome":
            check(unv.view_channel and not unv.send_messages, "unverified can read #welcome, can't type")
        elif key in public:
            check(not unv.view_channel, f"unverified can't see #{c.name}")
            check(ver.view_channel, f"verified can see #{c.name}")
        else:
            check(not ver.view_channel and stf.view_channel, f"#{c.name} is staff-only")
    for key in ("announcements", "devlogs", "socials", "support"):
        check(not perms_for(ch(key), [traveler]).send_messages, f"members can't post in #{ch(key).name}")
    check(perms_for(ch("chat"), [traveler]).send_messages, "members can chat in #chat")
    check(perms_for(ch("feedback"), [traveler]).send_messages, "members can make posts in #feedback")

    me = g.me.top_role
    for key in ("verified", "ping_announcements", "ping_devlogs", "ping_socials", "ping_polls"):
        r = g.get_role(state[f"role:{key}"])
        check(r < me, f"bot can give @{r.name}" + ("" if r < me else " -> MOVE THE BOT'S ROLE ABOVE IT"))
    if unverified:
        check(unverified < me, "bot can remove @Unverified" + ("" if unverified < me else " -> MOVE THE BOT'S ROLE"))

    for key in ("welcome", "support", "stats"):
        found = None
        async for m in ch(key).history(limit=25):
            if m.author.id == g.me.id and m.flags.components_v2:
                found = m
                break
        check(found is not None, f"{key} card posted (new layout)")

    inbox = ch("inbox")
    pinned = [t for t in inbox.threads if t.flags.pinned]
    check(any("How the inbox works" in t.name for t in pinned), "inbox guide pinned")
    tag = lambda n: next(t for t in inbox.available_tags if t.name == n)
    made = await inbox.create_thread(name="[self-test] ticket flow", content="Automatic test, ignore.",
                                     applied_tags=[tag("Open")])
    th = made.thread
    await th.edit(applied_tags=[tag("Waiting"), tag("Claimed"), tag("Urgent")])
    th = await client.fetch_channel(th.id)
    check({t.name for t in th.applied_tags} == {"Waiting", "Claimed", "Urgent"}, "ticket tags switch")
    await th.send("[self-test] member message")
    await th.send("// [self-test] staff note")
    from asbot import transcripts
    n = await transcripts.archive(999999, th, g.me.id)
    check(n >= 2 and len(transcripts.pages(999999)) >= 1, "closed ticket chat stored for the viewer")
    db.run("DELETE FROM ticket_messages WHERE ticket_id=999999")
    await th.delete()
    try:
        await client.fetch_channel(th.id)
        check(False, "closed ticket removed from inbox")
    except discord.NotFound:
        check(True, "closed ticket removed from inbox")

    hub = ch("create_party")
    vc = await g.create_voice_channel("[self-test] party", category=hub.category, overwrites=hub.overwrites)
    check(perms_for(vc, [traveler]).connect, "party channel joinable by members")
    await vc.delete(reason="self-test")
    check(True, "party channel removed")

    polls = g.get_role(state["role:ping_polls"])
    await g.me.add_roles(polls, reason="self-test")
    await g.me.remove_roles(polls, reason="self-test")
    check(True, "bot can give/remove a ping role it sits above")


async def main():
    load_dotenv(ROOT / ".env")
    client = discord.Client(intents=discord.Intents(guilds=True))

    @client.event
    async def on_ready():
        try:
            await run(client)
        except Exception as e:  # noqa: BLE001
            check(False, f"crashed: {type(e).__name__}: {e}")
        finally:
            await client.close()

    await client.start(os.environ["DISCORD_TOKEN"])
    for ok, text in results:
        print(("PASS " if ok else "FAIL ") + text)
    print(f"\n{sum(ok for ok, _ in results)}/{len(results)} passed")


if __name__ == "__main__":
    asyncio.run(main())
