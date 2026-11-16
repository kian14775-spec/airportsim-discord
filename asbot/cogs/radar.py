"""Incident radar: notices when several members hit the same problem and asks staff about it.

3+ members with the same word in their ticket (or the same topic: Bug, Purchase) within an hour ->
a card in #alerts. It's real -> the text goes on the support card (amber) and the Support Agent
answers new tickets with it. Fixed -> the card goes back to normal and everyone involved gets a DM.
Nothing reaches members until staff press It's real. Settings: `radar:` in config/bot.yaml.
"""
from __future__ import annotations

import asyncio
import json
import logging

import discord
from discord.ext import commands

from .. import db, faq, names
from ..core import cfg, channel, is_staff, log_card, log_staff

log = logging.getLogger("asbot")

db.conn().executescript("""
CREATE TABLE IF NOT EXISTS incidents (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new', opened REAL NOT NULL, tickets TEXT NOT NULL DEFAULT '[]',
    message_id INTEGER, issue TEXT, by_id INTEGER);
""")

IGNORED_TOPICS = {"report", "appeal"}   # about a person, not about the game
NOISE = {"game", "play", "playing", "played", "work", "working", "works", "issue", "issues", "problem", "problems",
         "ticket", "help", "roblox", "airport", "simulator", "plane", "please", "thanks", "sorry", "hello", "someone",
         "something", "anything", "keeps", "still", "today", "really", "been", "same", "time", "after", "before",
         "would", "could", "should", "because", "them", "their", "were", "only", "even", "cant", "wont", "dont"}
TOPIC_NAMES = {"bug": "bugs", "purchase": "purchases"}
MAX_DMS = 100
LIST_TICKETS = 8


def settings() -> dict:
    # open_hours: an unanswered "possible incident" stops collecting tickets (and blocking new alerts) after this
    return {"window_min": 60, "min_members": 3, "topics": ["bug", "purchase"], "cooldown_hours": 2, "open_hours": 48,
            **(cfg().get("radar") or {})}


def words_of(text: str) -> dict[str, str]:
    """{stem: word as typed} for the words in a ticket that could name a problem."""
    from .answers import STOPWORDS
    return {faq.stem(w): w for w in faq.WORD.findall(text.lower())
            if len(w) > 3 and w not in NOISE and w not in STOPWORDS and not w.isdigit()}


def cluster(rows: list[dict], s: dict, blocked: set[str]) -> tuple[str, str, list[int]] | None:
    """The biggest group of different members with the same problem: (key, label, ticket ids).
    A shared word says more than a shared topic, so a word wins a tie."""
    groups: dict[str, list[dict]] = {}
    labels: dict[str, str] = {}
    for r in rows:
        for stem, word in r["words"].items():
            groups.setdefault(f"word:{stem}", []).append(r)
            labels.setdefault(f"word:{stem}", word)
        if r["topic"] in s["topics"]:
            groups.setdefault(f"topic:{r['topic']}", []).append(r)
            labels.setdefault(f"topic:{r['topic']}", TOPIC_NAMES.get(r["topic"], r["topic"]))
    best = None
    for key, group in groups.items():
        people = {r["user"] for r in group}
        if key in blocked or len(people) < s["min_members"]:
            continue
        rank = (len(people), key.startswith("word:"), key)
        if best is None or rank > best[0]:
            best = (rank, key, labels[key], sorted(r["id"] for r in group))
    return best[1:] if best else None


def matches(inc, topic: str, words: dict, text: str) -> bool:
    from .tickets import known_issue
    kind, _, value = inc["key"].partition(":")
    return (value in words if kind == "word" else value == topic) or \
        bool(inc["issue"] and known_issue(text) == inc["issue"])


def ticket_text(tid: int) -> str:
    d = db.kv_get(f"tcard:{tid}", {})
    return f"{d.get('problem') or ''} {d.get('extra') or ''}"


def incident(iid: int):
    return db.one("SELECT * FROM incidents WHERE id=?", iid)


def card(inc) -> tuple[discord.Embed, discord.ui.View | None]:
    ids = json.loads(inc["tickets"])
    rows = [db.one("SELECT * FROM tickets WHERE id=?", i) for i in ids]
    rows = [r for r in rows if r]
    people = len({r["user_id"] for r in rows})
    mins = max(1, round((db.now() - min((r["created"] for r in rows), default=inc["opened"])) / 60))
    lines = [f"**{len(rows)} tickets** from **{people} members**, first one {mins} min ago"
             if inc["status"] in ("new", "real") else f"**{len(rows)} tickets** from **{people} members**"]
    lines += [f"`{r['id']:04d}` " + (f"<#{r['thread_id']}>" if r["thread_id"] else "no post yet") for r in rows[-LIST_TICKETS:]]
    if len(rows) > LIST_TICKETS:
        lines.append(f"-# and {len(rows) - LIST_TICKETS} earlier")
    who = f"<@{inc['by_id']}>" if inc["by_id"] else "staff"
    head, tone = {
        "new": (f"Possible incident: {inc['label']}", "wait"),
        "real": (f"Incident: {inc['issue']}", "bad"),
        "false": (f"False alarm: {inc['label']}", "quiet"),
        "fixed": (f"Fixed: {inc['issue']}", "good"),
    }[inc["status"]]
    note = {"new": "Members see nothing until you press It's real.",
            "real": f"On the support card, confirmed by {who}.",
            "false": f"Dismissed by {who}.",
            "fixed": f"Closed by {who}, members told."}[inc["status"]]
    lines.append(f"-# {note}")
    e = log_card(head, "\n".join(lines), tone=tone)
    buttons = {"new": ("real", "false"), "real": ("fixed",)}.get(inc["status"], ())
    view = None
    if buttons:
        view = discord.ui.View(timeout=None)
        for act in buttons:
            view.add_item(RadarButton(act, inc["id"]))
    return e, view


async def post_new(guild: discord.Guild, iid: int) -> None:
    from .guard import alert_ping
    e, view = card(incident(iid))
    text, mentions = alert_ping(guild)
    msg = await log_staff(guild, text, kind="Security", embed=e, view=view, allowed_mentions=mentions)
    if msg:
        db.run("UPDATE incidents SET message_id=? WHERE id=?", msg.id, iid)


async def refresh(guild: discord.Guild, iid: int) -> None:
    inc = incident(iid)
    ch = channel(guild, "log_security")
    if not inc or not inc["message_id"] or ch is None:
        return
    e, view = card(inc)
    await names.fill_embed(e)
    try:
        await ch.get_partial_message(inc["message_id"]).edit(embed=e, view=view)
    except discord.HTTPException as err:
        log.warning("radar card %s: %s", iid, err)


async def note_ticket(guild: discord.Guild, tid: int, topic: str, user_id: int, text: str) -> None:
    """Called for every new ticket."""
    if topic in IGNORED_TOPICS:
        return
    s, now, words = settings(), db.now(), words_of(text)
    since = now - s["open_hours"] * 3600   # older unconfirmed ones are stale; confirmed ones stay until Fixed
    live = db.q("SELECT * FROM incidents WHERE status='real' OR (status='new' AND opened>=?) ORDER BY id", since)
    for inc in live:
        if matches(inc, topic, words, text):
            ids = json.loads(inc["tickets"])
            if tid not in ids:
                db.run("UPDATE incidents SET tickets=? WHERE id=?", json.dumps(ids + [tid]), inc["id"])
            return await refresh(guild, inc["id"])
    rows = [{"id": r["id"], "user": r["user_id"], "topic": r["topic"], "words": words_of(ticket_text(r["id"]))}
            for r in db.q("SELECT id, user_id, topic FROM tickets WHERE created>=?", now - s["window_min"] * 60)
            if r["topic"] not in IGNORED_TOPICS]
    recent = db.q("SELECT key FROM incidents WHERE status='real' OR (status='new' AND opened>=?) OR opened>=?",
                  since, now - s["cooldown_hours"] * 3600)
    found = cluster(rows, s, {r["key"] for r in recent})
    if found:
        key, label, ids = found
        await post_new(guild, db.run("INSERT INTO incidents(key, label, opened, tickets) VALUES(?, ?, ?, ?)",
                                     key, label, now, json.dumps(ids)))


class ConfirmModal(discord.ui.Modal, title="Confirm the incident"):
    text = discord.ui.TextInput(label="What members see on the support card", max_length=120)

    def __init__(self, iid: int, label: str):
        super().__init__()
        self.iid = iid
        self.text.default = f"{label.capitalize()} problems"

    async def on_submit(self, interaction: discord.Interaction):
        from .news import status
        await interaction.response.defer(ephemeral=True)
        text = str(self.text).strip()
        if not db.changes("UPDATE incidents SET status='real', issue=?, by_id=? WHERE id=? AND status='new'",
                          text, interaction.user.id, self.iid):   # someone confirmed it first: don't add it twice
            return await interaction.followup.send("Someone already handled this.", ephemeral=True)
        news = interaction.client.get_cog("News")
        await news.edit_status(add=text)
        if status()["state"] == "ok":
            await news.set_state("issues")
        await refresh(interaction.guild, self.iid)
        await interaction.followup.send("It's on the support card, and new tickets about it get told.", ephemeral=True)


class RadarButton(discord.ui.DynamicItem[discord.ui.Button], template=r"asbot:radar:(?P<act>real|false|fixed):(?P<iid>\d+)"):
    def __init__(self, act: str, iid: int):
        label, style = {"real": ("It's real", discord.ButtonStyle.danger),
                        "false": ("False alarm", discord.ButtonStyle.secondary),
                        "fixed": ("Fixed", discord.ButtonStyle.success)}[act]
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=f"asbot:radar:{act}:{iid}"))
        self.act, self.iid = act, iid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["act"], int(match["iid"]))

    async def callback(self, interaction: discord.Interaction):
        inc = incident(self.iid)
        if not is_staff(interaction.user):
            return await interaction.response.send_message("Staff only.", ephemeral=True)
        was = "real" if self.act == "fixed" else "new"
        if not inc or inc["status"] != was:
            return await interaction.response.send_message("Someone already handled this.", ephemeral=True)
        if self.act == "real":
            return await interaction.response.send_modal(ConfirmModal(self.iid, inc["label"]))
        # status first, before any await: a double-click on Fixed must not DM everyone twice
        if not db.changes("UPDATE incidents SET status=?, by_id=? WHERE id=? AND status=?",
                          self.act, interaction.user.id, self.iid, was):
            return await interaction.response.send_message("Someone already handled this.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        if self.act == "false":
            await refresh(interaction.guild, self.iid)
            return await interaction.followup.send("Dismissed.", ephemeral=True)
        await refresh(interaction.guild, self.iid)
        told = await close_out(interaction.client, inc)
        await interaction.followup.send(f"Done. Support card updated, {told} members told.", ephemeral=True)


async def close_out(bot: commands.Bot, inc) -> int:
    """Take it off the support card and DM everyone who raised it."""
    from .news import status
    news = bot.get_cog("News")
    await news.edit_status(remove=inc["issue"])
    s = status()
    if s["state"] == "issues" and not s["issues"]:
        await news.set_state("ok")
    ids = json.loads(inc["tickets"])
    marks = ",".join("?" * len(ids))
    users = [r["user_id"] for r in db.q(f"SELECT DISTINCT user_id FROM tickets WHERE id IN ({marks})", *ids)]
    told = 0
    for uid in users[:MAX_DMS]:
        try:
            user = bot.get_user(uid) or await bot.fetch_user(uid)
            await user.send(f"Good news: **{inc['issue']}** is fixed. If you still see it, rejoin the game "
                            "and let us know in your ticket.")
            told += 1
        except discord.HTTPException:
            pass
        await asyncio.sleep(1)
    return told


async def setup(bot):
    bot.add_dynamic_items(RadarButton)
