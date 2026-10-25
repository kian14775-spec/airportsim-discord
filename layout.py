"""Build or update the Discord server from layout/server.yaml.

    python layout.py preview                  show what would change (changes nothing)
    python layout.py apply                    make the changes
    python layout.py apply --archive-extras   also hide old channels in ARCHIVE
    python layout.py export                   list the server as it is now

Needs DISCORD_TOKEN and GUILD_ID in .env. Logs in, does the job, logs out; the
running bot is not affected.
"""
import argparse
import asyncio
import os
import sys

import discord
from dotenv import load_dotenv

from asbot.layout import sync
from asbot.layout.discord_backend import snapshot


async def main(args) -> int:
    load_dotenv()
    client = discord.Client(intents=discord.Intents(guilds=True))
    result = {"code": 0}

    @client.event
    async def on_ready():
        try:
            guild = client.get_guild(int(os.environ["GUILD_ID"]))
            if guild is None:
                print("The bot is not in that server (check GUILD_ID / invite the bot).")
                result["code"] = 1
            elif args.cmd == "export":
                _export(guild)
            else:
                _, p = sync.make_plan(guild, args.archive_extras)
                print(sync.describe(p))
                if args.cmd == "apply" and p.errors:
                    result["code"] = 1
                elif args.cmd == "apply" and p.changes:
                    print("\nApplying...")
                    await sync.apply(guild, args.archive_extras)
                    print("Done.")
        except Exception as e:  # noqa: BLE001 - report and exit non-zero
            print(f"FAILED: {e}")
            result["code"] = 1
        finally:
            await client.close()

    await client.start(os.environ["DISCORD_TOKEN"])
    return result["code"]


def _export(guild: discord.Guild) -> None:
    snap = snapshot(guild)
    roles = sorted((r for r in snap.roles.values() if r.id != snap.everyone_id), key=lambda r: -guild.get_role(r.id).position)
    print("ROLES:", ", ".join(r.name for r in roles))
    cats = sorted((c for c in snap.channels.values() if c.type == "category"), key=lambda c: c.position)
    for parent in [None] + [c.id for c in cats]:
        kids = sorted((c for c in snap.channels.values() if c.type != "category" and c.parent == parent),
                      key=lambda c: (c.type == "voice", c.position))
        if parent is None and not kids:
            continue
        print("\n" + (snap.channels[parent].name if parent else "(no category)"))
        for c in kids:
            print(f"  [{c.type}] {c.name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["preview", "apply", "export"])
    ap.add_argument("--archive-extras", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args())))
