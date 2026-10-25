"""/announce composer, the live game status board in #support, and the bot's status line."""
from __future__ import annotations

import discord
from discord.ext import commands, tasks

from .. import db
from ..core import channel, embed, game_stats, log, log_staff, role
from ..panels import ensure_panel
from ..ui import BAD, BRAND, GOOD, WAIT

STATES = {"ok": ("All systems normal", GOOD), "issues": ("Some issues right now", WAIT),
          "down": ("Game is down", BAD), "maintenance": ("Maintenance", BRAND)}
TARGETS = {"announcements": "announcements", "dev-logs": "devlogs", "socials": "socials"}
PINGS = {"none": None, "announcements": "ping_announcements", "updates": "ping_updates",
         "dev logs": "ping_devlogs", "polls": "ping_polls", "socials": "ping_socials"}


def status() -> dict:
    return db.kv_get("status", {"state": "ok", "issues": [], "next_update": None})


class AnnounceModal(discord.ui.Modal, title="New announcement"):
    head = discord.ui.TextInput(label="Title", max_length=200)
    body = discord.ui.TextInput(label="Message", style=discord.TextStyle.paragraph, max_length=4000)
    image = discord.ui.TextInput(label="Image link (optional)", required=False, max_length=500)

    def __init__(self, target: discord.TextChannel, ping: discord.Role | None):
        super().__init__()
        self.target, self.ping = target, ping

    async def on_submit(self, interaction: discord.Interaction):
        e = embed(self.head.value, self.body.value)
        if self.image.value.startswith("http"):
            e.set_image(url=self.image.value)
        e.set_footer(text=f"Posted by {interaction.user.display_name}")
        v = ConfirmPost(self.target, self.ping, e)
        where = f"{self.target.mention}" + (f" and ping {self.ping.mention}" if self.ping else "")
        await interaction.response.send_message(f"Preview. This will post in {where}:", embed=e, view=v,
                                                ephemeral=True)


class ConfirmPost(discord.ui.View):
    def __init__(self, target, ping, e):
        super().__init__(timeout=600)
        self.target, self.ping, self.e = target, ping, e

    @discord.ui.button(label="Post it", style=discord.ButtonStyle.success)
    async def post(self, interaction: discord.Interaction, _):
        msg = await self.target.send(content=self.ping.mention if self.ping else None, embed=self.e,
                                     allowed_mentions=discord.AllowedMentions(roles=[self.ping] if self.ping else []))
        if self.target.is_news():
            try:
                await msg.publish()
            except discord.HTTPException:
                pass
        await interaction.response.edit_message(content=f"Posted: {msg.jump_url}", embed=None, view=None)
        await log_staff(interaction.guild, f"{interaction.user.mention} posted in {self.target.mention}", kind="Bot")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _):
        await interaction.response.edit_message(content="Cancelled.", embed=None, view=None)


class News(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.players: int | None = None
        self.poll_game.start()

    def cog_unload(self):
        self.poll_game.cancel()

    @property
    def guild(self):
        return self.bot.get_guild(self.bot.guild_id)

    async def start_announce(self, interaction: discord.Interaction, where: str, ping: str = "none"):
        target = channel(interaction.guild, TARGETS[where])
        if target is None:
            return await interaction.response.send_message("That channel isn't set up.", ephemeral=True)
        r = role(interaction.guild, PINGS[ping]) if PINGS.get(ping) else None
        await interaction.response.send_modal(AnnounceModal(target, r))

    async def set_state(self, state: str):
        s = status()
        s["state"] = state
        db.kv_set("status", s)
        await self.render_status()

    async def edit_status(self, add: str = "", remove: str = "", next_update: str | None = None):
        s = status()
        if add:
            s["issues"].append(add[:200])
        if remove:
            s["issues"] = [] if remove.lower() == "all" else [i for i in s["issues"] if remove.lower() not in i.lower()]
        if next_update is not None:
            s["next_update"] = next_update or None
        db.kv_set("status", s)
        await self.render_status()

    async def render_status(self):
        """One message in #support: live game status on top, the ticket menu below."""
        from .tickets import SupportView, support_embed
        s = status()
        name, colour = STATES.get(s["state"], STATES["ok"])
        e = embed(f"Game status · {name}", colour=discord.Colour(colour))
        if self.players is not None:
            e.add_field(name="Playing now", value=f"{self.players:,}")
        e.add_field(name="Next update", value=s.get("next_update") or "Not announced yet")
        e.add_field(name="Known issues", value="\n".join(f"• {i}" for i in s["issues"]) or "None right now",
                    inline=False)
        e.set_footer(text="Status updates on its own")
        e.timestamp = discord.utils.utcnow()
        await ensure_panel("support", channel(self.guild, "support"), embeds=[e, support_embed()],
                           view=SupportView())
        await self._drop_old_status_panel()

    async def _drop_old_status_panel(self):
        """The status board used to be its own message; remove that one (the bot's own post)."""
        row = db.panel("status")
        if not row:
            return
        try:
            msg = await self.guild.get_channel(row["channel_id"]).fetch_message(row["message_id"])
            await msg.delete()
        except (discord.HTTPException, AttributeError):
            pass
        db.run("DELETE FROM panels WHERE name='status'")

    @commands.Cog.listener()
    async def on_ready(self):
        await self.render_status()

    @tasks.loop(minutes=3)
    async def poll_game(self):
        try:
            g = await game_stats()
        except Exception as e:  # noqa: BLE001 - Roblox API hiccup, try next round
            log.warning("game stats: %s", e)
            return
        if g:
            self.players = g.get("playing")
            await self.bot.change_presence(activity=discord.Activity(
                type=discord.ActivityType.watching, name=f"{self.players:,} players fly"))
        if self.guild:
            await self.render_status()

    @poll_game.before_loop
    async def _ready(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(News(bot))
