"""One-off: move closed/leftover posts out of #inbox.

- "[self-test]" posts: removed (test junk the self-test made).
- Any other post that isn't an open ticket (names starting "#<n> ·", the old format): the full
  conversation is saved to #transcripts as an HTML file first, then the post is removed.
- Open tickets (new "🟢 0002 · ..." titles) and the pinned guide are left alone.
Run: python tools/cleanup_inbox.py [--dry-run]
"""
import asyncio
import io
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import discord  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from asbot.cogs.tickets import _html_row  # noqa: E402
from asbot.layout import sync  # noqa: E402

OLD = re.compile(r"^#(\d+) · (.+?) · (.+)$")


async def run(client: discord.Client, dry: bool):
    g = client.get_guild(int(os.environ["GUILD_ID"]))
    state = sync.load_state(g.id)
    inbox, out = g.get_channel(state["ch:inbox"]), g.get_channel(state["ch:transcripts"])
    posts = list(inbox.threads)
    async for th in inbox.archived_threads(limit=200):
        posts.append(th)
    for th in posts:
        if th.flags.pinned or th.name[:1] in "🟢🔵🟡🔴":
            continue
        if th.name.startswith("[self-test]"):
            print("remove test post:", th.name)
            if not dry:
                await th.delete()
            continue
        m = OLD.match(th.name)
        if not m:
            print("left alone (unknown):", th.name)
            continue
        rows, files = [], []
        async for msg in th.history(limit=None, oldest_first=True):
            rows.append(_html_row(msg))
            for a in msg.attachments:
                if len(files) < 9 and a.size <= 8_000_000:
                    files.append(await a.to_file())
        doc = (f"<!doctype html><meta charset='utf-8'><title>{th.name}</title>"
               f"<body style='background:#313338;color:#dbdee1;font-family:system-ui'><h1>{th.name}</h1>"
               + "".join(rows) + "</body>")
        num = int(m.group(1))
        card = discord.Embed(title=f"Ticket {num:04d} · {m.group(2)}", colour=0x95A5A6,
                             description=f"Member: {m.group(3)}\nMoved here from the old inbox.")
        card.set_footer(text=f"{len(rows)} messages · full chat in the file")
        print("transcript + remove:", th.name, f"({len(rows)} messages)")
        if not dry:
            files.insert(0, discord.File(io.BytesIO(doc.encode()), f"ticket-{num:04d}.html"))
            await out.send(embed=card, files=files)
            await th.delete()


async def main():
    load_dotenv(ROOT / ".env")
    client = discord.Client(intents=discord.Intents(guilds=True))

    @client.event
    async def on_ready():
        try:
            await run(client, "--dry-run" in sys.argv)
        finally:
            await client.close()

    await client.start(os.environ["DISCORD_TOKEN"])


if __name__ == "__main__":
    asyncio.run(main())
