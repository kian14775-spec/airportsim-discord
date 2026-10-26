"""Instant answers, the quick way: /answer add (or "Save fix" on a ticket) asks for a few
trigger words and the fix in rough notes; the bot writes the full answer, shows a preview,
and one click saves it. /answer list | edit | remove | test for the rest.

No AI is needed: faq.write_answer turns notes into sentences. If a free Gemini key is set,
ai.polish rewrites the wording a bit nicer first (see asbot/ai.py).
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from .. import ai, db, faq, ui
from ..core import is_staff, log_staff, staff_only

STOPWORDS = {"the", "and", "you", "your", "for", "this", "that", "with", "have", "please", "just", "then", "can",
             "will", "again", "game", "there", "they", "what", "when", "it's", "from", "into", "also", "help",
             "need", "doesn't", "dont", "don't", "cant", "can't", "how", "why", "does", "know", "want", "like"}


def guess_keywords(src: str) -> str:
    """Suggest a few trigger words from the member's problem text."""
    seen = []
    for w in faq.WORD.findall(src.lower()):
        if len(w) > 3 and w not in STOPWORDS and w not in seen:
            seen.append(w)
    return ", ".join(seen[:4])


def preview_card(keywords: str, answer: str, polished: bool) -> discord.Embed:
    how = "Wording polished by the free AI helper." if polished else "Written from your notes."
    return ui.card("New instant answer · preview", answer, tone="brand", fields=[
        ("Trigger words", keywords, False),
        ("What happens", f"Members whose problem mentions these words get this fix first, before a person "
                         f"replies. {how}", False)])


class PreviewView(discord.ui.View):
    def __init__(self, keywords: str, answer: str, ticket_id: int | None = None):
        super().__init__(timeout=900)
        self.keywords, self.answer, self.ticket_id = keywords, answer, ticket_id

    @discord.ui.button(label="Save", style=discord.ButtonStyle.success)
    async def save(self, interaction: discord.Interaction, _):
        fid = faq.add(self.keywords, self.answer, interaction.user.id)
        await interaction.response.edit_message(
            content=f"Saved as instant answer **#{fid}**.", embed=preview_card(self.keywords, self.answer, False),
            view=None)
        where = f" (from ticket {self.ticket_id:04d})" if self.ticket_id else ""
        await log_staff(interaction.guild, f"Instant answer #{fid} added by {interaction.user.mention}{where}: "
                                           f"{self.keywords}", kind="Tickets")

    @discord.ui.button(label="Change wording", style=discord.ButtonStyle.secondary)
    async def edit(self, interaction: discord.Interaction, _):
        await interaction.response.send_modal(WordingModal(self))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _):
        await interaction.response.edit_message(content="Not saved.", embed=None, view=None)


class WordingModal(discord.ui.Modal, title="Change the wording"):
    keywords = discord.ui.TextInput(label="Trigger words (comma separated)", max_length=200)
    answer = discord.ui.TextInput(label="Answer", style=discord.TextStyle.paragraph, max_length=1500)

    def __init__(self, preview: PreviewView):
        super().__init__()
        self.preview = preview
        self.keywords.default, self.answer.default = preview.keywords, preview.answer

    async def on_submit(self, interaction: discord.Interaction):
        self.preview.keywords, self.preview.answer = self.keywords.value, self.answer.value
        await interaction.response.edit_message(embed=preview_card(self.keywords.value, self.answer.value, False),
                                                view=self.preview)


class AnswerModal(discord.ui.Modal, title="New instant answer"):
    keywords = discord.ui.TextInput(label="Trigger words (a few, comma separated)", max_length=200,
                                    placeholder="livery, template, paint")
    notes = discord.ui.TextInput(label="The fix (rough notes are fine)", style=discord.TextStyle.paragraph,
                                 max_length=1200, placeholder="open templates post in liveries, download, "
                                                              "paint in any editor, upload as decal")

    def __init__(self, keywords: str = "", notes: str = "", ticket_id: int | None = None):
        super().__init__()
        self.keywords.default, self.notes.default = keywords[:200], notes[:1200]
        self.ticket_id = ticket_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        polished = await ai.polish(self.keywords.value, self.notes.value)
        answer = polished or faq.write_answer(self.notes.value)
        clash = [faq.label(e) for e in faq.ranked(self.keywords.value)[:2]]
        note = f"Heads up, these answers use similar words: {', '.join(clash)}" if clash else None
        await interaction.followup.send(content=note, embed=preview_card(self.keywords.value, answer, bool(polished)),
                                        view=PreviewView(self.keywords.value, answer, self.ticket_id), ephemeral=True)


class EditModal(discord.ui.Modal, title="Edit instant answer"):
    keywords = discord.ui.TextInput(label="Trigger words (comma separated)", max_length=200)
    answer = discord.ui.TextInput(label="Answer", style=discord.TextStyle.paragraph, max_length=1500)

    def __init__(self, row):
        super().__init__()
        self.fid = row["id"]
        self.keywords.default, self.answer.default = row["keywords"], row["answer"]

    async def on_submit(self, interaction: discord.Interaction):
        faq.update(self.fid, self.keywords.value, self.answer.value)
        await interaction.response.send_message(f"Answer #{self.fid} updated.", ephemeral=True)


def list_card() -> discord.Embed:
    rows = db.q("SELECT * FROM faq ORDER BY hits DESC, id DESC")
    if not rows:
        return ui.card("Instant answers", "None saved yet. Add one with `/answer add` or the **Save fix** button "
                                          "on a ticket.")
    lines = [f"`#{r['id']}` **{r['keywords']}** · used {r['hits']}x\n{r['answer'][:90]}" for r in rows[:15]]
    more = f"\n\n-# +{len(rows) - 15} more" if len(rows) > 15 else ""
    return ui.card("Instant answers", "\n\n".join(lines) + more,
                   fields=[("Add or change", "`/answer add` · `/answer edit` · `/answer remove` · `/answer test`",
                            False)])


class Answers(commands.Cog):
    group = app_commands.Group(name="answer", description="Instant answers (staff)",
                               default_permissions=discord.Permissions(moderate_members=True))

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @group.command(name="add", description="Add an instant answer: a few trigger words + the fix")
    @staff_only()
    async def add(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AnswerModal())

    @group.command(name="list", description="Every saved instant answer and how often it's used")
    @staff_only()
    async def list_(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=list_card(), ephemeral=True)

    @group.command(name="edit", description="Change an instant answer")
    @staff_only()
    async def edit(self, interaction: discord.Interaction, number: int):
        row = faq.get(number)
        if not row:
            return await interaction.response.send_message(f"No answer #{number}.", ephemeral=True)
        await interaction.response.send_modal(EditModal(row))

    @group.command(name="remove", description="Delete an instant answer")
    @staff_only()
    async def remove(self, interaction: discord.Interaction, number: int):
        if not faq.get(number):
            return await interaction.response.send_message(f"No answer #{number}.", ephemeral=True)
        faq.remove(number)
        await interaction.response.send_message(f"Answer #{number} removed.", ephemeral=True)
        await log_staff(interaction.guild, f"Instant answer #{number} removed by {interaction.user.mention}",
                        kind="Tickets")

    @group.command(name="test", description="Type a problem like a member would: see which answer they'd get")
    @staff_only()
    async def test(self, interaction: discord.Interaction, problem: str):
        found = faq.ranked(problem)
        if not found:
            return await interaction.response.send_message(
                "Nothing matches, so this would go straight to a person. Add one with `/answer add`.",
                ephemeral=True)
        best = found[0]
        rest = ", ".join(faq.label(e) for e in found[1:4])
        await interaction.response.send_message(embed=ui.card(
            f"They'd get {faq.label(best)}", best["answer"], tone="brand",
            fields=[("Next if that doesn't fix it", rest, False)] if rest else None), ephemeral=True)

    @edit.autocomplete("number")
    @remove.autocomplete("number")
    async def _numbers(self, interaction: discord.Interaction, current: str):
        if not is_staff(interaction.user):
            return []
        rows = db.q("SELECT id, keywords FROM faq ORDER BY id DESC")
        return [app_commands.Choice(name=f"#{r['id']} · {r['keywords']}"[:100], value=r["id"])
                for r in rows if current.lower() in f"{r['id']} {r['keywords']}".lower()][:25]


async def setup(bot):
    await bot.add_cog(Answers(bot))
