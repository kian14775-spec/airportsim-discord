"""Build or update the Discord server from layout/server.yaml.

    python layout.py preview                  show what would change (changes nothing)
    python layout.py export                   list the server as it is now
    python layout.py export --perms           ...with every channel's permission overwrites
    python layout.py apply --from-pc          make the changes from the PC (see below)
    python layout.py apply --from-pc --archive-extras   also hide old channels in ARCHIVE

Needs DISCORD_TOKEN and GUILD_ID in .env. Logs in, does the job, logs out; the
running bot is not affected.

Applies normally run ON Railway: /layout preview, then /layout apply in Discord, so the
volume's state stays the truth. On the PC the state is seed/state (asbot/paths.py), which can
be behind the volume; an apply from here needs --from-pc and the steps in ARCHITECTURE.md.
"""
import argparse
import asyncio
import os
import sys

import discord
from dotenv import load_dotenv

from asbot.layout import sync
from asbot.layout.discord_backend import snapshot
from asbot.layout.planner import flag_names


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
                _export(guild, args.perms)
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


def _export(guild: discord.Guild, perms: bool = False) -> None:
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
            if perms:
                _print_perms(snap, c, "      ")


def _print_perms(snap, c, pad: str) -> None:
    """Exact overwrites, to copy into perms_extra in layout/server.yaml."""
    for t, (allow, deny) in sorted(c.overwrites.items()):
        if t in c.member_targets:
            who = f"member {t}"
        elif t == snap.everyone_id:
            who = "@everyone"
        else:
            who = f"@{snap.roles[t].name}" if t in snap.roles else f"role {t}"
        print(f"{pad}{who}: allow [{', '.join(flag_names(allow))}]  deny [{', '.join(flag_names(deny))}]")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["preview", "apply", "export"])
    ap.add_argument("--archive-extras", action="store_true")
    ap.add_argument("--from-pc", action="store_true", help="really apply from the PC (normally: /layout apply)")
    ap.add_argument("--perms", action="store_true", help="export: also list permission overwrites")
    a = ap.parse_args()
    if a.cmd == "apply" and not a.from_pc:
        sys.exit("Layout applies run on Railway: /layout preview, then /layout apply in Discord.\n"
                 "Only if that's impossible: python layout.py apply --from-pc, then follow\n"
                 "'Applying from the PC' in ARCHITECTURE.md (commit seed/state + deploy).")
    sys.exit(asyncio.run(main(a)))
