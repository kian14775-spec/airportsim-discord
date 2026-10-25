"""Keeps the bot honest about being online.

If the connection has been down for WATCHDOG_MINUTES, the process exits on purpose
and the service manager (systemd) starts a fresh one. Also logs every error from
slash commands instead of failing silently.
"""
import logging
import os
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

log = logging.getLogger(__name__)
WATCHDOG_MINUTES = 5


class Health(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_ok = time.monotonic()
        self.started = time.time()
        bot.tree.on_error = self.on_app_command_error
        self.watchdog.start()

    def cog_unload(self):
        self.watchdog.cancel()

    @tasks.loop(seconds=30)
    async def watchdog(self):
        if self.bot.is_ready() and not self.bot.is_closed() and self.bot.latency < 60:
            self.last_ok = time.monotonic()
        elif time.monotonic() - self.last_ok > WATCHDOG_MINUTES * 60:
            log.critical("disconnected for %d min, restarting", WATCHDOG_MINUTES)
            logging.shutdown()
            os._exit(1)    # systemd Restart=always brings it back

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        log.error("command %s failed", interaction.command and interaction.command.qualified_name, exc_info=error)
        msg = "Something went wrong. Staff have been told."
        if isinstance(error, app_commands.MissingPermissions):
            msg = "You can't use that."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass

    def summary(self) -> str:
        up = int(time.time() - self.started)
        return f"Online · latency {round(self.bot.latency * 1000)} ms · up {up // 3600}h {up % 3600 // 60}m"


async def setup(bot):
    await bot.add_cog(Health(bot))
