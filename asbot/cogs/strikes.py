"""Strikes: staff warn a member, the bot escalates by itself.

Right-click a member or a message -> Apps -> "Warn", or /staff -> Warn a member.
Active strikes (last 30 days) decide the step: 1 = warning DM, 2 = 1 h timeout,
3 = 24 h timeout, 4+ = 7 day timeout and staff are asked to consider a ban.
Every strike is logged in Moderation; members can appeal through support.
"""
from __future__ import annotations

import datetime as dt

import discord
from discord import app_commands
from discord.ext import commands

from .. import db, ui
from ..core import channel, is_staff, log_staff

db.conn().executescript("""
CREATE TABLE IF NOT EXISTS strikes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    reason TEXT NOT NULL, by_id INTEGER NOT NULL, created REAL NOT NULL, removed INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS strikes_user ON strikes(user_id);
""")

WINDOW_DAYS = 30
STEPS = {1: None, 2: dt.timedelta(hours=1), 3: dt.timedelta(hours=24)}
MAX_STEP = dt.timedelta(days=7)


def active(user_id: int):
    return db.q("SELECT * FROM strikes WHERE user_id=? AND removed=0 AND created>? ORDER BY id",
                user_id, db.now() - WINDOW_DAYS * 86400)


def step_text(n: int) -> str:
    t = STEPS.get(n, MAX_STEP)
    if t is None:
        return "a warning"
    hours = int(t.total_seconds() // 3600)
    return f"a {hours} hour timeout" if hours < 48 else f"a {hours // 24} day timeout"


async def give_strike(interaction: discord.Interaction, member: discord.Member, reason: str, context: str = ""):
    if member.bot or is_staff(member):
        return await interaction.response.send_message("You can't strike bots or staff.", ephemeral=True)
    db.run("INSERT INTO strikes(user_id, reason, by_id, created) VALUES(?, ?, ?, ?)",
           member.id, reason, interaction.user.id, db.now())
    n = len(active(member.id))
    timeout = STEPS.get(n, MAX_STEP)
    note = ""
    if timeout:
        try:
            await member.timeout(timeout, reason=f"strike {n}: {reason}")
        except discord.HTTPException:
            note = " (couldn't time them out: their role is above mine)"
    sup = channel(member.guild, "support")
    dm = ui.card(f"You've received a strike ({n}/{len(STEPS) + 1})",
                 ui.kv(("Reason", reason), ("Result", step_text(n)),
                       ("Expires", f"<t:{int(db.now() + WINDOW_DAYS * 86400)}:R>")) +
                 (f"\n\nThink it's wrong? Appeal in {sup.mention}." if sup else ""), tone="wait" if n < 3 else "bad")
    try:
        await member.send(embed=dm)
    except discord.HTTPException:
        note += " (DMs closed)"
    await interaction.response.send_message(
        f"Strike {n} for {member.mention}: {step_text(n)}.{note}", ephemeral=True)
    extra = " · ⚠️ 4+ strikes: consider a ban" if n > len(STEPS) else ""
    await log_staff(member.guild, f"⚠️ Strike {n} for {member.mention} by {interaction.user.mention}: {reason}"
                                  f" → {step_text(n)}{extra}" + (f"\n> {context[:300]}" if context else ""),
                    kind="Moderation")


class StrikeModal(discord.ui.Modal, title="Warn member"):
    reason = discord.ui.TextInput(label="Reason (the member sees this)", max_length=300,
                                  placeholder="Spamming in general")

    def __init__(self, member: discord.Member, context: str = ""):
        super().__init__()
        self.member, self.context = member, context

    async def on_submit(self, interaction: discord.Interaction):
        await give_strike(interaction, self.member, self.reason.value, self.context)


class RemoveStrike(discord.ui.Select):
    def __init__(self, rows):
        super().__init__(placeholder="Remove a strike", options=[
            discord.SelectOption(label=f"#{r['id']} · {r['reason'][:80]}", value=str(r["id"])) for r in rows[:25]])

    async def callback(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("Staff only.", ephemeral=True)
        db.run("UPDATE strikes SET removed=1 WHERE id=?", int(self.values[0]))
        await interaction.response.send_message(f"Strike #{self.values[0]} removed.", ephemeral=True)
        await log_staff(interaction.guild, f"Strike #{self.values[0]} removed by {interaction.user.mention}",
                        kind="Moderation")


def strikes_line(user_id: int) -> str:
    rows = active(user_id)
    if not rows:
        return "None"
    return f"{len(rows)} active · last: {rows[-1]['reason'][:60]}"


class Strikes(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.menus = [app_commands.ContextMenu(name="Warn", callback=self.warn_member),
                      app_commands.ContextMenu(name="Warn for this message", callback=self.warn_message)]
        for m in self.menus:
            m.default_permissions = discord.Permissions(moderate_members=True)
            bot.tree.add_command(m)

    def cog_unload(self):
        for m in self.menus:
            self.bot.tree.remove_command(m.name, type=m.type)

    async def warn_member(self, interaction: discord.Interaction, member: discord.Member):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("Staff only.", ephemeral=True)
        await interaction.response.send_modal(StrikeModal(member))

    async def warn_message(self, interaction: discord.Interaction, message: discord.Message):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("Staff only.", ephemeral=True)
        member = interaction.guild.get_member(message.author.id)
        if not member:
            return await interaction.response.send_message("They're not in the server any more.", ephemeral=True)
        await interaction.response.send_modal(StrikeModal(member, f"{message.content} ({message.jump_url})"))


async def setup(bot):
    await bot.add_cog(Strikes(bot))
