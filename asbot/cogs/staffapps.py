"""Staff applications: pick a position, fill a form -> one post in the #applications forum.

Each post is tagged Pending / Accepted / Denied, staff vote with 👍 / 👎 on it and talk
underneath, then press Accept or Deny. The applicant gets a DM (and the role on accept),
and the post is locked.
"""
from __future__ import annotations

import discord
from discord.ext import commands

from .. import db
from ..core import cfg, channel, embed, is_staff, log_staff, role_named

from ..ui import BAD as RED, GOOD as GREEN, WAIT as AMBER


def positions() -> dict[str, list[str]]:
    return cfg()["applications"]["positions"]


def _tags(forum: discord.ForumChannel, name: str) -> list[discord.ForumTag]:
    return [t for t in forum.available_tags if t.name == name]


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
        forum = channel(interaction.guild, "applications")
        if not isinstance(forum, discord.ForumChannel):
            return await interaction.response.send_message("Applications aren't set up yet.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        user = interaction.user
        aid = db.run("INSERT INTO applications(user_id, position, created) VALUES(?, ?, ?)",
                     user.id, self.position, db.now())
        e = embed(f"{self.position} application", f"From {user.mention} (`{user.id}`)", colour=discord.Colour(AMBER))
        link = db.one("SELECT * FROM links WHERE discord_id=?", user.id)
        if link:
            e.description += f"\nRoblox: [{link['roblox_name']}](https://www.roblox.com/users/{link['roblox_id']}/profile)"
        if user.joined_at:
            e.description += f"\nJoined <t:{int(user.joined_at.timestamp())}:R>"
        for q, ti in self.inputs:
            e.add_field(name=q[:256], value=ti.value[:1024] or "-", inline=False)
        e.set_thumbnail(url=user.display_avatar.url)
        e.set_footer(text="Vote 👍 / 👎 and discuss below, then Accept or Deny")
        v = discord.ui.View(timeout=None)
        v.add_item(AppButton("accept", aid))
        v.add_item(AppButton("deny", aid))
        made = await forum.create_thread(name=f"{aid:04d} · {self.position} · {user.name}"[:100], embed=e, view=v,
                                         applied_tags=_tags(forum, "Pending"),
                                         allowed_mentions=discord.AllowedMentions.none())
        db.run("UPDATE applications SET message_id=?, thread_id=? WHERE id=?", made.message.id, made.thread.id, aid)
        for emoji in ("👍", "👎"):
            try:
                await made.message.add_reaction(emoji)
            except discord.HTTPException:
                pass
        await interaction.followup.send("Application sent. You'll get a DM when the team decides.", ephemeral=True)
        await log_staff(interaction.guild, f"New {self.position} application from {user.mention}", kind="Applications")


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
                    await member.add_roles(r, reason=f"application {self.aid:04d} accepted")
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
        th = interaction.channel
        if isinstance(th, discord.Thread) and isinstance(th.parent, discord.ForumChannel):
            try:
                await th.send(f"{'✅ Accepted' if accepted else '❌ Denied'} by {interaction.user.mention}{note}.",
                              allowed_mentions=discord.AllowedMentions.none())
                await th.edit(applied_tags=_tags(th.parent, "Accepted" if accepted else "Denied"),
                              locked=True, archived=True)
            except discord.HTTPException:
                pass
        await log_staff(interaction.guild, f"Application {self.aid:04d} ({app['position']}) "
                                           f"{'accepted' if accepted else 'denied'} by {interaction.user.mention}",
                        kind="Applications")


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
