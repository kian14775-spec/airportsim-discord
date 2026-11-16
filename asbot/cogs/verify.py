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

from .. import cards, db, timeline, ui
from ..core import (cfg, channel, embed, log_card, log_staff, role, role_named, roblox_headshot,
                    roblox_user, roblox_user_by_name)
from ..panels import drop_panel

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
    timeline.add(member.id, "member", f"Verified as {roblox_name}")
    await log_staff(member.guild, "", kind="Verifications", event="verified", embed=log_card(
        "Verified", f"{member.mention} as [{roblox_name}](https://www.roblox.com/users/{roblox_id}/profile) (`{roblox_id}`)",
        tone="good", user=member))
    await welcome_dm(member)
    interaction.client.dispatch("roblox_linked", member, roblox_id)


async def welcome_dm(member: discord.Member) -> None:
    """One short DM after verifying, with the places worth knowing."""
    g, game = member.guild, cfg()["game"]
    spots = [(k, label) for k, label in (("welcome", "pick your pings"), ("support", "get help"),
                                          ("chat", "say hi"), ("rp_servers", "find a player airline"))
             if channel(g, k)]
    lines = [f"{channel(g, k).mention} · {label}" for k, label in spots]
    view = cards.CardView(cards.container(
        cards.title(f"Welcome, {member.display_name}", "You're verified and all set."),
        cards.divider(),
        cards.section("A few places to start", "\n".join(lines)),
        cards.row(discord.ui.Button(label="Play the game", url=game["link"])),
        accent=ui.BRAND, banner_name="welcome"))
    try:
        await member.send(view=view, files=cards.files_for("welcome"))
    except discord.HTTPException:
        pass


class UsernameModal(discord.ui.Modal, title="Verify with Roblox"):
    username = discord.ui.TextInput(label="Your Roblox username", min_length=3, max_length=20,
                                    placeholder="Builderman")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        user = await roblox_user_by_name(self.username.value.strip())
        if not user:
            return await interaction.followup.send("I couldn't find that Roblox username. Check the spelling, or "
                                                   "try again in a minute if Roblox is slow.", ephemeral=True)
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


class VerifyButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Verify", style=discord.ButtonStyle.primary, custom_id="asbot:verify:start")

    async def callback(self, interaction: discord.Interaction):
        await start_verify(interaction)


class HelpButton(discord.ui.Button):
    """Opens /help privately. A link to #support can't work here: unverified members can't see it yet."""

    def __init__(self):
        super().__init__(label="Get help", style=discord.ButtonStyle.secondary, custom_id="asbot:welcome:help")

    async def callback(self, interaction: discord.Interaction):
        from .hub import help_card
        await interaction.response.send_message(view=help_card(), files=cards.files_for("help"), ephemeral=True)


class WelcomeCard(cards.CardView):
    """rules-and-verify, one message: the welcome card, then one card that reads top to bottom:
    rules -> get started (verify + help) -> pings and platform menus."""

    def __init__(self, guild: discord.Guild | None = None):
        w = cfg()["welcome"]
        links = [discord.ui.Button(label=label, url=url) for label, url in (w.get("links") or {}).items()]
        rules = "\n".join(f"**{i}.** {r}" for i, r in enumerate(w["rules"], 1))
        menus = [cards.row(RoleGroupSelect(g)) for g, (key, _, _) in ROLE_GROUPS.items() if cfg().get(key)]
        super().__init__(
            cards.container(cards.title(w["title"], w["intro"]),
                            cards.row(*links) if links else None, banner_name="welcome"),
            cards.container(cards.title("Rules"), cards.text(rules),
                            cards.small("Breaking the rules can mean a strike, a timeout or a ban."),
                            cards.divider(big=True),
                            cards.title("Get started", "Two quick steps and the whole server opens up."),
                            cards.text("**1.** Press **Verify** and link your Roblox account\n"
                                       "**2.** Pick what pings you and what you play on"),
                            cards.row(VerifyButton(), HelpButton()),
                            cards.divider(),
                            *menus,
                            cards.small("You can change these any time."), banner_name="rules"))


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
        bot.add_view(WelcomeCard())
        bot.add_view(CheckView())

    @commands.Cog.listener()
    async def on_ready(self):
        guild = self.bot.get_guild(self.bot.guild_id)
        if guild is None:
            return
        await cards.post_card("welcome", channel(guild, "welcome"), WelcomeCard(guild),
                              banners=("welcome", "rules"))
        await drop_panel(guild, "roles")   # roles used to be a second message; now part of the welcome one

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        link = db.one("SELECT * FROM links WHERE discord_id=?", member.id)
        if link:   # rejoined: verified again straight away
            await apply_verified(member, link["roblox_id"], link["roblox_name"])

    # joins, leaves, bans and timeouts are logged in modlog.py / auditlog.py


async def setup(bot):
    await bot.add_cog(Verify(bot))
