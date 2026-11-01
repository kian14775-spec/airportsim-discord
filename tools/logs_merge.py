"""Seven log channels -> three, without a full `layout apply` (that would undo the hand-made
perms on #general, #media and #dev-logs).

    python tools/logs_merge.py            show what would change
    python tools/logs_merge.py rename     rename the 3 that stay + set their topics (history kept)
    python tools/logs_merge.py delete     back up the other 4 to backups/, then delete them

Channels are found by their exact ids in state/ and must still have the expected name.
Safe to run again. Run `delete` after the new bot is live and posting.
"""
import asyncio
import datetime as dt
import json
import os
import sys
from pathlib import Path

import discord
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from asbot.layout import sync  # noqa: E402

KEEP = {"log_security": "security-logs", "log_mod": "mod-logs", "log_messages": "message-logs"}
DROP = {"log_members": "member-logs", "log_server": "server-logs", "log_voice": "voice-logs", "log_tickets": "ticket-logs"}


def new_channels() -> dict:
    with open(ROOT / "layout" / "server.yaml", encoding="utf-8") as f:
        cats = yaml.safe_load(f)["categories"]
    logs = next(c for c in cats if c["key"] == "logs_cat")
    return {c["key"]: c for c in logs["channels"]}


async def backup(ch: discord.TextChannel, folder: Path) -> int:
    rows = [{"id": m.id, "time": m.created_at.isoformat(), "author": str(m.author), "content": m.content,
             "embeds": [e.to_dict() for e in m.embeds], "files": [a.url for a in m.attachments]}
            async for m in ch.history(limit=None, oldest_first=True)]
    (folder / f"{ch.name}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(rows)


async def main(mode: str):
    load_dotenv(ROOT / ".env")
    c = discord.Client(intents=discord.Intents.none())
    await c.login(os.environ["DISCORD_TOKEN"])
    g = await c.fetch_guild(int(os.environ["GUILD_ID"]))
    state, new = sync.load_state(g.id), new_channels()
    live = {x.id: x for x in await g.fetch_channels()}

    for key, old in KEEP.items():
        ch, spec = live.get(state.get(f"ch:{key}")), new[key]
        if ch is None:
            print(f"! {key}: missing, skipped")
        elif ch.name == spec["name"]:
            print(f"  #{ch.name}: already renamed")
        elif ch.name != old:
            print(f"! #{ch.name}: expected #{old}, skipped")
        elif mode == "rename":
            await ch.edit(name=spec["name"], topic=spec["topic"], reason="Logs: seven channels -> three")
            print(f"  #{old} -> #{spec['name']}")
        else:
            print(f"  would rename #{old} -> #{spec['name']}")

    folder = ROOT / "backups" / f"logs_merge_{dt.date.today():%Y-%m-%d}"
    for key, old in DROP.items():
        ch = live.get(state.get(f"ch:{key}"))
        if ch is None:
            print(f"  #{old}: already gone")
        elif ch.name != old:
            print(f"! #{ch.name}: expected #{old}, skipped")
        elif mode == "delete":
            folder.mkdir(parents=True, exist_ok=True)
            n = await backup(ch, folder)
            await ch.delete(reason="Logs: seven channels -> three (backed up)")
            print(f"  #{old}: {n} messages backed up, deleted ({ch.id})")
        else:
            print(f"  would back up + delete #{old} ({ch.id})")
    await c.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "show"))
