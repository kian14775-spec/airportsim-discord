"""The ticket list: every ticket in one private, paged browser instead of searching.

Tabs: Open (urgent first, jump to the post) · Closed (newest first, pick one -> transcript) ·
Mine (claimed by you) · Search (number, member name or any word in the chat).
Opened from ?tickets, /ticket list, the "Browse tickets" button on the inbox guide and /staff.
"""
from __future__ import annotations

import discord

from . import db, transcripts, ui
from .core import is_staff

PER_PAGE = 10
TABS = {"open": "Open", "closed": "Closed", "mine": "Mine"}


def _name(t) -> str:
    p = db.kv_get(f"tcard:{t['id']}", {}).get("profile") or {}
    return p.get("name") or f"user {t['user_id']}"


def find(query: str, limit: int = 100):
    """Ticket number, member name/id, or any word in a closed ticket's chat."""
    q = query.strip().lstrip("#")
    if q.isdigit() and len(q) <= 6:
        return db.q("SELECT * FROM tickets WHERE id=?", int(q))
    if q.isdigit():
        return db.q("SELECT * FROM tickets WHERE user_id=? ORDER BY id DESC LIMIT ?", int(q), limit)
    by_name = db.q("SELECT t.* FROM tickets t JOIN kv ON kv.key = 'tcard:' || t.id "
                   "WHERE kv.value LIKE ? ORDER BY t.id DESC LIMIT ?", f'%"name": "{q}%', limit)
    seen = {r["id"] for r in by_name}
    return by_name + [r for r in transcripts.search(q, limit) if r["id"] not in seen]


class Browser(discord.ui.View):
    def __init__(self, who: int, tab: str = "open", query: str = ""):
        super().__init__(timeout=900)
        self.who, self.tab, self.query, self.page = who, tab, query, 0
        self.rebuild()

    # ---- data ----
    def rows(self):
        if self.tab == "open":
            return db.q("SELECT * FROM tickets WHERE status!='closed' "
                        "ORDER BY CASE priority WHEN 'urgent' THEN 0 ELSE 1 END, id")
        if self.tab == "closed":
            return db.q("SELECT * FROM tickets WHERE status='closed' ORDER BY COALESCE(closed, created) DESC, id DESC")
        if self.tab == "mine":
            return db.q("SELECT * FROM tickets WHERE claimed_by=? ORDER BY status='closed', id DESC", self.who)
        return find(self.query)

    def line(self, t) -> str:
        from .cogs.tickets import state_of, topic_label   # avoid an import loop
        if t["status"] != "closed":
            where = f" · <#{t['thread_id']}>" if t["thread_id"] else ""
            return (f"{state_of(t)[0]} **{t['id']:04d}** · {topic_label(t['topic'])} · {_name(t)} · "
                    f"<t:{int(t['created'])}:R>{where}")
        gone = " · 🗑️ deleted" if db.kv_get(f"tdeleted:{t['id']}") else ""
        rate = f" · {'★' * t['rating']}" if t["rating"] else ""
        by = f" · <@{t['claimed_by']}>" if t["claimed_by"] else ""
        return (f"⚫ **{t['id']:04d}** · {topic_label(t['topic'])} · {_name(t)} · closed "
                f"<t:{int(t['closed'] or t['created'])}:R>{by}{rate}{gone}")

    def card(self) -> discord.Embed:
        rows = self.rows()
        pages = max(1, -(-len(rows) // PER_PAGE))
        self.page = min(self.page, pages - 1)
        chunk = rows[self.page * PER_PAGE:(self.page + 1) * PER_PAGE]
        title = f"Tickets · {TABS.get(self.tab, 'Search')}" + (f" \"{self.query}\"" if self.tab == "search" else "")
        body = "\n".join(self.line(t) for t in chunk) or "Nothing here."
        return ui.card(title, body, tone="neutral", footer=f"{len(rows)} ticket(s) · page {self.page + 1}/{pages} · "
                                                           "pick one below to open it")

    # ---- controls ----
    def rebuild(self):
        self.clear_items()
        rows = self.rows()
        open_n = db.one("SELECT COUNT(*) n FROM tickets WHERE status!='closed'")["n"]
        for key, label in TABS.items():
            b = discord.ui.Button(label=f"{label} ({open_n})" if key == "open" else label, row=0,
                                  style=discord.ButtonStyle.primary if key == self.tab else discord.ButtonStyle.secondary)
            b.callback = self._tab(key)
            self.add_item(b)
        s = discord.ui.Button(label="Search", row=0, style=discord.ButtonStyle.primary if self.tab == "search"
                              else discord.ButtonStyle.secondary)
        s.callback = self._search
        self.add_item(s)
        chunk = rows[self.page * PER_PAGE:(self.page + 1) * PER_PAGE]
        if chunk:
            pick = discord.ui.Select(placeholder="Open a ticket", row=1, options=[
                discord.SelectOption(label=f"{t['id']:04d} · {_name(t)}"[:100], value=str(t["id"]),
                                     description=("open, jump to the post" if t["status"] != "closed"
                                                  else "closed, read the transcript"))
                for t in chunk])
            pick.callback = self._pick(pick)
            self.add_item(pick)
        pages = max(1, -(-len(rows) // PER_PAGE))
        prev = discord.ui.Button(label="◀", row=2, disabled=self.page == 0)
        nxt = discord.ui.Button(label="▶", row=2, disabled=self.page >= pages - 1)
        prev.callback, nxt.callback = self._turn(-1), self._turn(1)
        self.add_item(prev)
        self.add_item(nxt)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return False
        return True

    async def show(self, interaction: discord.Interaction):
        self.rebuild()
        await interaction.response.edit_message(embed=self.card(), view=self)

    def _tab(self, key):
        async def cb(interaction):
            self.tab, self.page, self.who = key, 0, interaction.user.id
            await self.show(interaction)
        return cb

    def _turn(self, step):
        async def cb(interaction):
            self.page += step
            await self.show(interaction)
        return cb

    async def _search(self, interaction):
        await interaction.response.send_modal(SearchModal(self))

    def _pick(self, sel):
        async def cb(interaction):
            from .cogs.tickets import ticket
            t = ticket(int(sel.values[0]))
            if t and t["status"] == "closed":
                if db.kv_get(f"tdeleted:{t['id']}"):
                    return await interaction.response.send_message(
                        f"Ticket {t['id']:04d} was deleted, so its chat wasn't stored.", ephemeral=True)
                return await transcripts.open_viewer(interaction, t["id"])
            if t and t["thread_id"]:
                return await interaction.response.send_message(f"Ticket {t['id']:04d}: <#{t['thread_id']}>",
                                                               ephemeral=True)
            await interaction.response.send_message("That ticket has no post.", ephemeral=True)
        return cb


class SearchModal(discord.ui.Modal, title="Find a ticket"):
    query = discord.ui.TextInput(label="Ticket number, member name or any word", max_length=80,
                                 placeholder="142 · uhxylor · livery")

    def __init__(self, browser: Browser):
        super().__init__()
        self.browser = browser

    async def on_submit(self, interaction: discord.Interaction):
        self.browser.tab, self.browser.query, self.browser.page = "search", self.query.value, 0
        await self.browser.show(interaction)


async def send(interaction: discord.Interaction, tab: str = "open", query: str = ""):
    """Open the browser privately for the staff member who asked."""
    if not is_staff(interaction.user):
        return await interaction.response.send_message("Staff only.", ephemeral=True)
    b = Browser(interaction.user.id, tab, query)
    await interaction.response.send_message(embed=b.card(), view=b, ephemeral=True)


class BrowseButton(discord.ui.DynamicItem[discord.ui.Button], template=r"asbot:tlist"):
    """Persistent "Browse tickets" button (inbox guide)."""
    def __init__(self):
        super().__init__(discord.ui.Button(label="Browse tickets", style=discord.ButtonStyle.primary,
                                           custom_id="asbot:tlist"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls()

    async def callback(self, interaction: discord.Interaction):
        await send(interaction)
