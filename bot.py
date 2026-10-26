"""Airport Simulator bot. Start: python bot.py  (on the server it runs as a service)."""
import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from asbot import ui
from asbot.paths import DATA, seed_once

ROOT = Path(__file__).resolve().parent
COGS = ["asbot.cogs.admin", "asbot.cogs.health", "asbot.cogs.verify", "asbot.cogs.tickets",
        "asbot.cogs.staffapps", "asbot.cogs.news", "asbot.cogs.community", "asbot.cogs.forums",
        "asbot.cogs.hub", "asbot.cogs.stats", "asbot.cogs.strikes",
        "asbot.cogs.modlog", "asbot.cogs.answers", "asbot.cogs.staffcmds", "asbot.cogs.profile"]


def setup_logging() -> None:
    logs = DATA / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file = RotatingFileHandler(logs / "bot.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[file, console])


class AirportBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True            # verify + roles
        intents.message_content = True    # DM tickets
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, max_messages=5000,
                         allowed_mentions=discord.AllowedMentions(everyone=False, roles=False))
        self.guild_id = int(os.environ["GUILD_ID"])

    async def setup_hook(self):
        for cog in COGS:
            await self.load_extension(cog)
        guild = discord.Object(self.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)   # guild sync = commands show up instantly

    async def on_ready(self):
        guild = self.get_guild(self.guild_id)
        if guild and guild.icon:
            ui.set_icon(guild.icon.url)   # small server icon in every card's footer
        logging.info("online as %s in %d server(s)", self.user, len(self.guilds))


async def main():
    load_dotenv(ROOT / ".env")
    seed_once()
    setup_logging()
    logging.info("data folder %s, seed in image: %s, state files: %s", DATA, (ROOT / "seed").exists(),
                 [p.name for p in (DATA / "state").glob("*.json")])
    bot = AirportBot()
    async with bot:
        await bot.start(os.environ["DISCORD_TOKEN"])   # reconnects on its own after drops


if __name__ == "__main__":
    asyncio.run(main())
