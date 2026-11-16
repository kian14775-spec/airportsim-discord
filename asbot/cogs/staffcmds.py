"""Quick `?` commands for staff, typed straight into a ticket post or a staff channel.

Only staff, only in channels members can't see (a member typing "?" never triggers anything).
Commands typed in a ticket post are never relayed to the member. `?help` lists them.
"""
from __future__ import annotations

import discord
from discord.ext import commands

from .. import db, faq, ticketlist, transcripts, ui
from ..core import cfg, is_staff, log_staff, role

# name -> (usage, what it does, ticket post only)
COMMANDS = {
    "close": ("?close", "Close now and save the chat", True),
    "ask": ("?ask", "Ask the member if it's solved (closes when they confirm, or in 24 h)", True),
    "delete": ("?delete", "Delete a spam/troll ticket without saving the chat", True),
    "claim": ("?claim", "Claim this ticket", True),
    "unclaim": ("?unclaim", "Let someone else take it", True),
    "urgent": ("?urgent", "Mark urgent and ping the Support Team", True),
    "low": ("?low", "Set priority to low", True),
    "normal": ("?normal", "Set priority back to normal", True),
    "dev": ("?dev", "Send to the dev team (pings Developer)", True),
    "takeover": ("?takeover", "Take over from the Support Agent", True),
    "r": ("?r <name>", "Send a saved reply (?r alone lists them)", True),
    "history": ("?history", "This member's past tickets", True),
    "tickets": ("?tickets [closed|mine|words]", "Browse every ticket", False),
    "t": ("?t <number>", "Open one ticket: jump to it, or read its transcript", False),
    "answer": ("?answer trigger, words | the fix", "Save an instant answer in one line", False),
    "help": ("?help", "This list", False),
}


def staff_place(ch) -> bool:
    """Only staff can read it: neither @everyone nor verified members (the `verified` role) can see the channel."""
    g = ch.guild
    return not any(r and ch.permissions_for(r).view_channel for r in (g.default_role, role(g, "verified")))


def help_card() -> discord.Embed:
    post = "\n".join(f"`{u}` · {d}" for u, d, p in COMMANDS.values() if p)
    anywhere = "\n".join(f"`{u}` · {d}" for u, d, p in COMMANDS.values() if not p)
    return ui.card("Staff commands", fields=[("In a ticket post", post, False),
                                             ("Any staff channel", anywhere, False)],
                   footer="Start a message with // for a staff-only note")


class Button(discord.ui.View):
    """A one-off button that opens something privately (message commands can't reply privately)."""

    def __init__(self, label: str, fn):
        super().__init__(timeout=600)
        b = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)

        async def cb(interaction: discord.Interaction):
            if not is_staff(interaction.user):
                return await interaction.response.send_message("Staff only.", ephemeral=True)
            await fn(interaction)
        b.callback = cb
        self.add_item(b)


class DeleteConfirm(discord.ui.View):
    def __init__(self, tid: int, by: int):
        super().__init__(timeout=120)
        self.tid, self.by = tid, by

    @discord.ui.button(label="Delete for good", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, _):
        if interaction.user.id != self.by:
            return await interaction.response.send_message("Only whoever typed ?delete can confirm.", ephemeral=True)
        from .tickets import ticket
        await interaction.response.edit_message(content="Deleting...", view=None)
        await interaction.client.get_cog("Tickets").close(ticket(self.tid), by=interaction.user,
                                                          reason=f"deleted by {interaction.user.display_name}",
                                                          save=False)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, _):
        await interaction.response.edit_message(content="Not deleted.", view=None)


class StaffCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        if msg.author.bot or not msg.guild or not msg.content.startswith("?"):
            return
        name, _, rest = msg.content[1:].partition(" ")
        name, rest = name.lower(), rest.strip()
        if name not in COMMANDS or not is_staff(msg.author):
            return
        if not staff_place(msg.channel):
            return   # members can read it: stay quiet, commands are for staff-only places
        from .tickets import ticket_for_thread
        t = ticket_for_thread(msg.channel.id) if isinstance(msg.channel, discord.Thread) else None
        if COMMANDS[name][2] and (not t or t["status"] == "closed"):
            return await msg.reply(f"`?{name}` works inside an open ticket post.", mention_author=False,
                                   delete_after=15)
        try:
            await getattr(self, f"c_{name}")(msg, t, rest)
        except discord.HTTPException as e:
            await msg.reply(f"Couldn't do that: {e.text or e}", mention_author=False)

    async def _done(self, msg: discord.Message, text: str | None = None):
        await msg.add_reaction("✅")
        if text:
            await msg.reply(text, mention_author=False, allowed_mentions=discord.AllowedMentions.none())

    # ---- ticket post ----
    async def c_close(self, msg, t, rest):
        await self._done(msg)
        await self.bot.get_cog("Tickets").close(t, by=msg.author, reason=f"closed by {msg.author.display_name}"
                                                + (f": {rest}" if rest else ""))

    async def c_ask(self, msg, t, rest):
        await self._done(msg, "Asked the member if it's solved.")
        await self.bot.get_cog("Tickets").ask_close(t, msg.author)

    async def c_delete(self, msg, t, rest):
        await msg.reply("Delete this ticket? The chat **won't** be stored and the member gets no rating "
                        "message. Use it for spam or troll tickets.", view=DeleteConfirm(t["id"], msg.author.id),
                        mention_author=False)

    async def c_claim(self, msg, t, rest):
        db.run("UPDATE tickets SET claimed_by=? WHERE id=?", msg.author.id, t["id"])
        await self._done(msg)
        await self.bot.get_cog("Tickets").refresh(t["id"])

    async def c_unclaim(self, msg, t, rest):
        db.run("UPDATE tickets SET claimed_by=NULL WHERE id=?", t["id"])
        await self._done(msg)
        await self.bot.get_cog("Tickets").refresh(t["id"])

    async def _priority(self, msg, t, level: str):
        db.run("UPDATE tickets SET priority=? WHERE id=?", level, t["id"])
        if level == "urgent":
            from ..core import role_named
            team = role_named(msg.guild, "Support Team")
            await msg.reply(f"🔴 Marked **urgent** by {msg.author.mention}. {team.mention if team else ''}",
                            mention_author=False, allowed_mentions=discord.AllowedMentions(roles=True, users=False))
        await msg.add_reaction("✅")
        await self.bot.get_cog("Tickets").refresh(t["id"])

    async def c_urgent(self, msg, t, rest):
        await self._priority(msg, t, "urgent")

    async def c_low(self, msg, t, rest):
        await self._priority(msg, t, "low")

    async def c_normal(self, msg, t, rest):
        await self._priority(msg, t, "normal")

    async def c_dev(self, msg, t, rest):
        from ..core import role_named
        db.kv_set(f"ticket_dev:{t['id']}", True)
        dev = role_named(msg.guild, cfg().get("dev_role_name", "Developer"))
        await msg.reply(f"{dev.mention if dev else 'Dev team'}: {msg.author.display_name} needs you on this one.",
                        mention_author=False, allowed_mentions=discord.AllowedMentions(roles=True))
        await self.bot.get_cog("Tickets").refresh(t["id"])

    async def c_takeover(self, msg, t, rest):
        if t["status"] != "bot":
            return await msg.reply("The Support Agent isn't on this one.", mention_author=False, delete_after=15)
        db.run("UPDATE tickets SET claimed_by=COALESCE(claimed_by, ?) WHERE id=?", msg.author.id, t["id"])
        await self._done(msg)
        await self.bot.get_cog("Tickets").handoff(t, "taken over", by=msg.author)

    async def c_r(self, msg, t, rest):
        snippets = cfg()["support"]["snippets"]
        text = snippets.get(rest.lower())
        if not text:
            names = " · ".join(f"`{k}`" for k in snippets)
            return await msg.reply(f"Saved replies: {names}\nUse `?r <name>`.", mention_author=False)
        tickets = self.bot.get_cog("Tickets")
        if t["status"] == "bot":
            await tickets.handoff(t, "taken over", by=msg.author)
        ok = await tickets.relay_staff(t, msg.author, text, [])
        await msg.add_reaction("✅" if ok else "❌")
        await msg.reply(f"Sent: {text}" if ok else "Couldn't DM the member.", mention_author=False)

    async def c_history(self, msg, t, rest):
        rows = db.q("SELECT * FROM tickets WHERE user_id=? AND status='closed' ORDER BY id DESC", t["user_id"])
        if not rows:
            return await msg.reply("No past tickets from them.", mention_author=False)
        await msg.reply(f"{len(rows)} past ticket(s):", view=transcripts.pick_list(rows), mention_author=False)

    # ---- anywhere staff-only ----
    async def c_tickets(self, msg, t, rest):
        tab = rest.lower() if rest.lower() in ("open", "closed", "mine") else ("search" if rest else "open")
        await msg.reply("Your ticket list opens privately:", mention_author=False, view=Button(
            "Open ticket list", lambda i: ticketlist.send(i, tab, rest if tab == "search" else "")))

    async def c_t(self, msg, t, rest):
        from .tickets import summary_card, ticket
        num = rest.lstrip("#")
        found = ticket(int(num)) if num.isdigit() else None
        if not found:
            return await msg.reply("Use `?t <number>`, like `?t 142`.", mention_author=False)
        if found["status"] != "closed":
            return await msg.reply(f"Ticket {found['id']:04d} is open: <#{found['thread_id']}>", mention_author=False)
        await msg.reply(embed=summary_card(found), mention_author=False, view=Button(
            "Read transcript", lambda i: transcripts.open_viewer(i, found["id"])))

    async def c_answer(self, msg, t, rest):
        words, _, notes = rest.partition("|")
        if not words.strip() or not notes.strip():
            return await msg.reply("Use `?answer trigger, words | the fix in rough notes`\n"
                                   "Example: `?answer livery, template | open the templates post in liveries, "
                                   "download, paint, upload as decal`", mention_author=False)
        answer = faq.write_answer(notes)
        fid = faq.add(words, answer, msg.author.id)
        await msg.reply(embed=ui.card(f"Saved instant answer #{fid}", answer, tone="brand",
                                      fields=[("Trigger words", words.strip(), False)],
                                      footer="Wrong? /answer edit or /answer remove"), mention_author=False)
        await log_staff(msg.guild, f"Instant answer #{fid} added by {msg.author.mention}: {words.strip()}",
                        kind="Tickets")

    async def c_help(self, msg, t, rest):
        await msg.reply(embed=help_card(), mention_author=False)


async def setup(bot):
    await bot.add_cog(StaffCommands(bot))
