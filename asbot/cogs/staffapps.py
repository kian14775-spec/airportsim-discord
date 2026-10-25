"""Staff applications: pick a position, fill a form, staff accept/deny with buttons."""
from __future__ import annotations

import discord
from discord.ext import commands

from .. import db
from ..core import cfg, channel, embed, is_staff, log_staff, role_named

GREEN, RED, AMBER = 0x2ECC71, 0xE74C3C, 0xF1C40F


def positions() -> dict[str, list[str]]:
    return cfg()["applications"]["positions"]


class AppModal(discord.ui.Modal):
    def __init__(self, position: str):
        super().__init__(title=f"Apply: {position}"[:45])
        self.position = position
        self.inputs = []
        for q in positions()[position][:5]:
            ti = discord.ui.TextInput(label=q[:45], style=discord.TextStyle.paragraph, max_length=1000,
                                      placeholder=q[:100] if len(q) > 45 else None)
            self.inputs.append((q, ti))
            self.add_item(ti)

    async def on_submit(self, interaction: discord.Interaction):
        ch = channel(interaction.guild, "applications")
        if ch is None:
            return await interaction.response.send_message("Applications aren't set up yet.", ephemeral=True)
        aid = db.run("INSERT INTO applications(user_id, position, created) VALUES(?, ?, ?)",
                     interaction.user.id, self.position, db.now())
        e = embed(f"Application #{aid} · {self.position}", f"From {interaction.user.mention} (`{interaction.user.id}`)",
                  colour=discord.Colour(AMBER))
        link = db.one("SELECT * FROM links WHERE discord_id=?", interaction.user.id)
        if link:
            e.description += f"\nRoblox: [{link['roblox_name']}](https://www.roblox.com/users/{link['roblox_id']}/profile)"
        if interaction.user.joined_at:
            e.description += f"\nJoined <t:{int(interaction.user.joined_at.timestamp())}:R>"
        for q, ti in self.inputs:
            e.add_field(name=q[:256], value=ti.value[:1024] or "-", inline=False)
        e.set_thumbnail(url=interaction.user.display_avatar.url)
        v = discord.ui.View(timeout=None)
        v.add_item(AppButton("accept", aid))
        v.add_item(AppButton("deny", aid))
        msg = await ch.send(embed=e, view=v)
        db.run("UPDATE applications SET message_id=? WHERE id=?", msg.id, aid)
        await interaction.response.send_message(
            "Application sent. You'll get a DM when the team decides.", ephemeral=True)


class AppButton(discord.ui.DynamicItem[discord.ui.Button], template=r"asbot:app:(?P<action>accept|deny):(?P<id>\d+)"):
    def __init__(self, action: str, aid: int):
        style = discord.ButtonStyle.success if action == "accept" else discord.ButtonStyle.danger
        super().__init__(discord.ui.Button(label=action.title(), style=style, custom_id=f"asbot:app:{action}:{aid}"))
        self.action, self.aid = action, aid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["action"], int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("Staff only.", ephemeral=True)
        app = db.one("SELECT * FROM applications WHERE id=?", self.aid)
        if not app or app["status"] != "pending":
            return await interaction.response.send_message("Already decided.", ephemeral=True)
        accepted = self.action == "accept"
        db.run("UPDATE applications SET status=? WHERE id=?", "accepted" if accepted else "denied", self.aid)
        e = interaction.message.embeds[0]
        e.colour = discord.Colour(GREEN if accepted else RED)
        e.set_footer(text=f"{'Accepted' if accepted else 'Denied'} by {interaction.user.display_name}")
        await interaction.response.edit_message(embed=e, view=None)
        member = interaction.guild.get_member(app["user_id"])
        note = ""
        if accepted and member:
            rname = cfg()["applications"].get("accept_roles", {}).get(app["position"])
            r = role_named(interaction.guild, rname) if rname else None
            if r:
                try:
                    await member.add_roles(r, reason=f"application #{self.aid} accepted")
                except discord.Forbidden:
                    note = f" (couldn't give @{r.name}: my role is too low)"
        if member:
            try:
                await member.send(embed=embed(
                    f"Your {app['position']} application",
                    "Congrats, you've been accepted! Staff will reach out with next steps." if accepted
                    else "Thanks for applying. This time it's a no, but you're welcome to apply again later.",
                    colour=discord.Colour(GREEN if accepted else RED)))
            except discord.HTTPException:
                note += " (couldn't DM them)"
        await interaction.followup.send(f"Done{note}.", ephemeral=True)
        await log_staff(interaction.guild, f"Application #{self.aid} ({app['position']}) "
                                           f"{'accepted' if accepted else 'denied'} by {interaction.user.mention}")


class PositionSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder="Which position?", options=[discord.SelectOption(label=p) for p in positions()])

    async def callback(self, interaction: discord.Interaction):
        pending = db.one("SELECT 1 FROM applications WHERE user_id=? AND position=? AND status='pending'",
                         interaction.user.id, self.values[0])
        if pending:
            return await interaction.response.send_message("You already have an application for this waiting.",
                                                           ephemeral=True)
        await interaction.response.send_modal(AppModal(self.values[0]))


class Applications(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        bot.add_dynamic_items(AppButton)

    async def start(self, interaction: discord.Interaction):
        v = discord.ui.View(timeout=300)
        v.add_item(PositionSelect())
        await interaction.response.send_message("Pick the position you want to apply for:", view=v, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Applications(bot))
