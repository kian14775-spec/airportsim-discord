"""Inbox tickets: the member talks to the bot in DMs, staff work in one forum (#inbox).

Flow: #support menu -> pick topic -> short form -> instant answer if the FAQ matches
("fix it first") -> ticket post in #inbox with the player's profile -> messages are
relayed both ways. Staff messages starting with // stay internal (notes).
Tags follow the state automatically: Open (waiting on staff), Waiting (on member),
Claimed, Dev team, Appeal, Closed. Closed posts are the transcripts.
"""
from __future__ import annotations

import datetime as dt

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .. import db, opencloud
from ..core import (cfg, channel, embed, is_staff, log, log_staff, role_named, roblox_group_rank,
                    roblox_headshot, roblox_user, roblox_user_by_name, staff_only)
from ..panels import ensure_panel

NOTE_PREFIX = "//"
GREEN, RED, AMBER, GREY = 0x2ECC71, 0xE74C3C, 0xF1C40F, 0x95A5A6


def topics() -> dict[str, dict]:
    return {t["key"]: t for t in cfg()["support"]["topics"]}


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


async def set_tags(thread: discord.Thread, *names: str) -> None:
    forum = thread.parent
    if not isinstance(forum, discord.ForumChannel):
        return
    tags = [t for t in forum.available_tags if t.name in names][:5]
    try:
        await thread.edit(applied_tags=tags)
    except discord.HTTPException as e:
        log.warning("ticket tags: %s", e)


def state_tags(t) -> list[str]:
    tags = ["Closed"] if t["status"] == "closed" else ["Waiting" if t["status"] == "waiting" else "Open"]
    if t["claimed_by"] and t["status"] != "closed":
        tags.append("Claimed")
    if t["topic"] == "appeal":
        tags.append("Appeal")
    if t["topic"] == "dev" or db.kv_get(f"ticket_dev:{t['id']}"):
        tags.append("Dev team")
    return tags


async def profile_embed(guild: discord.Guild, user: discord.abc.User, roblox_hint: str | None = None) -> discord.Embed:
    member = guild.get_member(user.id)
    e = embed(f"{user.display_name}", colour=discord.Colour(GREY))
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
        e.add_field(name="Roblox", value="Not verified", inline=False)
    past = db.one("SELECT COUNT(*) n FROM tickets WHERE user_id=? AND status='closed'", user.id)["n"]
    e.set_footer(text=f"{past} past ticket(s)")
    return e


# ---------- persistent buttons (survive restarts) ----------
class TicketButton(discord.ui.DynamicItem[discord.ui.Button], template=r"asbot:t:(?P<action>[a-z]+):(?P<id>\d+)"):
    LABELS = {"claim": ("Claim", discord.ButtonStyle.primary), "dev": ("Send to dev team", discord.ButtonStyle.secondary),
              "close": ("Close", discord.ButtonStyle.danger), "unban": ("Unban in game", discord.ButtonStyle.success),
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
        if t is None:
            return await interaction.response.send_message("Ticket not found.", ephemeral=True)
        if self.action == "uclose":
            if interaction.user.id != t["user_id"]:
                return await interaction.response.send_message("Not your ticket.", ephemeral=True)
            await interaction.response.defer()
            return await cog.close(t, by=interaction.user, by_member=True)
        member = cog.guild.get_member(interaction.user.id)
        if not member or not is_staff(member):
            return await interaction.response.send_message("Staff only.", ephemeral=True)
        if t["status"] == "closed":
            return await interaction.response.send_message("Already closed.", ephemeral=True)
        if self.action == "claim":
            db.run("UPDATE tickets SET claimed_by=? WHERE id=?", member.id, t["id"])
            await interaction.response.send_message(f"Claimed by {member.mention}.",
                                                    allowed_mentions=discord.AllowedMentions.none())
            await cog.refresh_tags(t["id"])
        elif self.action == "dev":
            db.kv_set(f"ticket_dev:{t['id']}", True)
            dev = role_named(cog.guild, cfg().get("dev_role_name", "Developer"))
            await interaction.response.send_message(
                f"{dev.mention if dev else 'Dev team'}: {member.display_name} needs you on this one.",
                allowed_mentions=discord.AllowedMentions(roles=True))
            await cog.refresh_tags(t["id"])
        elif self.action == "close":
            await interaction.response.defer()
            await cog.close(t, by=member)
        elif self.action == "unban":
            link = db.kv_get(f"ticket_roblox:{t['id']}")
            if not link:
                return await interaction.response.send_message("No Roblox account on this appeal.", ephemeral=True)
            ok = await opencloud.unban(link)
            await interaction.response.send_message(
                f"Unbanned in game by {member.mention}." if ok else "Unban failed (Open Cloud key missing or no access).",
                allowed_mentions=discord.AllowedMentions.none())
            if ok:
                await cog.send_to_user(t, embed("You've been unbanned",
                                                "Your appeal was accepted. You can join the game again.",
                                                colour=discord.Colour(GREEN)))
                await log_staff(cog.guild, f"Game unban for `{link}` by {member.mention} (ticket #{t['id']})")


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
        cog: Tickets = interaction.client.get_cog("Tickets")
        thread = cog.guild.get_thread(t["thread_id"]) or await cog.fetch_thread(t["thread_id"])
        if thread:
            await thread.send(f"Member rated this ticket {'★' * self.score}")


def staff_view(t) -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    for a in ("claim", "dev", "close"):
        v.add_item(TicketButton(a, t["id"]))
    if t["topic"] == "appeal":
        v.add_item(TicketButton("unban", t["id"]))
    return v


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
            return await interaction.response.send_message(
                embed=embed("Try this first", answer), view=view, ephemeral=True)
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
        blocked = await cog.blocked_reason(interaction.user)
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
        cog: Tickets = interaction.client.get_cog("Tickets")
        blocked = await cog.blocked_reason(interaction.user)
        if blocked:
            return await interaction.response.send_message(blocked, ephemeral=True)
        await interaction.response.send_modal(AppealModal())

    @discord.ui.button(label="Apply for staff", style=discord.ButtonStyle.secondary,
                       custom_id="asbot:support:apply", row=1)
    async def apply(self, interaction: discord.Interaction, _):
        apps = interaction.client.get_cog("Applications")
        await apps.start(interaction)


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(SupportView())
        bot.add_dynamic_items(TicketButton, RateButton)
        self._dm_nag: dict[int, float] = {}
        self.monthly.start()

    def cog_unload(self):
        self.monthly.cancel()

    @property
    def guild(self) -> discord.Guild:
        return self.bot.get_guild(self.bot.guild_id)

    async def fetch_thread(self, tid: int):
        try:
            return await self.bot.fetch_channel(tid)
        except discord.HTTPException:
            return None

    @commands.Cog.listener()
    async def on_ready(self):
        await ensure_panel("support", channel(self.guild, "support"), view=SupportView(), embeds=[
            embed("Support", "Pick a topic below and tell us what's wrong. You'll often get an answer straight "
                             "away. If not, a ticket opens and the team replies **in your DMs**.\n\n"
                             "Make sure DMs from server members are on.")])

    async def blocked_reason(self, user) -> str | None:
        member = self.guild.get_member(user.id)
        bl = role_named(self.guild, "Ticket Blacklisted")
        if member and bl and bl in member.roles:
            return "You can't open tickets right now."
        t = open_ticket_for(user.id)
        if t:
            return f"You already have ticket #{t['id']} open. Reply to the bot in your DMs."
        return None

    async def open(self, interaction: discord.Interaction, topic_key: str, text: str, extra: str, username: str):
        user = interaction.user
        if await self.blocked_reason(user):
            return await interaction.followup.send(await self.blocked_reason(user), ephemeral=True)
        inbox = channel(self.guild, "inbox")
        if not isinstance(inbox, discord.ForumChannel):
            return await interaction.followup.send("Support isn't set up yet. Try again later.", ephemeral=True)
        tid = db.run("INSERT INTO tickets(user_id, topic, created) VALUES(?, ?, ?)", user.id, topic_key, db.now())
        label = "Ban appeal" if topic_key == "appeal" else topics().get(topic_key, {}).get("label", topic_key)
        # DM first: no ticket if we can't reach them
        pos, eta = queue_position(tid), eta_minutes()
        hello = embed(f"Ticket #{tid} opened · {label}",
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
        issue = embed(label, text[:4000], colour=discord.Colour(AMBER))
        if extra:
            issue.add_field(name="Details", value=extra[:1000], inline=False)
        rid = None
        if topic_key == "appeal" and username:
            found = await roblox_user_by_name(username)
            if found:
                rid = found["id"]
                db.kv_set(f"ticket_roblox:{tid}", rid)
                ban = await opencloud.get_ban(rid)
                issue.add_field(name="Ban on record", value=(
                    f"{ban.get('displayReason') or 'no public reason'}\nPrivate: {ban.get('privateReason') or '-'}"
                    if ban else ("Not banned (or Open Cloud not set up)" if opencloud.available()
                                 else "Open Cloud key not set up, check by hand")), inline=False)
        prof = await profile_embed(self.guild, user, username or None)
        t = ticket(tid)
        tags = [tg for tg in inbox.available_tags if tg.name in state_tags(t)]
        created = await inbox.create_thread(
            name=f"#{tid} · {label} · {user.name}"[:100], embeds=[prof, issue], applied_tags=tags,
            view=staff_view(t), allowed_mentions=discord.AllowedMentions.none())
        db.run("UPDATE tickets SET thread_id=? WHERE id=?", created.thread.id, tid)
        await interaction.followup.send(f"Ticket #{tid} is open. Check your DMs.", ephemeral=True)

    async def refresh_tags(self, tid: int):
        t = ticket(tid)
        thread = self.guild.get_thread(t["thread_id"]) or await self.fetch_thread(t["thread_id"])
        if thread:
            if thread.archived and t["status"] != "closed":
                await thread.edit(archived=False)
            await set_tags(thread, *state_tags(t))

    async def send_to_user(self, t, e: discord.Embed, files=None) -> bool:
        user = self.bot.get_user(t["user_id"]) or await self.bot.fetch_user(t["user_id"])
        try:
            await user.send(embed=e, files=files or [])
            return True
        except discord.HTTPException:
            return False

    async def close(self, t, by: discord.abc.User, by_member: bool = False):
        db.run("UPDATE tickets SET status='closed', closed=? WHERE id=?", db.now(), t["id"])
        thread = self.guild.get_thread(t["thread_id"]) or await self.fetch_thread(t["thread_id"])
        who = "the member" if by_member else by.display_name
        if thread:
            await thread.send(f"Closed by {who}.")
            await set_tags(thread, *state_tags(ticket(t["id"])))
            await thread.edit(archived=True, locked=True)
        user = self.bot.get_user(t["user_id"]) or await self.bot.fetch_user(t["user_id"])
        try:
            await user.send(embed=embed(f"Ticket #{t['id']} closed",
                                        "Thanks for reaching out. How did we do?", colour=discord.Colour(GREY)),
                            view=rating_view(t["id"]))
        except discord.HTTPException:
            pass

    # ---------- relay ----------
    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        if msg.author.bot:
            return
        if isinstance(msg.channel, discord.DMChannel):
            return await self._from_member(msg)
        if isinstance(msg.channel, discord.Thread) and msg.channel.parent_id == (channel(self.guild, "inbox") or
                                                                                    discord.Object(0)).id:
            await self._from_staff(msg)

    async def _files(self, msg: discord.Message) -> list[discord.File]:
        out = []
        for a in msg.attachments[:10]:
            if a.size <= 10_000_000:
                out.append(await a.to_file())
        return out

    async def _from_member(self, msg: discord.Message):
        t = open_ticket_for(msg.author.id)
        if not t:
            last = self._dm_nag.get(msg.author.id, 0)
            if db.now() - last > 600:
                self._dm_nag[msg.author.id] = db.now()
                sup = channel(self.guild, "support")
                await msg.channel.send(f"To get help, open a ticket in {sup.mention if sup else '#support'}.")
            return
        thread = self.guild.get_thread(t["thread_id"]) or await self.fetch_thread(t["thread_id"])
        if not thread:
            return await msg.channel.send("Your ticket can't be reached right now. Try again in a minute.")
        if thread.archived:
            await thread.edit(archived=False)
        e = embed(description=msg.content[:4000] or "*(attachment)*", colour=discord.Colour(AMBER))
        e.set_author(name=msg.author.display_name, icon_url=msg.author.display_avatar.url)
        await thread.send(embed=e, files=await self._files(msg))
        await msg.add_reaction("✅")
        if t["status"] != "open":
            db.run("UPDATE tickets SET status='open' WHERE id=?", t["id"])
            await self.refresh_tags(t["id"])

    async def _from_staff(self, msg: discord.Message):
        t = ticket_for_thread(msg.channel.id)
        if not t or not is_staff(msg.author) or msg.content.startswith(NOTE_PREFIX):
            return
        if t["status"] == "closed":
            return await msg.reply("This ticket is closed. The member won't see this.", mention_author=False)
        ok = await self.relay_staff(t, msg.author, msg.content, await self._files(msg))
        await msg.add_reaction("✅" if ok else "❌")
        if not ok:
            await msg.reply("Couldn't DM the member (DMs closed or they left).", mention_author=False)

    async def relay_staff(self, t, author: discord.Member, content: str, files) -> bool:
        e = embed(description=content[:4000] or "*(attachment)*", colour=discord.Colour(GREEN))
        e.set_author(name=f"{author.display_name} · Support team", icon_url=author.display_avatar.url)
        if not await self.send_to_user(t, e, files):
            return False
        updates = ["status='waiting'"]
        if t["first_reply"] is None:
            updates.append(f"first_reply={db.now()}")
        if t["claimed_by"] is None:
            updates.append(f"claimed_by={author.id}")
        db.run(f"UPDATE tickets SET {', '.join(updates)} WHERE id=?", t["id"])
        await self.refresh_tags(t["id"])
        return True

    # ---------- staff commands ----------
    @app_commands.command(description="Send a saved reply to the member (use inside a ticket)")
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

    @app_commands.command(description="Close this ticket")
    @staff_only()
    async def close_ticket(self, interaction: discord.Interaction):
        t = ticket_for_thread(interaction.channel_id)
        if not t or t["status"] == "closed":
            return await interaction.response.send_message("Use this inside an open ticket post.", ephemeral=True)
        await interaction.response.send_message("Closing...")
        await self.close(t, by=interaction.user)

    @app_commands.command(description="Ticket stats for this month (staff)")
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
        solved = db.kv_get("triage_solved", 0)
        e = embed(title or f"Support · {start:%B %Y}",
                  f"{total} tickets opened · {solved} solved by instant answers (all time)")
        lines = []
        for i, r in enumerate(rows, 1):
            rating = f"{r['r']:.1f}★" if r["r"] else "no ratings"
            first = f"{round((r['f'] or 0) / 60)} min first reply"
            lines.append(f"**{i}.** <@{r['claimed_by']}> · {r['n']} closed · {rating} · {first}")
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
