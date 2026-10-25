"""Roblox verify (code in profile About), welcome panel, ping-roles panel.

Verify: member enters their Roblox username -> bot gives a short code -> they paste it
in their Roblox profile About -> bot checks the public profile -> link saved,
Traveler role given, Unverified removed, nickname set to the Roblox name.
No keys or OAuth needed. Members already verified by Bloxlink keep their roles.
"""
from __future__ import annotations

import random

import discord
from discord.ext import commands

from .. import db, ui
from ..core import (cfg, channel, embed, log_staff, role, role_named, roblox_headshot,
                    roblox_user, roblox_user_by_name)
from ..panels import drop_panel, ensure_panel

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
    roles_ch = channel(member.guild, "welcome")
    if roles_ch:
        e.add_field(name="Next", value=f"Pick your ping roles in {roles_ch.mention}.", inline=False)
    await interaction.followup.send(embed=e, ephemeral=True)
    await log_staff(member.guild, f"Verified {member.mention} as **{roblox_name}** (`{roblox_id}`)",
                    kind="Verifications")
    await welcome_dm(member)
    interaction.client.dispatch("roblox_linked", member, roblox_id)


async def welcome_dm(member: discord.Member) -> None:
    """One short DM after verifying, with the places worth knowing."""
    g, game = member.guild, cfg()["game"]
    spots = [(k, label) for k, label in (("welcome", "pick your pings"), ("support", "get help"),
                                          ("chat", "say hi"), ("rp_servers", "find a player airline"))
             if channel(g, k)]
    lines = [f"{channel(g, k).mention}: {label}" for k, label in spots]
    e = embed(f"Welcome to {game['name']}, {member.display_name}!",
              "You're all set. A few places to start:\n" + "\n".join(lines) +
              f"\n\n[Play the game]({game['link']})")
    try:
        await member.send(embed=e)
    except discord.HTTPException:
        pass


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


async def start_verify(interaction: discord.Interaction):
    """Verify button and /help both land here."""
    link = db.one("SELECT * FROM links WHERE discord_id=?", interaction.user.id)
    if link:
        await interaction.response.defer(ephemeral=True, thinking=True)
        problems = await apply_verified(interaction.user, link["roblox_id"], link["roblox_name"])
        msg = f"You're already verified as **{link['roblox_name']}**."
        return await interaction.followup.send("\n".join([msg, *problems]), ephemeral=True)
    verified = role(interaction.guild, "verified")
    if verified and verified in interaction.user.roles:   # verified earlier, e.g. through Bloxlink
        return await interaction.response.send_message(
            "You're already verified, nothing to do. You can still link your Roblox account here so "
            "support can see it in your tickets.", view=LinkAnywayView(), ephemeral=True)
    await interaction.response.send_modal(UsernameModal())


class WelcomeView(discord.ui.View):
    """The one message in rules-and-verify: Verify, help and links on top, role menus below."""

    def __init__(self, guild: discord.Guild | None = None):
        super().__init__(timeout=None)
        sup = channel(guild, "support") if guild else None
        if sup:
            self.add_item(discord.ui.Button(label="Get help", url=sup.jump_url, row=0))
        for label, url in (cfg()["welcome"].get("links") or {}).items():
            self.add_item(discord.ui.Button(label=label, url=url, row=0))
        for i, (group, (key, _, _)) in enumerate(ROLE_GROUPS.items(), start=1):
            if cfg().get(key):
                sel = RoleGroupSelect(group)
                sel.row = i
                self.add_item(sel)

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, custom_id="asbot:verify:start", row=0)
    async def verify(self, interaction: discord.Interaction, _):
        await start_verify(interaction)


def welcome_card() -> discord.Embed:
    w = cfg()["welcome"]
    rules = "\n".join(f"`{i}`  {r}" for i, r in enumerate(w["rules"], 1))
    return ui.card(w["title"], f"{w['intro']}\n\n**Rules**\n{rules}\n\n"
                               "**1.** Press **Verify** and link Roblox (under a minute)\n"
                               "**2.** Pick your pings and platform below", tone="brand")


class LinkAnywayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Link my Roblox account", style=discord.ButtonStyle.secondary)
    async def link(self, interaction: discord.Interaction, _):
        await interaction.response.send_modal(UsernameModal())


# Role menus under the verify panel in rules-and-verify. "pings" lets you pick several, "platform" one.
ROLE_GROUPS = {"pings": ("ping_roles", "Choose what pings you", True),
               "platform": ("platform_roles", "What do you play on?", False)}


class RoleGroupSelect(discord.ui.Select):
    def __init__(self, group: str):
        key, placeholder, multi = ROLE_GROUPS[group]
        self.group, self.items = group, cfg().get(key, [])
        opts = [discord.SelectOption(label=p["label"], description=p.get("description"), value=p["role"])
                for p in self.items]
        super().__init__(placeholder=placeholder, min_values=0, max_values=len(opts) if multi else 1,
                         options=opts, custom_id=f"asbot:roles:{group}")

    async def callback(self, interaction: discord.Interaction):
        member, chosen = interaction.user, set(self.values)
        add, remove = [], []
        for p in self.items:
            r = role(member.guild, p["role"])
            if r is None:
                continue
            if p["role"] in chosen and r not in member.roles:
                add.append(r)
            elif p["role"] not in chosen and r in member.roles:
                remove.append(r)
        try:
            if add:
                await member.add_roles(*add, reason="roles panel")
            if remove:
                await member.remove_roles(*remove, reason="roles panel")
        except discord.Forbidden:
            await log_staff(member.guild, "Roles panel: my role is below a menu role. Move it up.")
            return await interaction.response.send_message("I can't give that role right now. Staff have been told.",
                                                           ephemeral=True)
        now = [p["label"] for p in self.items if p["role"] in chosen]
        if self.group == "pings":
            text = ("You'll get pings for: " + ", ".join(now)) if now else "You won't get any pings."
        else:
            text = f"Platform set to {now[0]}." if now else "Platform cleared."
        await interaction.response.send_message(text, ephemeral=True)


class Verify(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(WelcomeView())
        bot.add_view(CheckView())

    @commands.Cog.listener()
    async def on_ready(self):
        guild = self.bot.get_guild(self.bot.guild_id)
        if guild is None:
            return
        await ensure_panel("welcome", channel(guild, "welcome"), view=WelcomeView(guild), embeds=[welcome_card()])
        await drop_panel(guild, "roles")   # roles used to be a second message; now part of the welcome one

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        link = db.one("SELECT * FROM links WHERE discord_id=?", member.id)
        if link:   # rejoined: verified again straight away
            await apply_verified(member, link["roblox_id"], link["roblox_name"])

    # ---------- staff log threads: joins/leaves and moderation ----------
    @commands.Cog.listener("on_member_join")
    async def _log_join(self, member: discord.Member):
        age = (discord.utils.utcnow() - member.created_at).days
        await log_staff(member.guild, f"📥 {member.mention} joined · account {age} days old"
                                      + (" ⚠️ new account" if age < 7 else ""), kind="Joins and leaves")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await log_staff(member.guild, f"📤 {member} left", kind="Joins and leaves")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        await log_staff(guild, f"🔨 {user} was banned", kind="Moderation")

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        await log_staff(guild, f"♻️ {user} was unbanned", kind="Moderation")

    @commands.Cog.listener("on_member_update")
    async def _log_timeout(self, before: discord.Member, after: discord.Member):
        if before.timed_out_until != after.timed_out_until:
            if after.timed_out_until:
                await log_staff(after.guild, f"🔇 {after.mention} timed out until "
                                             f"<t:{int(after.timed_out_until.timestamp())}:f>", kind="Moderation")
            else:
                await log_staff(after.guild, f"🔈 {after.mention} timeout removed", kind="Moderation")


async def setup(bot):
    await bot.add_cog(Verify(bot))
