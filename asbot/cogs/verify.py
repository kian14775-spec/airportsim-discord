"""Roblox verify (code in profile About), welcome panel, ping-roles panel.

Verify: member enters their Roblox username -> bot gives a short code -> they paste it
in their Roblox profile About -> bot checks the public profile -> link saved,
Traveler role given, Unverified removed, nickname set to the Roblox name.
No keys or OAuth needed. Members already verified by Bloxlink keep their roles.
"""
from __future__ import annotations

import random

import discord
from discord import app_commands
from discord.ext import commands

from .. import db
from ..core import (cfg, channel, embed, log_staff, role, role_named, roblox_headshot,
                    roblox_user, roblox_user_by_name, staff_only)
from ..panels import ensure_panel

WORDS = ["runway", "taxi", "gate", "cabin", "jet", "tower", "cargo", "radar", "flaps", "pilot",
         "hangar", "apron", "boarding", "terminal", "jetway", "cockpit", "landing", "takeoff"]
CODE_TTL = 30 * 60


async def apply_verified(member: discord.Member, roblox_id: int, roblox_name: str) -> list[str]:
    """Give roles + nickname. Returns problems (e.g. role above the bot) for the reply."""
    problems = []
    verified, unverified = role(member.guild, "verified"), role_named(member.guild, "Unverified")
    try:
        if verified and verified not in member.roles:
            await member.add_roles(verified, reason=f"verified as {roblox_name}")
        if unverified and unverified in member.roles:
            await member.remove_roles(unverified, reason="verified")
    except discord.Forbidden:
        problems.append("I couldn't change your roles (my role is too low). Staff have been told.")
        await log_staff(member.guild, f"Verify: can't edit roles for {member.mention}. "
                                      "Move the bot's role above Traveler/Unverified.")
    if member.display_name != roblox_name and member.id != member.guild.owner_id:
        try:
            await member.edit(nick=roblox_name, reason="verified")
        except discord.Forbidden:
            pass
    return problems


class CheckView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="I've added it, check now", style=discord.ButtonStyle.success,
                       custom_id="asbot:verify:check")
    async def check(self, interaction: discord.Interaction, _):
        row = db.one("SELECT * FROM pending_verify WHERE discord_id=?", interaction.user.id)
        if not row or db.now() - row["created"] > CODE_TTL:
            return await interaction.response.send_message(
                "That code expired. Press Verify again for a new one.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        user = await roblox_user(row["roblox_id"])
        if not user or row["code"].lower() not in (user.get("description") or "").lower():
            return await interaction.followup.send(
                f"I can't see `{row['code']}` in **{row['roblox_name']}**'s About yet. Save your profile, "
                "wait a few seconds and press the button again.", ephemeral=True)
        await finish_link(interaction, row["roblox_id"], row["roblox_name"])


async def finish_link(interaction: discord.Interaction, roblox_id: int, roblox_name: str):
    member = interaction.user
    db.run("DELETE FROM pending_verify WHERE discord_id=?", member.id)
    db.run("INSERT INTO links VALUES(?, ?, ?, ?) ON CONFLICT(discord_id) DO UPDATE SET roblox_id=excluded.roblox_id, "
           "roblox_name=excluded.roblox_name, linked_at=excluded.linked_at", member.id, roblox_id, roblox_name, db.now())
    problems = await apply_verified(member, roblox_id, roblox_name)
    e = embed("You're verified", f"Linked to **{roblox_name}**. You can remove the code from your About now.")
    thumb = await roblox_headshot(roblox_id)
    if thumb:
        e.set_thumbnail(url=thumb)
    if problems:
        e.add_field(name="Heads up", value="\n".join(problems), inline=False)
    roles_ch = channel(member.guild, "roles")
    if roles_ch:
        e.add_field(name="Next", value=f"Pick your ping roles in {roles_ch.mention}.", inline=False)
    await interaction.followup.send(embed=e, ephemeral=True)
    await log_staff(member.guild, f"Verified {member.mention} as **{roblox_name}** (`{roblox_id}`)")
    interaction.client.dispatch("roblox_linked", member, roblox_id)


class UsernameModal(discord.ui.Modal, title="Verify with Roblox"):
    username = discord.ui.TextInput(label="Your Roblox username", min_length=3, max_length=20,
                                    placeholder="Builderman")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        user = await roblox_user_by_name(self.username.value.strip())
        if not user:
            return await interaction.followup.send("I couldn't find that Roblox username. Check the spelling.",
                                                   ephemeral=True)
        code = f"ASIM-{random.choice(WORDS)}-{random.randint(1000, 9999)}"
        db.run("INSERT INTO pending_verify VALUES(?, ?, ?, ?, ?) ON CONFLICT(discord_id) DO UPDATE SET "
               "roblox_id=excluded.roblox_id, roblox_name=excluded.roblox_name, code=excluded.code, "
               "created=excluded.created", interaction.user.id, user["id"], user["name"], code, db.now())
        e = embed(f"Almost there, {user['name']}",
                  f"1. Open your [Roblox profile](https://www.roblox.com/users/{user['id']}/profile) "
                  "and edit **About**.\n"
                  f"2. Paste this code anywhere in it and save:\n```{code}```"
                  "3. Press the button below.\n\nThe code works for 30 minutes.")
        await interaction.followup.send(embed=e, view=CheckView(), ephemeral=True)


class WelcomeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for label, url in (cfg()["welcome"].get("links") or {}).items():
            self.add_item(discord.ui.Button(label=label, url=url, row=1))

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, custom_id="asbot:verify:start", row=0)
    async def verify(self, interaction: discord.Interaction, _):
        link = db.one("SELECT * FROM links WHERE discord_id=?", interaction.user.id)
        if link:
            await interaction.response.defer(ephemeral=True, thinking=True)
            problems = await apply_verified(interaction.user, link["roblox_id"], link["roblox_name"])
            msg = f"You're already verified as **{link['roblox_name']}**."
            return await interaction.followup.send("\n".join([msg, *problems]), ephemeral=True)
        await interaction.response.send_modal(UsernameModal())


class RolesSelect(discord.ui.Select):
    def __init__(self):
        opts = []
        for p in cfg()["ping_roles"]:
            opts.append(discord.SelectOption(label=p["label"], description=p.get("description"),
                                             value=p["role"]))
        super().__init__(placeholder="Choose what pings you", min_values=0, max_values=len(opts),
                         options=opts, custom_id="asbot:roles:select")

    async def callback(self, interaction: discord.Interaction):
        member, chosen = interaction.user, set(self.values)
        add, remove, missing = [], [], []
        for p in cfg()["ping_roles"]:
            r = role(member.guild, p["role"])
            if r is None:
                missing.append(p["label"])
            elif p["role"] in chosen and r not in member.roles:
                add.append(r)
            elif p["role"] not in chosen and r in member.roles:
                remove.append(r)
        try:
            if add:
                await member.add_roles(*add, reason="roles panel")
            if remove:
                await member.remove_roles(*remove, reason="roles panel")
        except discord.Forbidden:
            await log_staff(member.guild, "Roles panel: my role is below the ping roles. Move it up.")
            return await interaction.response.send_message("I can't give that role right now. Staff have been told.",
                                                           ephemeral=True)
        now = [p["label"] for p in cfg()["ping_roles"] if p["role"] in chosen]
        await interaction.response.send_message(
            ("You'll get pings for: " + ", ".join(now)) if now else "You won't get any pings.", ephemeral=True)


class RolesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(RolesSelect())


class Verify(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(WelcomeView())
        bot.add_view(CheckView())
        bot.add_view(RolesView())

    @commands.Cog.listener()
    async def on_ready(self):
        guild = self.bot.get_guild(self.bot.guild_id)
        if guild is None:
            return
        await ensure_panel("welcome", channel(guild, "welcome"), view=WelcomeView(), embeds=[
            embed("Verify to unlock the server",
                  "Read the rules above, then press **Verify** and link your Roblox account. "
                  "It takes under a minute.\n\nNeed help? Verify first, then use #support.")])
        await ensure_panel("roles", channel(guild, "roles"), view=RolesView(), embeds=[
            embed("Pick your pings", "Choose what you want to be pinged for. Change it any time.\n\n" +
                  "\n".join(f"**{p['label']}**: {p.get('description', '')}" for p in cfg()["ping_roles"]))])

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        link = db.one("SELECT * FROM links WHERE discord_id=?", member.id)
        if link:   # rejoined: verified again straight away
            await apply_verified(member, link["roblox_id"], link["roblox_name"])

    @app_commands.command(description="Link your Roblox account")
    async def verify(self, interaction: discord.Interaction):
        await WelcomeView().verify.callback(interaction)

    @app_commands.command(description="Show a member's linked Roblox account")
    @staff_only()
    async def whois(self, interaction: discord.Interaction, member: discord.Member):
        link = db.one("SELECT * FROM links WHERE discord_id=?", member.id)
        if not link:
            return await interaction.response.send_message(f"{member.mention} hasn't verified with this bot.",
                                                           ephemeral=True)
        e = embed(link["roblox_name"], f"[Roblox profile](https://www.roblox.com/users/{link['roblox_id']}/profile)\n"
                                       f"ID `{link['roblox_id']}` · linked <t:{int(link['linked_at'])}:R>")
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(description="Unlink your Roblox account")
    async def unlink(self, interaction: discord.Interaction):
        db.run("DELETE FROM links WHERE discord_id=?", interaction.user.id)
        await interaction.response.send_message("Unlinked. Press Verify in #welcome to link again.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Verify(bot))
