"""Closed-ticket history, kept by the bot (no channel).

On close every message of the ticket post is stored (who, when, text, kind) and its
screenshots are saved on the data volume. Staff read it in a private, page-by-page
viewer opened from a button (log line, "Past tickets" on a new ticket) or /ticket.
"""
from __future__ import annotations

import json
import re
import shutil

import discord

from . import db
from .core import disk_ok, embed, is_staff, log
from .paths import DATA

FILES = DATA / "data" / "attachments"
FILE_BUDGET = 25_000_000            # per ticket
FILES_TOTAL = 200_000_000           # all tickets; + 80 MB log image copies (modlog) leaves room on the 500 MB volume
PAGE_CHARS = 3600
KIND_ICON = {"member": "👤", "staff": "🛠️", "note": "🔒", "bot": "🤖"}

db.conn().executescript("""
CREATE TABLE IF NOT EXISTS ticket_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL,
    ts REAL NOT NULL, who TEXT NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL, files TEXT NOT NULL DEFAULT '[]');
CREATE INDEX IF NOT EXISTS ticket_messages_t ON ticket_messages(ticket_id);
""")


def _kind_and_text(m: discord.Message, bot_id: int) -> tuple[str, str, str]:
    """(who, kind, text) for one message in a ticket post."""
    text = m.content or ""
    if m.author.id != bot_id:
        return m.author.display_name, ("note" if text.startswith("//") else "staff"), text
    if m.flags.components_v2:   # the staff card: keep its text (member, problem, helper line)
        return "Bot", "bot", "\n".join(_card_text(m.components))
    if m.embeds:
        e = m.embeds[0]
        name = (e.author.name if e.author else "") or ""
        if name.endswith("(member)"):
            return name.removesuffix(" (member)"), "member", e.description or ""
        if name.startswith("Support Agent · "):   # the support helper's lines in the post
            return "Support helper", "bot", f"**{name.split(' · ', 1)[1]}**\n{e.description or ''}"
        if "Support team" in name or name.endswith(" · Support Agent"):
            return name.split(" · ")[0], "staff", e.description or ""
        parts = []
        for e in m.embeds:   # skip the live status card on top of a ticket; keep profile + problem
            if e.footer and (e.footer.text or "").startswith("Type here to reply"):
                continue
            parts += [f"**{e.title}**" if e.title else "", e.description or ""]
            parts += [f"{f.name}: {f.value}" for f in e.fields]
        return "Bot", "bot", "\n".join(p for p in parts if p)
    return "Bot", "bot", text


def _card_text(items) -> list[str]:
    out = []
    for c in items or []:
        if getattr(c, "content", None) and not str(c.content).startswith("-# Type here"):
            out.append(c.content)
        out += _card_text(getattr(c, "children", None) or getattr(c, "components", None))
    return out


async def archive(tid: int, thread: discord.Thread, bot_id: int) -> int:
    """Store the whole post. Returns the number of messages saved."""
    folder = FILES / f"{tid:04d}"
    budget, n = FILE_BUDGET, 0
    rows = []
    async for m in thread.history(limit=None, oldest_first=True):
        who, kind, text = _kind_and_text(m, bot_id)
        saved = []
        for a in m.attachments:
            if a.size <= budget and disk_ok(a.size, f"ticket {tid} file"):
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / f"{m.id}_{re.sub(r'[^A-Za-z0-9._-]', '_', a.filename)}"
                try:
                    await a.save(path)
                    saved.append(path.name)
                    budget -= a.size
                except (discord.HTTPException, OSError):   # a lost file must not lose the whole chat
                    pass
        if text or saved:
            rows.append((tid, m.created_at.timestamp(), who, kind, text[:4000], json.dumps(saved)))
            n += 1
    with db.conn():
        db.conn().executemany("INSERT INTO ticket_messages(ticket_id, ts, who, kind, content, files) "
                              "VALUES(?, ?, ?, ?, ?, ?)", rows)
    return n


def import_rows(tid: int, rows: list[dict]) -> None:
    """Used once to bring in tickets closed before this system existed."""
    if db.one("SELECT 1 FROM ticket_messages WHERE ticket_id=?", tid):
        return
    for r in rows:
        db.run("INSERT INTO ticket_messages(ticket_id, ts, who, kind, content) VALUES(?, ?, ?, ?, ?)",
               tid, r["ts"], r["who"], r["kind"], r["content"])


def messages(tid: int):
    return db.q("SELECT * FROM ticket_messages WHERE ticket_id=? ORDER BY ts, id", tid)


def pages(tid: int) -> list[str]:
    out, cur = [], ""
    for m in messages(tid):
        files = json.loads(m["files"])
        line = (f"{KIND_ICON.get(m['kind'], '•')} **{m['who']}** · <t:{int(m['ts'])}:t>"
                + (" · staff note" if m["kind"] == "note" else "") + "\n"
                + (m["content"].replace("\n", "\n> ").join(["> ", ""]) if m["content"] else "")
                + (f"\n📎 {len(files)} file(s)" if files else "") + "\n")
        if len(cur) + len(line) > PAGE_CHARS and cur:
            out.append(cur)
            cur = ""
        cur += line[:PAGE_CHARS] + "\n"
    if cur:
        out.append(cur)
    return out or ["*No messages were saved for this ticket.*"]


def files_of(tid: int) -> list:
    folder = FILES / f"{tid:04d}"
    names = [n for m in messages(tid) for n in json.loads(m["files"])]
    return [folder / n for n in names if (folder / n).exists()]


def search(text: str, limit: int = 15):
    like = f"%{text}%"
    return db.q("SELECT DISTINCT t.* FROM tickets t LEFT JOIN ticket_messages m ON m.ticket_id=t.id "
                "WHERE t.status='closed' AND (m.content LIKE ? OR m.who LIKE ? OR t.topic LIKE ?) "
                "ORDER BY t.id DESC LIMIT ?", like, like, like, limit)


def prune(keep_bytes: int = FILES_TOTAL) -> None:
    """Keep the volume under its 500 MB: drop the oldest tickets' files first (text stays)."""
    if not FILES.exists():
        return
    folders = sorted((p for p in FILES.iterdir() if p.is_dir()), key=lambda p: int(p.name) if p.name.isdigit() else 0)
    size = sum(f.stat().st_size for p in folders for f in p.iterdir())
    while folders and size > keep_bytes:
        old = folders.pop(0)
        size -= sum(f.stat().st_size for f in old.iterdir())
        shutil.rmtree(old, ignore_errors=True)
        log.info("transcripts: dropped files of ticket %s to save space", old.name)


# ---------- viewer ----------
class Viewer(discord.ui.View):
    """Private, page-by-page transcript for one staff member."""

    def __init__(self, tid: int, header: discord.Embed):
        super().__init__(timeout=900)
        self.tid, self.header, self.page = tid, header, 0
        self.pages = pages(tid)
        self.files.disabled = not files_of(tid)
        self._sync()

    def _sync(self):
        self.prev.disabled = self.page == 0
        self.next.disabled = self.page >= len(self.pages) - 1
        self.counter.label = f"{self.page + 1} / {len(self.pages)}"

    def body(self) -> list[discord.Embed]:
        return [self.header, embed(description=self.pages[self.page], colour=discord.Colour(0x2B2D31))]

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, _):
        self.page -= 1
        self._sync()
        await interaction.response.edit_message(embeds=self.body(), view=self)

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.secondary, disabled=True)
    async def counter(self, interaction: discord.Interaction, _):
        pass

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _):
        self.page += 1
        self._sync()
        await interaction.response.edit_message(embeds=self.body(), view=self)

    @discord.ui.button(label="Screenshots", style=discord.ButtonStyle.primary)
    async def files(self, interaction: discord.Interaction, _):
        paths = files_of(self.tid)[:10]
        await interaction.response.send_message(
            f"Files from ticket {self.tid:04d}:", files=[discord.File(p, p.name.split("_", 1)[-1]) for p in paths],
            ephemeral=True)


async def open_viewer(interaction: discord.Interaction, tid: int):
    if not is_staff(interaction.user):
        return await interaction.response.send_message("Staff only.", ephemeral=True)
    from .cogs.tickets import summary_card, ticket   # avoid an import loop
    t = ticket(tid)
    if not t or t["status"] != "closed":
        return await interaction.response.send_message("No closed ticket with that number.", ephemeral=True)
    v = Viewer(tid, summary_card(t))
    await interaction.response.send_message(embeds=v.body(), view=v, ephemeral=True)


class ViewButton(discord.ui.DynamicItem[discord.ui.Button], template=r"asbot:tv:(?P<id>\d+)"):
    def __init__(self, tid: int):
        super().__init__(discord.ui.Button(label="View transcript", style=discord.ButtonStyle.secondary,
                                           custom_id=f"asbot:tv:{tid}"))
        self.tid = tid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        await open_viewer(interaction, self.tid)


def pick_list(rows, placeholder: str = "Open a past ticket") -> discord.ui.View:
    """Dropdown of tickets; picking one opens the viewer."""
    v = discord.ui.View(timeout=600)
    sel = discord.ui.Select(placeholder=placeholder, options=[
        discord.SelectOption(label=f"{r['id']:04d} · {r['topic']}"[:100], value=str(r["id"]),
                             description=("★" * r["rating"] if r["rating"] else "not rated"))
        for r in rows[:25]])

    async def chosen(interaction: discord.Interaction):
        await open_viewer(interaction, int(sel.values[0]))
    sel.callback = chosen
    v.add_item(sel)
    return v


class HistoryButton(discord.ui.DynamicItem[discord.ui.Button], template=r"asbot:th:(?P<uid>\d+)"):
    def __init__(self, uid: int, count: int = 0):
        super().__init__(discord.ui.Button(label=f"Past tickets ({count})" if count else "Past tickets",
                                           style=discord.ButtonStyle.secondary, custom_id=f"asbot:th:{uid}"))
        self.uid = uid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["uid"]))

    async def callback(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("Staff only.", ephemeral=True)
        rows = db.q("SELECT * FROM tickets WHERE user_id=? AND status='closed' ORDER BY id DESC", self.uid)
        if not rows:
            return await interaction.response.send_message("No past tickets.", ephemeral=True)
        await interaction.response.send_message(f"{len(rows)} past ticket(s) from <@{self.uid}>:",
                                                view=pick_list(rows), ephemeral=True)
