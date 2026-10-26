"""/profile: a member's card to show off. Roblox account, how long they've been around, their
liveries and wins, their airline, ideas, and badge roles. Also right-click a member -> Apps -> Profile.
Everything comes from data the bot already has or free public APIs (Roblox web API).
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from .. import cards, db, ui
from ..core import (cfg, channel, linked_roblox, log, roblox_group_rank, roblox_headshot, roblox_user, role,
                    staff_roles)

BADGES = ["Livery of the Month", "Server Booster", "Airport VIP", "Star Program", "QA", "PR Team"]
PLATFORMS = ["PC", "Mobile", "Console", "VR"]


async def _count_threads(forum, uid: int, tag: str | None = None) -> int:
    if not isinstance(forum, discord.ForumChannel):
        return 0
    threads = list(forum.threads)
    try:
        async for th in forum.archived_threads(limit=100):
            threads.append(th)
    except discord.HTTPException:
        pass
    return sum(1 for th in threads if th.owner_id == uid and (not tag or tag in {t.name for t in th.applied_tags}))


async def profile_card(member: discord.Member) -> cards.CardView:
    g = member.guild
    lines = []
    thumb = None
    try:
        found = await linked_roblox(g, member)
    except Exception as e:  # noqa: BLE001 - Roblox being slow must not break the card
        log.warning("profile roblox: %s", e)
        found = None
    if found:
        rid, rname, how = found
        info = await roblox_user(rid) or {}
        rank = await roblox_group_rank(rid, cfg()["game"]["group_id"])
        thumb = await roblox_headshot(rid)
        display = info.get("displayName") or rname
        name = rname if display == rname else f"{display} (@{rname})"
        tick = " ✅" if how != "from their nickname" else ""
        lines.append(f"**Roblox** [{name}](https://www.roblox.com/users/{rid}/profile){tick}")
        bits = [f"group: {rank or 'not in the group'}"]
        if info.get("created"):
            bits.append(f"on Roblox since {info['created'][:4]}")
        lines.append("-# " + " · ".join(bits))
    else:
        lines.append("**Roblox** not linked yet · press **Verify** in rules-and-verify")
    if member.joined_at:
        lines.append(f"**In the server since** <t:{int(member.joined_at.timestamp())}:D>")
    plat = next((r.name for r in member.roles if r.name in PLATFORMS), None)
    if plat:
        lines.append(f"**Plays on** {plat}")
    head = cards.section(member.display_name, "\n".join(lines))
    top = discord.ui.Section(head, accessory=discord.ui.Thumbnail(thumb or member.display_avatar.url))

    liveries = await _count_threads(channel(g, "liveries"), member.id, "Showcase")
    ideas = await _count_threads(channel(g, "feedback"), member.id)
    wins = db.kv_get(f"livery_wins:{member.id}", 0)
    airline = db.one("SELECT thread_id FROM rp_posts WHERE owner_id=?", member.id)
    stats = [f"**{liveries}** livery post{'s' if liveries != 1 else ''}",
             f"**{wins}** Livery of the Month win{'s' if wins != 1 else ''}",
             f"**{ideas}** idea{'s' if ideas != 1 else ''} or bug report{'s' if ideas != 1 else ''}"]
    if airline:
        stats.append(f"Runs <#{airline['thread_id']}>")

    names = {r.name for r in member.roles}
    staff = next((r.name for r in sorted(member.roles, reverse=True) if r in staff_roles(g)), None)
    badges = ([f"🛠️ {staff}"] if staff else []) + [b for b in BADGES if b in names]
    if member.premium_since and "Server Booster" not in names:
        badges.append("Server Booster")
    winner = role(g, "livery_winner")
    accent = 0xF1C40F if winner and winner in member.roles else ui.BRAND
    return cards.CardView(cards.container(
        top,
        cards.divider(),
        cards.section("In the community", " · ".join(stats)),
        cards.section("Badges", " · ".join(badges)) if badges else None,
        accent=accent), timeout=None)


class Profile(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.menu = app_commands.ContextMenu(name="Profile", callback=self.profile_menu)
        bot.tree.add_command(self.menu)

    def cog_unload(self):
        self.bot.tree.remove_command(self.menu.name, type=self.menu.type)

    @app_commands.command(name="profile", description="Show your Airport Simulator profile (or someone else's)")
    async def profile(self, interaction: discord.Interaction, member: discord.Member | None = None):
        await interaction.response.defer(thinking=True)
        await interaction.followup.send(view=await profile_card(member or interaction.user),
                                        allowed_mentions=discord.AllowedMentions.none())

    async def profile_menu(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(view=await profile_card(member), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Profile(bot))
