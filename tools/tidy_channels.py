"""Remove the bot's OWN clutter from the channels where it keeps its cards. Read-only unless --apply.

What counts as clutter (nothing else is ever touched):
  - a second copy of a one-of-a-kind card (same title twice, e.g. two "Creators and events" cards):
    the copy the bot still edits (newest edit) stays, the others go;
  - Discord's "Airport Simulator pinned a message" lines the bot caused;
  - report posts the bot no longer makes (RETIRED: daily/weekly reports, the old 30-day post, weekly staff
    reports): since 2026-11-16 they open privately from the menus on the #stats card and the Leads desk.
Only these channels are looked at (layout keys): CARD_CHANNELS. Announcements, dev-logs, liveries,
socials, chat, media and every member post are never read for deleting. Only the bot's own messages.
Before deleting, every message is saved to backups/tidy_<utc>.json (text + components).

    python tools/tidy_channels.py            # list what it would remove
    python tools/tidy_channels.py --apply    # remove it (after the backup)
"""
import asyncio
import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import discord  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

CARD_CHANNELS = ("welcome", "support", "stats", "staff_chat", "creators_room", "leads")
ONE_OF_A_KIND = ("Welcome to Airport Simulator", "Support", "Right now", "Staff board", "Staff guide",
                 "Creators and events", "Leads desk")
RETIRED = ("Daily report", "Weekly support report", "Staff report · last 30 days", "Staff week ")   # prefix match
PIN_NOTICE = 6          # Discord message type CHANNEL_PINNED_MESSAGE
LOOK_BACK = 100


def title_of(m: dict) -> str:
    def first(o):
        if isinstance(o, dict):
            if isinstance(o.get("content"), str) and o["content"].startswith("## "):
                return o["content"][3:].split("\n")[0].strip()
            for v in o.values():
                if (t := first(v)):
                    return t
        elif isinstance(o, list):
            for v in o:
                if (t := first(v)):
                    return t
        return ""
    return first(m.get("components", []))


async def main(apply: bool):
    load_dotenv(ROOT / ".env")
    ids = json.loads((ROOT / "seed" / "state" / f"ids_{os.environ['GUILD_ID']}.json").read_text())
    c = discord.Client(intents=discord.Intents.none())
    await c.login(os.environ["DISCORD_TOKEN"])
    me = (await c.application_info()).id
    doomed: list[tuple[int, dict, str]] = []
    try:
        for key in CARD_CHANNELS:
            cid = ids.get(f"ch:{key}")
            if not cid:
                continue
            msgs, before = [], None
            for _ in range(5):                      # up to 500 back: old daily reports go back weeks
                batch = await c.http.logs_from(cid, LOOK_BACK, before=before)
                if not batch:
                    break
                msgs += [m for m in batch if int(m["author"]["id"]) == me]
                before = batch[-1]["id"]
            for m in msgs:
                if m["type"] == PIN_NOTICE:
                    doomed.append((cid, m, f"#{key}: pin notice"))
                elif (t := title_of(m)) and any(t.startswith(r) for r in RETIRED):
                    doomed.append((cid, m, f"#{key}: old '{t}' post from {m['timestamp'][:10]}"))
            cardz: dict[str, list[dict]] = {}
            for m in msgs:
                t = title_of(m)
                if t in ONE_OF_A_KIND:
                    cardz.setdefault(t, []).append(m)
            for t, group in cardz.items():
                if len(group) < 2:
                    continue
                group.sort(key=lambda m: m.get("edited_timestamp") or m["timestamp"], reverse=True)
                for m in group[1:]:
                    doomed.append((cid, m, f"#{key}: extra '{t}' card from {m['timestamp'][:16]}"))
        for _, m, why in doomed:
            print("-", why, m["id"])
        if not doomed:
            print("Nothing to tidy.")
            return
        if not apply:
            print(f"{len(doomed)} message(s). Run with --apply to remove them.")
            return
        out = ROOT / "backups" / f"tidy_{dt.datetime.now(dt.timezone.utc):%Y-%m-%d_%H%M%S}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps([{"channel_id": cid, "why": why, "message": m} for cid, m, why in doomed],
                                  indent=1), encoding="utf-8")
        print("backup:", out)
        for cid, m, why in doomed:
            try:
                await c.http.delete_message(cid, m["id"], reason="tidy: bot's own duplicate/pin notice")
                print("removed", m["id"])
            except discord.HTTPException as e:
                print("failed", m["id"], e)
            await asyncio.sleep(0.6)
    finally:
        await c.close()


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv))
