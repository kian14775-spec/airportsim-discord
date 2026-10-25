"""Inbox tickets: the member talks to the bot in DMs, staff work in one forum (#inbox).

Flow: support menu -> topic -> short form -> instant answer if the FAQ matches
("fix it first") -> ticket post in #inbox -> messages relayed both ways.
Staff messages starting with // stay internal (notes).

#inbox only ever holds OPEN tickets. Each post starts with a control card that is
edited in place (status, priority, claimed by, waiting time) and a title like
"🟢 0012 · Purchase · kian". On close the chat and screenshots are stored by the bot
(asbot/transcripts.py) and the post is removed; staff read it in a private viewer.
Timers: staff get one nudge if nobody answered in 30 min; a ticket waiting on the
member is warned after 24 h of silence and auto-closed after 48 h.
"""
from __future__ import annotations

import datetime as dt
import json

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .. import db, opencloud
from ..core import (cfg, channel, embed, is_staff, log, log_staff, role_named, roblox_group_rank,
                    roblox_headshot, roblox_user, roblox_user_by_name, staff_only)
from ..renamer import rename_soon
from .. import transcripts

NOTE_PREFIX = "//"
from ..ui import BAD as RED, BRAND as BLUE, GOOD as GREEN, QUIET as GREY, WAIT as AMBER
NUDGE_AFTER = 30 * 60
WARN_AFTER = 24 * 3600
CLOSE_AFTER = 48 * 3600
PRIORITIES = {"low": "Low", "normal": "Normal", "urgent": "Urgent"}
CARD_FOOTER = "Type here to reply to the member · start a message with // for a staff-only note"


def topics() -> dict[str, dict]:
    return {t["key"]: t for t in cfg()["support"]["topics"]}


def topic_label(key: str) -> str:
    return "Ban appeal" if key == "appeal" else topics().get(key, {}).get("label", key)


def ticket(tid: int):
    return db.one("SELECT * FROM tickets WHERE id=?", tid)


def open_ticket_for(user_id: int):
    return db.one("SELECT * FROM tickets WHERE user_id=? AND status!='closed' ORDER BY id DESC", user_id)


def ticket_for_thread(thread_id: int):
    return db.one("SELECT * FROM tickets WHERE thread_id=?", thread_id)


def queue_position(tid: int) -> int:
    return db.one("SELECT COUNT(*) n FROM tickets WHERE status!='closed' AND first_reply IS NULL AND id<=?",
                  tid)["n"]


def eta_minutes() -> int:
    rows = db.q("SELECT first_reply - created d FROM tickets WHERE first_reply IS NOT NULL ORDER BY id DESC LIMIT 20")
    if not rows:
        return cfg()["support"]["queue_eta_fallback_min"]
    return max(1, round(sum(r["d"] for r in rows) / len(rows) / 60))


def faq_answer(text: str) -> str | None:
    low = text.lower()
    for a in cfg()["support"]["answers"]:
        if any(k in low for k in a["keywords"]):
            return a["answer"]
    return None


def state_of(t) -> tuple[str, str, int]:
    """(dot, words, colour) for the title and control card."""
    if t["status"] == "closed":
        return "⚫", "Closed", GREY
    if t["status"] == "closing":
        return "⏳", "Waiting for the member to confirm", AMBER
    if t["priority"] == "urgent":
        return "🔴", "Urgent", RED
    if t["status"] == "waiting":
        return "🟡", "Waiting on member", AMBER
    if t["claimed_by"]:
        return "🔵", "Claimed", BLUE
    return "🟢", "New, needs a reply", GREEN


def title_of(t, user_name: str) -> str:
    dot = state_of(t)[0]
    return f"{dot} {t['id']:04d} · {topic_label(t['topic'])} · {user_name}"[:100]


def tag_names(t) -> list[str]:
    tags = ["Waiting" if t["status"] in ("waiting", "closing") else "Open"]
    if t["claimed_by"]:
        tags.append("Claimed")
    if t["priority"] == "urgent":
        tags.append("Urgent")
    if t["topic"] == "appeal":
        tags.append("Appeal")
    if db.kv_get(f"ticket_dev:{t['id']}"):
        tags.append("Dev team")
    return tags[:5]


def control_card(t) -> discord.Embed:
    dot, words, colour = state_of(t)
    e = embed(f"{dot} Ticket {t['id']:04d} · {topic_label(t['topic'])}", colour=discord.Colour(colour))
    e.add_field(name="Status", value=words)
    e.add_field(name="Priority", value=PRIORITIES.get(t["priority"], "Normal"))
    e.add_field(name="Claimed by", value=f"<@{t['claimed_by']}>" if t["claimed_by"] else "Nobody yet")
    e.add_field(name="Opened", value=f"<t:{int(t['created'])}:R>")
    last = t["last_activity"] or t["created"]
    e.add_field(name="Last message", value=f"<t:{int(last)}:R>")
    e.add_field(name="Member", value=f"<@{t['user_id']}>")
    e.set_footer(text=CARD_FOOTER)
    return e


async def profile_embed(guild: discord.Guild, user: discord.abc.User, roblox_hint: str | None = None) -> discord.Embed:
    member = guild.get_member(user.id)
    e = embed(user.display_name, colour=discord.Colour(GREY))
    e.add_field(name="Discord", value=f"{user.mention}\n`{user.id}`\nAccount <t:{int(user.created_at.timestamp())}:R>")
    if member and member.joined_at:
        e.add_field(name="Joined server", value=f"<t:{int(member.joined_at.timestamp())}:R>")
    link = db.one("SELECT * FROM links WHERE discord_id=?", user.id)
    rid, rname, note = None, None, ""
    if link:
        rid, rname = link["roblox_id"], link["roblox_name"]
    elif roblox_hint:
        found = await roblox_user_by_name(roblox_hint)
        if found:
            rid, rname, note = found["id"], found["name"], " (typed, not verified)"
    if rid:
        info = await roblox_user(rid) or {}
        created = info.get("created", "")[:10]
        rank = await roblox_group_rank(rid, cfg()["game"]["group_id"])
        lines = [f"[{rname}](https://www.roblox.com/users/{rid}/profile){note}", f"ID `{rid}`"]
        if created:
            lines.append(f"Roblox account since {created}")
        lines.append(f"Group rank: {rank or 'not in group'}")
        ban = await opencloud.get_ban(rid)
        if ban:
            lines.append(f"**Game banned**: {ban.get('displayReason') or 'no reason given'}")
        e.add_field(name="Roblox", value="\n".join(lines), inline=False)
        thumb = await roblox_headshot(rid)
        if thumb:
            e.set_thumbnail(url=thumb)
    else:
        e.add_field(name="Roblox", value="Not linked", inline=False)
    past = db.one("SELECT COUNT(*) n FROM tickets WHERE user_id=? AND status='closed'", user.id)["n"]
    e.set_footer(text=f"{past} past ticket(s)")
    return e


def link_copy(src: discord.Message, copy: discord.Message, tid: int, side: str) -> None:
    """Remember that `copy` is the relayed version of `src` (side = who wrote the original)."""
    db.run("INSERT OR REPLACE INTO relay_map(src, dst_channel, dst, ticket_id, side) VALUES(?, ?, ?, ?, ?)",
           src.id, copy.channel.id, copy.id, tid, side)


# ---------- persistent controls (survive restarts) ----------
class TicketButton(discord.ui.DynamicItem[discord.ui.Button], template=r"asbot:t:(?P<action>[a-z]+):(?P<id>\d+)"):
    LABELS = {"claim": ("Claim", discord.ButtonStyle.primary), "dev": ("Send to dev team", discord.ButtonStyle.secondary),
              "close": ("Ask to close", discord.ButtonStyle.success), "force": ("Close now", discord.ButtonStyle.danger),
              "unban": ("Unban in game", discord.ButtonStyle.success),
              "uclose": ("Close my ticket", discord.ButtonStyle.secondary)}

    def __init__(self, action: str, tid: int):
        label, style = self.LABELS[action]
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=f"asbot:t:{action}:{tid}"))
        self.action, self.tid = action, tid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["action"], int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        cog: Tickets = interaction.client.get_cog("Tickets")
        t = ticket(self.tid)
        if t is None or t["status"] == "closed":
            return await interaction.response.send_message("This ticket is closed.", ephemeral=True)
        if self.action == "uclose":
            if interaction.user.id != t["user_id"]:
                return await interaction.response.send_message("Not your ticket.", ephemeral=True)
            await interaction.response.send_message("Closing your ticket...")
            return await cog.close(t, by=interaction.user, reason="closed by the member")
        member = cog.guild.get_member(interaction.user.id)
        if not member or not is_staff(member):
            return await interaction.response.send_message("Staff only.", ephemeral=True)
        if self.action == "claim":
            db.run("UPDATE tickets SET claimed_by=? WHERE id=?", member.id, t["id"])
            await interaction.response.send_message(f"You claimed ticket {t['id']:04d}.", ephemeral=True)
            await cog.refresh(t["id"])
            await log_staff(cog.guild, f"Ticket {t['id']:04d} claimed by {member.mention}", kind="Tickets")
        elif self.action == "dev":
            db.kv_set(f"ticket_dev:{t['id']}", True)
            dev = role_named(cog.guild, cfg().get("dev_role_name", "Developer"))
            await interaction.response.send_message(
                f"{dev.mention if dev else 'Dev team'}: {member.display_name} needs you on this one.",
                allowed_mentions=discord.AllowedMentions(roles=True))
            await cog.refresh(t["id"])
        elif self.action == "close":
            await interaction.response.send_message("Asked the member if it's solved. It closes when they "
                                                    "confirm, or by itself in 24 hours.")
            await cog.ask_close(t, member)
        elif self.action == "force":
            await interaction.response.send_message("Closing now...")
            await cog.close(t, by=member)
        elif self.action == "unban":
            rid = db.kv_get(f"ticket_roblox:{t['id']}")
            if not rid:
                return await interaction.response.send_message("No Roblox account on this appeal.", ephemeral=True)
            ok = await opencloud.unban(rid)
            await interaction.response.send_message(
                f"Unbanned in game by {member.mention}." if ok else "Unban failed (Open Cloud key missing or no access).",
                allowed_mentions=discord.AllowedMentions.none())
            if ok:
                await cog.send_to_user(t, embed("You've been unbanned",
                                                "Your appeal was accepted. You can join the game again.",
                                                colour=discord.Colour(GREEN)))
                await log_staff(cog.guild, f"Game unban for `{rid}` by {member.mention} (ticket {t['id']:04d})",
                                kind="Moderation")


class PrioritySelect(discord.ui.DynamicItem[discord.ui.Select], template=r"asbot:tp:(?P<id>\d+)"):
    def __init__(self, tid: int):
        super().__init__(discord.ui.Select(
            custom_id=f"asbot:tp:{tid}", placeholder="Set priority",
            options=[discord.SelectOption(label=v, value=k) for k, v in PRIORITIES.items()]))
        self.tid = tid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        cog: Tickets = interaction.client.get_cog("Tickets")
        t = ticket(self.tid)
        if not t or t["status"] == "closed" or not is_staff(interaction.user):
            return await interaction.response.send_message("Not available.", ephemeral=True)
        pr = self.item.values[0]
        db.run("UPDATE tickets SET priority=? WHERE id=?", pr, t["id"])
        if pr == "urgent":
            team = role_named(cog.guild, "Support Team")
            await interaction.response.send_message(
                f"🔴 Marked **urgent** by {interaction.user.mention}. {team.mention if team else ''}",
                allowed_mentions=discord.AllowedMentions(roles=True, users=False))
        else:
            await interaction.response.send_message(f"Priority set to {PRIORITIES[pr]}.", ephemeral=True)
        await cog.refresh(t["id"])


class ConfirmClose(discord.ui.DynamicItem[discord.ui.Button], template=r"asbot:tc:(?P<id>\d+):(?P<ans>yes|no)"):
    def __init__(self, tid: int, ans: str):
        label, style = (("Yes, close it", discord.ButtonStyle.success) if ans == "yes"
                        else ("No, I still need help", discord.ButtonStyle.secondary))
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=f"asbot:tc:{tid}:{ans}"))
        self.tid, self.ans = tid, ans

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]), match["ans"])

    async def callback(self, interaction: discord.Interaction):
        cog: Tickets = interaction.client.get_cog("Tickets")
        t = ticket(self.tid)
        if not t or t["user_id"] != interaction.user.id or t["status"] != "closing":
            return await interaction.response.send_message("This ticket isn't waiting on you.", ephemeral=True)
        if self.ans == "yes":
            await interaction.response.edit_message(content="Great, closing it now.", view=None)
            return await cog.close(t, by=interaction.user, reason="closed, the member confirmed it's solved")
        db.run("UPDATE tickets SET status='open', last_activity=? WHERE id=?", db.now(), t["id"])
        await interaction.response.edit_message(content="Got it, it stays open. Tell us what's still wrong here.",
                                                view=None)
        thread = await cog.fetch_thread(t["thread_id"])
        if thread:
            await thread.send("↩️ The member says it's **not solved yet**.")
        await cog.refresh(t["id"])


class RateButton(discord.ui.DynamicItem[discord.ui.Button], template=r"asbot:rate:(?P<id>\d+):(?P<score>[1-5])"):
    def __init__(self, tid: int, score: int):
        super().__init__(discord.ui.Button(label="★" * score, style=discord.ButtonStyle.secondary,
                                           custom_id=f"asbot:rate:{tid}:{score}"))
        self.tid, self.score = tid, score

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]), int(match["score"]))

    async def callback(self, interaction: discord.Interaction):
        t = ticket(self.tid)
        if not t or t["user_id"] != interaction.user.id:
            return await interaction.response.send_message("Not your ticket.", ephemeral=True)
        if t["rating"]:
            return await interaction.response.send_message("You already rated this one. Thanks!", ephemeral=True)
        db.run("UPDATE tickets SET rating=? WHERE id=?", self.score, self.tid)
        await interaction.response.edit_message(content=f"Thanks! You rated this {'★' * self.score}.", view=None)
        cog = interaction.client.get_cog("Tickets")
        await log_staff(cog.guild, f"Ticket {self.tid:04d} rated {'★' * self.score} by the member", kind="Tickets")


def staff_view(t) -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    for a in ("claim", "dev", "close", "force"):
        v.add_item(TicketButton(a, t["id"]))
    if t["topic"] == "appeal":
        v.add_item(TicketButton("unban", t["id"]))
    past = db.one("SELECT COUNT(*) n FROM tickets WHERE user_id=? AND status='closed'", t["user_id"])["n"]
    if past:
        v.add_item(transcripts.HistoryButton(t["user_id"], past))
    v.add_item(PrioritySelect(t["id"]))
    return v


def summary_card(t) -> discord.Embed:
    """Header of a closed ticket in the viewer."""
    mins = round(((t["closed"] or db.now()) - t["created"]) / 60)
    dur = f"{mins} min" if mins < 120 else f"{round(mins / 60, 1)} h"
    e = embed(f"Ticket {t['id']:04d} · {topic_label(t['topic'])}", colour=discord.Colour(GREY))
    e.add_field(name="Member", value=f"<@{t['user_id']}>")
    e.add_field(name="Handled by", value=f"<@{t['claimed_by']}>" if t["claimed_by"] else "Nobody claimed")
    e.add_field(name="Rating", value="★" * t["rating"] if t["rating"] else "Not rated")
    e.add_field(name="Opened", value=f"<t:{int(t['created'])}:f>")
    e.add_field(name="Open for", value=dur)
    e.add_field(name="Priority", value=PRIORITIES.get(t["priority"], "Normal"))
    return e


def user_view(tid: int) -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    v.add_item(TicketButton("uclose", tid))
    return v


def rating_view(tid: int) -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    for s in range(1, 6):
        v.add_item(RateButton(tid, s))
    return v


# ---------- opening ----------
class IssueModal(discord.ui.Modal):
    def __init__(self, topic: dict):
        super().__init__(title=topic["label"][:45])
        self.topic = topic
        extra = {"purchase": "Item name and when you bought it", "report": "Their Roblox username",
                 "bug": "Where in the game it happens"}.get(topic["key"])
        self.details = discord.ui.TextInput(label="What's going on?", style=discord.TextStyle.paragraph,
                                            min_length=10, max_length=1500)
        self.username = discord.ui.TextInput(label="Your Roblox username", required=False, max_length=20)
        self.add_item(self.details)
        self.add_item(self.username)
        self.extra = None
        if extra:
            self.extra = discord.ui.TextInput(label=extra, required=False, max_length=200)
            self.add_item(self.extra)

    async def on_submit(self, interaction: discord.Interaction):
        text = self.details.value
        extra = self.extra.value if self.extra else ""
        answer = faq_answer(text + " " + extra)
        if answer:
            view = TriageView(self.topic, text, extra, self.username.value)
            return await interaction.response.send_message(embed=embed("Try this first", answer), view=view,
                                                           ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.client.get_cog("Tickets").open(interaction, self.topic["key"], text, extra,
                                                          self.username.value)


class TriageView(discord.ui.View):
    def __init__(self, topic, text, extra, username):
        super().__init__(timeout=900)
        self.args = (topic["key"], text, extra, username)

    @discord.ui.button(label="That fixed it", style=discord.ButtonStyle.success)
    async def fixed(self, interaction: discord.Interaction, _):
        db.kv_set("triage_solved", db.kv_get("triage_solved", 0) + 1)
        await interaction.response.edit_message(content="Glad that helped!", embed=None, view=None)

    @discord.ui.button(label="I still need help", style=discord.ButtonStyle.secondary)
    async def still(self, interaction: discord.Interaction, _):
        await interaction.response.edit_message(content="Opening a ticket...", embed=None, view=None)
        await interaction.client.get_cog("Tickets").open(interaction, *self.args)


class AppealModal(discord.ui.Modal, title="Game ban appeal"):
    username = discord.ui.TextInput(label="Your Roblox username", max_length=20)
    why = discord.ui.TextInput(label="Why should we unban you?", style=discord.TextStyle.paragraph,
                               min_length=20, max_length=1500)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.client.get_cog("Tickets").open(interaction, "appeal", self.why.value, "",
                                                          self.username.value)


class TopicSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder="What do you need help with?", custom_id="asbot:support:topic", options=[
            discord.SelectOption(label=t["label"], description=t.get("description"), value=t["key"])
            for t in cfg()["support"]["topics"]])

    async def callback(self, interaction: discord.Interaction):
        cog: Tickets = interaction.client.get_cog("Tickets")
        blocked = cog.blocked_reason(interaction.user)
        if blocked:
            return await interaction.response.send_message(blocked, ephemeral=True)
        await interaction.response.send_modal(IssueModal(topics()[self.values[0]]))


class SupportView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TopicSelect())

    @discord.ui.button(label="Appeal a game ban", style=discord.ButtonStyle.secondary,
                       custom_id="asbot:support:appeal", row=1)
    async def appeal(self, interaction: discord.Interaction, _):
        blocked = interaction.client.get_cog("Tickets").blocked_reason(interaction.user)
        if blocked:
            return await interaction.response.send_message(blocked, ephemeral=True)
        await interaction.response.send_modal(AppealModal())

    @discord.ui.button(label="Apply for staff", style=discord.ButtonStyle.secondary,
                       custom_id="asbot:support:apply", row=1)
    async def apply(self, interaction: discord.Interaction, _):
        await interaction.client.get_cog("Applications").start(interaction)


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(SupportView())
        bot.add_dynamic_items(TicketButton, RateButton, PrioritySelect, ConfirmClose, transcripts.ViewButton,
                              transcripts.HistoryButton)
        self._dm_nag: dict[int, float] = {}
        self.monthly.start()
        self.timers.start()

    def cog_unload(self):
        self.monthly.cancel()
        self.timers.cancel()

    @property
    def guild(self) -> discord.Guild:
        return self.bot.get_guild(self.bot.guild_id)

    async def fetch_thread(self, tid: int | None):
        if not tid:
            return None
        th = self.guild.get_thread(tid)
        if th:
            return th
        try:
            return await self.bot.fetch_channel(tid)
        except discord.HTTPException:
            return None

    @commands.Cog.listener()
    async def on_ready(self):
        await self.ensure_inbox_guide()
        from ..paths import SEED   # tickets closed before the in-bot history existed
        for f in sorted((SEED / "import").glob("ticket-*.json")):
            try:
                transcripts.import_rows(int(f.stem.split("-")[1]), json.loads(f.read_text(encoding="utf-8")))
            except (ValueError, OSError) as e:
                log.warning("import %s: %s", f.name, e)
        for t in db.q("SELECT id FROM tickets WHERE status!='closed'"):   # bring older tickets up to date
            try:
                await self.refresh(t["id"])
            except Exception as e:  # noqa: BLE001 - one bad ticket must not stop the rest
                log.warning("refresh ticket %s: %s", t["id"], e)

    async def ensure_inbox_guide(self):
        inbox = channel(self.guild, "inbox")
        if not isinstance(inbox, discord.ForumChannel):
            return
        text = ("**How the inbox works**\n"
                "• Every post is one open ticket. Members talk to the bot in their DMs; you just type in the post.\n"
                "• Start a message with `//` for a staff-only note the member never sees.\n"
                "• Title dots: 🟢 new · 🔵 claimed · 🟡 waiting on member · 🔴 urgent.\n"
                "• Buttons: Claim, Send to dev team, Ask to close (the member confirms), Close now, priority.\n"
                "• `/reply` sends a saved reply. `/ticket_stats` shows this month's numbers.\n"
                "• Nobody answers in 30 min: Support Team gets one ping. Member silent 48 h: auto-close.\n"
                "• Closing stores the chat and removes the post. Read old tickets with the View transcript "
                "button in the Tickets log, Past tickets on a new ticket, or /ticket view · history · search.")
        tid = db.kv_get("inbox_guide")
        th = await self.fetch_thread(tid)
        if th is None:
            made = await inbox.create_thread(name="📌 How the inbox works", content=text)
            th = made.thread
            db.kv_set("inbox_guide", th.id)
            try:
                await th.edit(pinned=True, locked=True)
            except discord.HTTPException as e:
                log.warning("inbox guide pin: %s", e)
        else:
            try:
                starter = await th.fetch_message(th.id)
                if starter.content != text:
                    await starter.edit(content=text)
            except discord.HTTPException:
                pass

    def blocked_reason(self, user) -> str | None:
        member = self.guild.get_member(user.id)
        bl = role_named(self.guild, "Ticket Blacklisted")
        if member and bl and bl in member.roles:
            return "You can't open tickets right now."
        t = open_ticket_for(user.id)
        if t:
            return f"You already have ticket {t['id']:04d} open. Reply to the bot in your DMs."
        return None

    async def open(self, interaction: discord.Interaction, topic_key: str, text: str, extra: str, username: str):
        user = interaction.user
        blocked = self.blocked_reason(user)
        if blocked:
            return await interaction.followup.send(blocked, ephemeral=True)
        inbox = channel(self.guild, "inbox")
        if not isinstance(inbox, discord.ForumChannel):
            return await interaction.followup.send("Support isn't set up yet. Try again later.", ephemeral=True)
        now = db.now()
        tid = db.run("INSERT INTO tickets(user_id, topic, created, last_activity) VALUES(?, ?, ?, ?)",
                     user.id, topic_key, now, now)
        label = topic_label(topic_key)
        pos, eta = queue_position(tid), eta_minutes()
        hello = embed(f"Ticket {tid:04d} opened · {label}",
                      f"Thanks, the team has your message. You're **#{pos} in line**, usual first reply "
                      f"**~{eta} min**.\n\nReply here to add details or screenshots. Everything you send in this "
                      "DM goes to the team.")
        try:
            await user.send(embed=hello, view=user_view(tid))
        except discord.Forbidden:
            db.run("DELETE FROM tickets WHERE id=?", tid)
            return await interaction.followup.send(
                "I can't DM you. Turn on **Direct Messages** for this server (server name > Privacy Settings), "
                "then try again.", ephemeral=True)
        issue = embed(f"The problem · {label}", text[:4000], colour=discord.Colour(AMBER))
        if extra:
            issue.add_field(name="Details", value=extra[:1000], inline=False)
        if topic_key == "appeal" and username:
            found = await roblox_user_by_name(username)
            if found:
                db.kv_set(f"ticket_roblox:{tid}", found["id"])
                ban = await opencloud.get_ban(found["id"])
                issue.add_field(name="Ban on record", value=(
                    f"{ban.get('displayReason') or 'no public reason'}\nPrivate: {ban.get('privateReason') or '-'}"
                    if ban else ("Not banned (or Open Cloud not set up)" if opencloud.available()
                                 else "Open Cloud key not set up, check by hand")), inline=False)
        prof = await profile_embed(self.guild, user, username or None)
        t = ticket(tid)
        tags = [tg for tg in inbox.available_tags if tg.name in tag_names(t)]
        made = await inbox.create_thread(
            name=title_of(t, user.name), embeds=[control_card(t), prof, issue], applied_tags=tags,
            view=staff_view(t), allowed_mentions=discord.AllowedMentions.none())
        db.run("UPDATE tickets SET thread_id=? WHERE id=?", made.thread.id, tid)
        await interaction.followup.send(f"Ticket {tid:04d} is open. Check your DMs.", ephemeral=True)
        await log_staff(self.guild, f"Ticket {tid:04d} opened by {user.mention} · {label}", kind="Tickets")

    async def refresh(self, tid: int):
        """Bring title, tags and the control card in line with the database."""
        t = ticket(tid)
        thread = await self.fetch_thread(t["thread_id"])
        if not thread or t["status"] == "closed":
            return
        if thread.archived:
            await thread.edit(archived=False)
        forum = thread.parent
        if isinstance(forum, discord.ForumChannel):
            tags = [tg for tg in forum.available_tags if tg.name in tag_names(t)]
            if {x.id for x in tags} != {x.id for x in thread.applied_tags}:
                try:
                    await thread.edit(applied_tags=tags)
                except discord.HTTPException as e:
                    log.warning("ticket tags: %s", e)
        user = self.bot.get_user(t["user_id"])
        if user is None:
            try:
                user = await self.bot.fetch_user(t["user_id"])
            except discord.HTTPException:
                user = None
        rename_soon(thread, title_of(t, user.name if user else str(t["user_id"])))
        try:
            starter = await thread.fetch_message(thread.id)
            rest = starter.embeds
            if rest and rest[0].footer and (rest[0].footer.text or "").startswith(CARD_FOOTER[:20]):
                rest = rest[1:]   # drop the old control card, keep profile + problem
            await starter.edit(embeds=[control_card(t)] + rest[:9], view=staff_view(t))
        except discord.HTTPException as e:
            log.warning("control card: %s", e)

    async def send_to_user(self, t, e: discord.Embed, files=None, view=None) -> discord.Message | None:
        try:
            user = self.bot.get_user(t["user_id"]) or await self.bot.fetch_user(t["user_id"])
            return await user.send(embed=e, files=files or [], view=view)
        except discord.HTTPException:
            return None

    # ---------- closing ----------
    async def ask_close(self, t, by: discord.Member):
        db.run("UPDATE tickets SET status='closing', last_activity=? WHERE id=?", db.now(), t["id"])
        v = discord.ui.View(timeout=None)
        v.add_item(ConfirmClose(t["id"], "yes"))
        v.add_item(ConfirmClose(t["id"], "no"))
        ok = await self.send_to_user(t, embed(f"Is ticket {t['id']:04d} solved?",
                                              f"{by.display_name} thinks this is sorted. Press a button below. "
                                              "If we don't hear back, it closes by itself in 24 hours.",
                                              tone="wait"), view=v)
        if not ok:   # can't reach them: just close
            return await self.close(ticket(t["id"]), by=by, reason=f"closed by {by.display_name} (member unreachable)")
        await self.refresh(t["id"])

    async def close(self, t, by: discord.abc.User | None, reason: str | None = None):
        if ticket(t["id"])["status"] == "closed":
            return
        db.run("UPDATE tickets SET status='closed', closed=? WHERE id=?", db.now(), t["id"])
        t = ticket(t["id"])
        thread = await self.fetch_thread(t["thread_id"])
        who = reason or (f"closed by {by.display_name}" if by else "closed")
        saved = 0
        if thread:
            try:
                saved = await transcripts.archive(t["id"], thread, self.bot.user.id)
                await thread.delete()
            except Exception as e:  # noqa: BLE001 - keep the post if storing failed
                log.warning("ticket %s archive: %s", t["id"], e)
                await thread.send(f"Closed ({who}). Couldn't store the chat, so this post stays.")
                await thread.edit(archived=True, locked=True)
        transcripts.prune()
        await self.send_to_user(t, embed(f"Ticket {t['id']:04d} closed",
                                         "Thanks for reaching out. How did we do?", colour=discord.Colour(GREY)),
                                view=rating_view(t["id"]))
        v = discord.ui.View(timeout=None)
        v.add_item(transcripts.ViewButton(t["id"]))
        await log_staff(self.guild, f"✅ Ticket {t['id']:04d} · {topic_label(t['topic'])} · <@{t['user_id']}> "
                                    f"{who} · {saved} messages", kind="Tickets", view=v)

    ticket_group = app_commands.Group(name="ticket", description="Tickets (staff)",
                                      default_permissions=discord.Permissions(moderate_members=True))

    @ticket_group.command(name="view", description="Open a closed ticket by number")
    @staff_only()
    async def ticket_view(self, interaction: discord.Interaction, number: int):
        await transcripts.open_viewer(interaction, number)

    @ticket_group.command(name="history", description="Every closed ticket from one member")
    @staff_only()
    async def ticket_history(self, interaction: discord.Interaction, member: discord.User):
        rows = db.q("SELECT * FROM tickets WHERE user_id=? AND status='closed' ORDER BY id DESC", member.id)
        if not rows:
            return await interaction.response.send_message("No closed tickets from them.", ephemeral=True)
        await interaction.response.send_message(f"{len(rows)} ticket(s) from {member.mention}:",
                                                view=transcripts.pick_list(rows), ephemeral=True)

    @ticket_group.command(name="search", description="Find closed tickets by any word in the chat")
    @staff_only()
    async def ticket_search(self, interaction: discord.Interaction, text: str):
        rows = transcripts.search(text)
        if not rows:
            return await interaction.response.send_message("Nothing found.", ephemeral=True)
        await interaction.response.send_message(f"{len(rows)} ticket(s) mention \"{text}\":",
                                                view=transcripts.pick_list(rows), ephemeral=True)

    # ---------- relay ----------
    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        if msg.author.bot:
            return
        if isinstance(msg.channel, discord.DMChannel):
            return await self._from_member(msg)
        inbox = channel(self.guild, "inbox") if self.guild else None
        if inbox and isinstance(msg.channel, discord.Thread) and msg.channel.parent_id == inbox.id:
            await self._from_staff(msg)

    async def _files(self, msg: discord.Message) -> list[discord.File]:
        return [await a.to_file() for a in msg.attachments[:10] if a.size <= 10_000_000]

    async def _from_member(self, msg: discord.Message):
        t = open_ticket_for(msg.author.id)
        if not t:
            last = self._dm_nag.get(msg.author.id, 0)
            if db.now() - last > 600:
                self._dm_nag[msg.author.id] = db.now()
                sup = channel(self.guild, "support")
                await msg.channel.send(f"To get help, open a ticket in {sup.mention if sup else '#support'}.")
            return
        thread = await self.fetch_thread(t["thread_id"])
        if not thread:
            return await msg.channel.send("Your ticket can't be reached right now. Try again in a minute.")
        if thread.archived:
            await thread.edit(archived=False)
        e = embed(description=msg.content[:4000] or "*(attachment)*", colour=discord.Colour(AMBER))
        e.set_author(name=f"{msg.author.display_name} (member)", icon_url=msg.author.display_avatar.url)
        copy = await thread.send(embed=e, files=await self._files(msg))
        link_copy(msg, copy, t["id"], "member")
        await msg.add_reaction("✅")
        db.run("UPDATE tickets SET status='open', last_activity=?, warned=0 WHERE id=?", db.now(), t["id"])
        if t["status"] != "open":
            await self.refresh(t["id"])

    async def _from_staff(self, msg: discord.Message):
        t = ticket_for_thread(msg.channel.id)
        if not t or not is_staff(msg.author) or msg.content.startswith(NOTE_PREFIX):
            return
        if t["status"] == "closed":
            return await msg.reply("This ticket is closed. The member won't see this.", mention_author=False)
        ok = await self.relay_staff(t, msg.author, msg.content, await self._files(msg), src=msg)
        await msg.add_reaction("✅" if ok else "❌")
        if not ok:
            await msg.reply("Couldn't DM the member (DMs closed or they left).", mention_author=False)

    async def relay_staff(self, t, author: discord.Member, content: str, files, src=None) -> bool:
        e = embed(description=content[:4000] or "*(attachment)*", colour=discord.Colour(GREEN))
        e.set_author(name=f"{author.display_name} · Support team", icon_url=author.display_avatar.url)
        sent = await self.send_to_user(t, e, files)
        if not sent:
            return False
        if src is not None:
            link_copy(src, sent, t["id"], "staff")
        now = db.now()
        db.run("UPDATE tickets SET status='waiting', last_activity=?, first_reply=COALESCE(first_reply, ?), "
               "claimed_by=COALESCE(claimed_by, ?) WHERE id=?", now, now, author.id, t["id"])
        await self.refresh(t["id"])
        return True

    # ---------- deletes and edits follow the message to the other side ----------
    async def _copy_of(self, payload_message_id: int):
        row = db.one("SELECT * FROM relay_map WHERE src=?", payload_message_id)
        if not row:
            return None, None
        try:
            ch = self.bot.get_channel(row["dst_channel"]) or await self.bot.fetch_channel(row["dst_channel"])
            return row, await ch.fetch_message(row["dst"])
        except discord.HTTPException:
            return row, None

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        row, copy = await self._copy_of(payload.message_id)
        if not row or not copy:
            return
        try:
            if row["side"] == "staff":            # staff deleted in the inbox -> remove it from the member's DMs
                await copy.delete()
            else:                                  # member deleted their DM -> staff keep it, marked
                e = copy.embeds[0]
                e.set_footer(text="🗑️ The member deleted this message")
                await copy.edit(embed=e)
        except discord.HTTPException as e:
            log.warning("relay delete: %s", e)
        db.run("DELETE FROM relay_map WHERE src=?", payload.message_id)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        content = payload.data.get("content")
        if content is None or payload.data.get("author", {}).get("bot"):
            return
        row, copy = await self._copy_of(payload.message_id)
        if not row or not copy or not copy.embeds:
            return
        e = copy.embeds[0]
        e.description = content[:4000] or "*(attachment)*"
        e.set_footer(text="✏️ edited")
        try:
            await copy.edit(embed=e)
        except discord.HTTPException as err:
            log.warning("relay edit: %s", err)

    # ---------- timers ----------
    @tasks.loop(minutes=5)
    async def timers(self):
        if not self.guild:
            return
        now = db.now()
        team = role_named(self.guild, "Support Team")
        for t in db.q("SELECT * FROM tickets WHERE status!='closed' AND first_reply IS NULL AND nudged=0 AND created<?",
                      now - NUDGE_AFTER):
            thread = await self.fetch_thread(t["thread_id"])
            if thread:
                await thread.send(f"⏰ No reply for 30 minutes. {team.mention if team else 'Support team'}, "
                                  "can someone take this?", allowed_mentions=discord.AllowedMentions(roles=True))
            db.run("UPDATE tickets SET nudged=1 WHERE id=?", t["id"])
        for t in db.q("SELECT * FROM tickets WHERE status='waiting' AND warned=0 AND last_activity<?", now - WARN_AFTER):
            await self.send_to_user(t, embed(f"Still need help with ticket {t['id']:04d}?",
                                             "Reply here if you do. Otherwise it closes by itself in 24 hours.",
                                             colour=discord.Colour(AMBER)))
            db.run("UPDATE tickets SET warned=1 WHERE id=?", t["id"])
        for t in db.q("SELECT * FROM tickets WHERE status='waiting' AND warned=1 AND last_activity<?", now - CLOSE_AFTER):
            await self.close(t, by=None, reason="auto-closed after 48 h without a reply")
        for t in db.q("SELECT * FROM tickets WHERE status='closing' AND last_activity<?", now - WARN_AFTER):
            await self.close(t, by=None, reason="closed, the member didn't answer within 24 h")

    @timers.before_loop
    async def _wait_timers(self):
        await self.bot.wait_until_ready()

    # ---------- staff commands ----------
    @ticket_group.command(name="reply", description="Send a saved reply (inside a ticket)")
    @staff_only()
    async def reply(self, interaction: discord.Interaction, name: str):
        t = ticket_for_thread(interaction.channel_id)
        text = cfg()["support"]["snippets"].get(name)
        if not t or not text:
            return await interaction.response.send_message("Use this inside a ticket post, with a saved reply name.",
                                                           ephemeral=True)
        if t["status"] == "closed":
            return await interaction.response.send_message("This ticket is closed.", ephemeral=True)
        ok = await self.relay_staff(t, interaction.user, text, [])
        await interaction.response.send_message(f"{'Sent' if ok else 'Could not send'} saved reply `{name}`:\n{text}")

    @reply.autocomplete("name")
    async def _reply_names(self, interaction, current: str):
        return [app_commands.Choice(name=f"{k}: {v[:60]}", value=k)
                for k, v in cfg()["support"]["snippets"].items() if current.lower() in k][:25]

    @ticket_group.command(name="close", description="Close this ticket now (inside a ticket)")
    @staff_only()
    async def close_ticket(self, interaction: discord.Interaction):
        t = ticket_for_thread(interaction.channel_id)
        if not t or t["status"] == "closed":
            return await interaction.response.send_message("Use this inside an open ticket post.", ephemeral=True)
        await interaction.response.send_message("Closing and saving the transcript...")
        await self.close(t, by=interaction.user)

    @ticket_group.command(name="stats", description="This month's support numbers")
    @staff_only()
    async def ticket_stats(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self.stats_embed(dt.datetime.now(dt.timezone.utc)),
                                                ephemeral=True)

    def stats_embed(self, month: dt.datetime, title: str | None = None) -> discord.Embed:
        start = month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (start + dt.timedelta(days=32)).replace(day=1)
        a, b = start.timestamp(), end.timestamp()
        total = db.one("SELECT COUNT(*) n FROM tickets WHERE created BETWEEN ? AND ?", a, b)["n"]
        rows = db.q("SELECT claimed_by, COUNT(*) n, AVG(rating) r, AVG(first_reply-created) f FROM tickets "
                    "WHERE status='closed' AND closed BETWEEN ? AND ? AND claimed_by IS NOT NULL "
                    "GROUP BY claimed_by ORDER BY n DESC LIMIT 10", a, b)
        e = embed(title or f"Support · {start:%B %Y}",
                  f"{total} tickets opened · {db.kv_get('triage_solved', 0)} solved by instant answers (all time)")
        lines = []
        for i, r in enumerate(rows, 1):
            rating = f"{r['r']:.1f}★" if r["r"] else "no ratings"
            lines.append(f"**{i}.** <@{r['claimed_by']}> · {r['n']} closed · {rating} · "
                         f"{round((r['f'] or 0) / 60)} min first reply")
        e.add_field(name="Leaderboard", value="\n".join(lines) or "No closed tickets yet.", inline=False)
        return e

    @tasks.loop(hours=6)
    async def monthly(self):
        now = dt.datetime.now(dt.timezone.utc)
        key = f"{now:%Y-%m}"
        if now.day != 1 or db.kv_get("stats_posted") == key or not self.bot.is_ready():
            return
        last_month = now.replace(day=1) - dt.timedelta(days=1)
        ch = channel(self.guild, "staff_chat")
        if ch:
            await ch.send(embed=self.stats_embed(last_month, f"Support leaderboard · {last_month:%B %Y}"),
                          allowed_mentions=discord.AllowedMentions.none())
            db.kv_set("stats_posted", key)

    @monthly.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Tickets(bot))
