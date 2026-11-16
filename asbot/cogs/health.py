"""Keeps the bot honest about being online.

If the connection has been down for WATCHDOG_MINUTES, the process exits on purpose
and the service manager (systemd) starts a fresh one. A background loop (tasks.loop in
any cog) that stopped is started again, with one line in #alerts. Also logs every error
from slash commands instead of failing silently.
"""
import logging
import os
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..core import guarded, log_staff

log = logging.getLogger(__name__)
WATCHDOG_MINUTES = 5
ALERT_EVERY = 3600   # a loop that keeps dying is restarted every check, but posted about once an hour


class Health(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_ok = time.monotonic()
        self.started = time.time()
        self.revived: dict[str, float] = {}
        bot.tree.on_error = self.on_app_command_error
        self.watchdog.start()

    def cog_unload(self):
        self.watchdog.cancel()

    @tasks.loop(seconds=30)
    @guarded
    async def watchdog(self):
        if self.bot.is_ready() and not self.bot.is_closed() and self.bot.latency < 60:
            self.last_ok = time.monotonic()
            await self.revive_loops()
        elif time.monotonic() - self.last_ok > WATCHDOG_MINUTES * 60:
            log.critical("disconnected for %d min, restarting", WATCHDOG_MINUTES)
            logging.shutdown()
            os._exit(1)    # systemd Restart=always brings it back

    async def revive_loops(self):
        """Start again every cog loop whose task ended (an error got past its guard)."""
        for cog in list(self.bot.cogs.values()):
            for name, attr in vars(type(cog)).items():
                if not isinstance(attr, tasks.Loop):
                    continue
                loop = getattr(cog, name)
                task = loop.get_task()
                if task is None or not task.done() or task.cancelled():   # never started, running, or stopped on purpose
                    continue
                what = f"{type(cog).__name__}.{name}"
                log.error("loop %s had stopped, restarting it", what, exc_info=task.exception())
                loop.start()
                g = self.bot.get_guild(self.bot.guild_id)
                if g and time.time() - self.revived.get(what, 0) > ALERT_EVERY:
                    self.revived[what] = time.time()
                    await log_staff(g, f"Background job `{what}` had stopped and was restarted. Details in the bot log.",
                                    kind="Security", event="loop_restarted")

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):   # not allowed: nothing broke
            if interaction.response.is_done():   # the check (staff_only) already answered
                return
            msg = "You can't use that."
        else:
            log.error("command %s failed", interaction.command and interaction.command.qualified_name, exc_info=error)
            msg = "Something went wrong. Staff have been told."
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
