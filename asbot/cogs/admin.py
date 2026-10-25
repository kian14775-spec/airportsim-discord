"""/layout preview | apply  - same as layout.py, from inside Discord (admins only)."""
import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..layout import sync

log = logging.getLogger(__name__)


def _as_reply(text: str) -> dict:
    if len(text) <= 1900:
        return {"content": f"```\n{text}\n```"}
    return {"content": "Full list attached.", "file": discord.File(io.BytesIO(text.encode()), "layout.txt")}


class ConfirmApply(discord.ui.View):
    def __init__(self, archive_extras: bool):
        super().__init__(timeout=120)
        self.archive_extras = archive_extras

    @discord.ui.button(label="Apply changes", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _):
        self.stop()
        await interaction.response.edit_message(content="Applying...", view=None, attachments=[])
        lines = []
        try:
            await sync.apply(interaction.guild, self.archive_extras, log=lines.append)
            text = "Done.\n" + "\n".join(lines)
        except Exception as e:  # noqa: BLE001
            log.exception("layout apply failed")
            text = f"Stopped: {e}\nDone before that:\n" + "\n".join(lines)
        await interaction.followup.send(ephemeral=True, **_as_reply(text))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _):
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", view=None, attachments=[])


@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
class Layout(commands.GroupCog, name="layout"):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(description="Show what the layout file would change")
    async def preview(self, interaction: discord.Interaction, archive_extras: bool = False):
        _, p = sync.make_plan(interaction.guild, archive_extras)
        await interaction.response.send_message(ephemeral=True, **_as_reply(sync.describe(p)))

    @app_commands.command(description="Make the server match the layout file")
    async def apply(self, interaction: discord.Interaction, archive_extras: bool = False):
        _, p = sync.make_plan(interaction.guild, archive_extras)
        reply = _as_reply(sync.describe(p))
        if p.errors or not p.changes:
            return await interaction.response.send_message(ephemeral=True, **reply)
        await interaction.response.send_message(ephemeral=True, view=ConfirmApply(archive_extras), **reply)


async def setup(bot):
    await bot.add_cog(Layout(bot))
