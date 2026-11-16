"""Inbox tickets: the member talks to the bot in DMs, staff work in one forum (#inbox).

Flow: support menu -> topic -> short form -> ticket post in #inbox -> messages relayed both ways.
Staff messages starting with // stay internal (notes).

Support helper (no AI, nothing paid): when an instant answer matches, the bot greets the member
in DM, offers the fix with "That fixed it / Still broken / Talk to a person" and keeps matching
their replies. Staff see every line in the post under "🎧 Support Agent is on it, don't reply yet"; it hands
over to a person when the member asks, nothing else matches, or staff type in the post.

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

from .. import db, names, opencloud
from ..core import (cfg, channel, embed, guarded, is_staff, linked_roblox, log, log_staff, role, role_named,
                    roblox_group_rank, roblox_headshot, roblox_user, roblox_user_by_name, staff_only)
from ..renamer import rename_soon
from .. import cards, faq, ticketlist, transcripts, ui
from .answers import STOPWORDS, AnswerModal, guess_keywords

NOTE_PREFIX = "//"
from ..ui import BAD as RED, BRAND as BLUE, GOOD as GREEN, QUIET as GREY, WAIT as AMBER
NUDGE_AFTER = 30 * 60
WARN_AFTER = 24 * 3600
CLOSE_AFTER = 48 * 3600
PRIORITIES = {"low": "Low", "normal": "Normal", "urgent": "Urgent"}
CARD_FOOTER = "Type here to reply to the member · start a message with // for a staff-only note"
HUMAN_WORDS = {"human", "person", "staff", "someone", "agent", "mod", "admin", "moderator", "somebody"}
THANKS_WORDS = {"thanks", "thank", "ty", "tysm", "thx", "fixed", "worked", "sorted", "solved"}
NOT_WORDS = {"not", "didn't", "didnt", "still", "no", "isn't", "doesn't"}
AGENT = "Support Agent"   # the name + headset icon on support messages only (the bot's own avatar never changes)
AGENT_ICON: str | None = None   # app-emoji URL of assets/support_agent.png, set on start
HELPER_ON = "🎧 **The Support Agent is talking to them. Don't reply yet** (or press **Take over**)."


def topics() -> dict[str, dict]:
    return {t["key"]: t for t in cfg()["support"]["topics"]}


def topic_label(key: str) -> str:
    return "Ban appeal" if key == "appeal" else topics().get(key, {}).get("label", key)


def ticket(tid: int):
    return db.one("SELECT * FROM tickets WHERE id=?", tid)


def open_ticket_for(user_id: int):
    return db.one("SELECT * FROM tickets WHERE user_id=? AND status!='closed' ORDER BY id DESC", user_id)


def drop_ticket(tid: int) -> None:
    """Undo a ticket that never got its inbox post, so it can't block the member."""
    db.run("DELETE FROM tickets WHERE id=?", tid)
    db.run("DELETE FROM kv WHERE key IN (?, ?, ?)", f"tbot:{tid}", f"tcard:{tid}", f"ticket_roblox:{tid}")


def is_command(text: str) -> bool:
    """`?close`, `?claim` ... are staff commands (asbot/cogs/staffcmds.py), never relayed to the member."""
    from .staffcmds import COMMANDS
    return text.startswith("?") and text[1:].split(" ", 1)[0].lower() in COMMANDS


def known_issue(text: str) -> str | None:
    """A known issue from the support card (/staff -> Known issues) that the member's text talks about."""
    from .news import status
    words = {faq.stem(w) for w in faq.WORD.findall(text.lower())}
    for issue in status().get("issues", []):
        keys = {faq.stem(w) for w in faq.WORD.findall(issue.lower()) if len(w) > 3 and w not in STOPWORDS
                and w not in ("issue", "issues", "problem", "problems")}
        if keys and len(keys & words) >= min(2, len(keys)):
            return issue
    return None


def issue_reply(issue: str) -> str:
    from .news import status
    nxt = status().get("next_update")
    return (f"Good news, we already know about this one: **{issue}**\n\nThe team is working on a fix"
            + (f" (next update: {nxt})" if nxt else "") + ". You don't need to do anything, and we'll let you know "
            "when it's fixed. Is that what you're seeing?")


def card_data(tid: int) -> dict:
    """What the staff card shows that isn't in the tickets table (problem, member profile, helper line)."""
    return db.kv_get(f"tcard:{tid}", {})


def set_card(tid: int, **changes) -> None:
    db.kv_set(f"tcard:{tid}", {**card_data(tid), **changes})


def helper_state(tid: int) -> dict:
    return db.kv_get(f"tbot:{tid}", {"shown": [], "said": ""})


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


def state_of(t) -> tuple[str, str, int]:
    """(dot, words, colour) for the title and control card."""
    if t["status"] == "closed":
        return "⚫", "Closed", GREY
    if t["status"] == "closing":
        return "⏳", "Waiting for the member to confirm", AMBER
    if t["status"] == "bot":
        return "🎧", "Support Agent is on it", ui.SLATE
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
    tags = ["Waiting" if t["status"] in ("waiting", "closing", "bot") else "Open"]
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
    e.add_field(name="Claimed by", value=names.who(t["claimed_by"]) if t["claimed_by"] else "Nobody yet")
    e.add_field(name="Opened", value=f"<t:{int(t['created'])}:R>")
    last = t["last_activity"] or t["created"]
    e.add_field(name="Last message", value=f"<t:{int(last)}:R>")
    e.add_field(name="Member", value=names.who(t["user_id"]))
    e.set_footer(text=CARD_FOOTER)
    return e


async def gather_profile(guild: discord.Guild, user: discord.abc.User, typed: str | None = None) -> dict:
    """Everything the member box on the staff card shows, looked up once when the ticket opens."""
    member = guild.get_member(user.id)
    p = {"id": user.id, "name": user.name, "created": int(user.created_at.timestamp()),
         "joined": int(member.joined_at.timestamp()) if member and member.joined_at else None,
         "verified_role": bool(member and role(guild, "verified") in member.roles), "roblox": None}
    try:
        found = await linked_roblox(guild, user)
        how = found[2] if found else None
        if not found and typed:
            hit = await roblox_user_by_name(typed)
            found, how = ((hit["id"], hit["name"], "typed, not verified") if hit else None), "typed, not verified"
        if found:
            rid, rname = found[0], found[1]
            info = await roblox_user(rid) or {}
            ban = await opencloud.get_ban(rid)
            p["roblox"] = {"id": rid, "name": rname, "display": info.get("displayName") or rname, "how": how,
                           "since": (info.get("created") or "")[:10],
                           "rank": await roblox_group_rank(rid, cfg()["game"]["group_id"]),
                           "ban": (ban.get("displayReason") or "no reason given") if ban else None,
                           "thumb": await roblox_headshot(rid)}
    except Exception as e:  # noqa: BLE001 - Roblox being slow must never block a ticket
        log.warning("ticket profile: %s", e)
    return p


def member_text(p: dict, past: int) -> str:
    lines = [f"{names.who(p['id'])} · `{p['name']}`"]
    bits = [f"Discord account <t:{p['created']}:R>"]
    if p.get("joined"):
        bits.append(f"joined <t:{p['joined']}:R>")
    bits.append(f"{past} past ticket{'s' if past != 1 else ''}" if past else "first ticket")
    lines.append(" · ".join(bits))
    r = p.get("roblox")
    if r:
        mark = {"verified": "✅ verified", "verified with Bloxlink": "✅ verified (Bloxlink)",
                "from their nickname": "☑️ from their nickname"}.get(r["how"], "⚠️ typed, not verified")
        name = r["name"] if r["display"] == r["name"] else f"{r['display']} (@{r['name']})"
        lines.append(f"**Roblox** [{name}](https://www.roblox.com/users/{r['id']}/profile) · {mark}")
        extra = [f"ID `{r['id']}`", f"group: {r['rank'] or 'not in group'}"]
        if r.get("since"):
            extra.append(f"since {r['since']}")
        lines.append("-# " + " · ".join(extra))
        if r.get("ban"):
            lines.append(f"**Game banned:** {r['ban']}")
    else:
        lines.append("**Roblox** not linked" + (" (verified role, but no account found)" if p.get("verified_role")
                                                else ""))
    return "\n".join(lines)


def suggestion(t, d: dict) -> dict | None:
    """Best instant answer a person could send in one click (not while the agent is on, not one it tried)."""
    if t["status"] in ("bot", "closing", "closed") or d.get("suggest_sent"):
        return None
    return faq.match(f"{d.get('problem', '')} {d.get('extra', '')}", skip=set(d.get("tried") or []))


def staff_card(t) -> discord.ui.LayoutView:
    """The one card on top of every ticket post: state, member, problem, helper line, buttons."""
    dot, words, colour = state_of(t)
    d = card_data(t["id"])
    p = d.get("profile") or {"id": t["user_id"], "name": str(t["user_id"]), "created": int(t["created"])}
    past = db.one("SELECT COUNT(*) n FROM tickets WHERE user_id=? AND status='closed'", t["user_id"])["n"]
    last = t["last_activity"] or t["created"]
    claimed = names.who(t["claimed_by"]) if t["claimed_by"] else "nobody"
    head = cards.title(f"{dot} {t['id']:04d} · {topic_label(t['topic'])}",
                       f"**{words}**\n-# opened <t:{int(t['created'])}:R> · last message <t:{int(last)}:R> · "
                       f"priority {PRIORITIES.get(t['priority'], 'Normal').lower()} · claimed by {claimed}")
    who = cards.section("Member", member_text(p, past))
    thumb = (p.get("roblox") or {}).get("thumb")
    member_part = discord.ui.Section(who, accessory=discord.ui.Thumbnail(thumb)) if thumb else who
    problem = (d.get("problem") or "-")[:1500]
    if d.get("extra"):
        problem += f"\n**Details** · {d['extra'][:300]}"
    row1 = [TicketButton(a, t["id"]) for a in ("claim", "close", "force", "dev")]
    row2 = []
    if t["status"] == "bot":
        row2.append(TicketButton("takeover", t["id"]))
    sug = suggestion(t, d)
    if sug:
        row2.append(TicketButton("suggest", t["id"]))
    row2.append(TicketButton("save", t["id"]))
    if t["topic"] == "appeal":
        row2.append(TicketButton("unban", t["id"]))
    if past:
        row2.append(transcripts.HistoryButton(t["user_id"], past))
    notes = [n for n in (d.get("helper"), d.get("flag")) if n]
    if sug:
        notes.append(f"-# 💡 Suggested fix: {faq.label(sug)} · **Send suggested fix** sends it as you")
    view = cards.CardView(cards.container(
        head,
        cards.divider(),
        member_part,
        cards.divider(),
        cards.section("Problem", problem),
        cards.text("\n".join(notes)) if notes else None,
        cards.divider(),
        cards.row(*row1),
        cards.row(*row2),
        cards.row(PrioritySelect(t["id"])),
        cards.small(CARD_FOOTER),
        accent=colour))
    return view


def link_copy(src: discord.Message, copy: discord.Message, tid: int, side: str) -> None:
    """Remember that `copy` is the relayed version of `src` (side = who wrote the original)."""
    db.run("INSERT OR REPLACE INTO relay_map(src, dst_channel, dst, ticket_id, side) VALUES(?, ?, ?, ?, ?)",
           src.id, copy.channel.id, copy.id, tid, side)


# ---------- persistent controls (survive restarts) ----------
class TicketButton(discord.ui.DynamicItem[discord.ui.Button], template=r"asbot:t:(?P<action>[a-z]+):(?P<id>\d+)"):
    LABELS = {"claim": ("Claim", discord.ButtonStyle.primary), "dev": ("Send to dev team", discord.ButtonStyle.secondary),
              "close": ("Ask to close", discord.ButtonStyle.success), "force": ("Close now", discord.ButtonStyle.danger),
              "unban": ("Unban in game", discord.ButtonStyle.success),
              "uclose": ("Close my ticket", discord.ButtonStyle.secondary),
              "save": ("Save fix as answer", discord.ButtonStyle.secondary),
              "suggest": ("Send suggested fix", discord.ButtonStyle.success),
              "takeover": ("Take over", discord.ButtonStyle.primary)}

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
        if self.action == "suggest":
            sug = suggestion(t, card_data(t["id"]))
            if not sug:
                return await interaction.response.send_message("No suggestion for this one any more.", ephemeral=True)
            await interaction.response.send_message(f"{member.mention} sent {faq.label(sug)}:\n>>> {sug['answer']}",
                                                    allowed_mentions=discord.AllowedMentions.none())
            set_card(t["id"], suggest_sent=True)
            if await cog.relay_staff(t, member, sug["answer"], []):
                faq.counted(sug)
            return
        if self.action == "save":
            d = card_data(t["id"])
            last = db.kv_get(f"tlast:{t['id']}", "")
            return await interaction.response.send_modal(
                AnswerModal(guess_keywords(d.get("problem", "")), last, ticket_id=t["id"]))
        if self.action == "takeover":
            if t["status"] != "bot":
                return await interaction.response.send_message("The Support Agent isn't on this one.", ephemeral=True)
            await interaction.response.send_message(f"{member.mention} took over from the Support Agent.",
                                                    allowed_mentions=discord.AllowedMentions.none())
            db.run("UPDATE tickets SET claimed_by=COALESCE(claimed_by, ?) WHERE id=?", member.id, t["id"])
            return await cog.handoff(t, "taken over", by=member)
        if self.action == "claim":
            db.run("UPDATE tickets SET claimed_by=? WHERE id=?", member.id, t["id"])
            await interaction.response.send_message(f"You claimed ticket {t['id']:04d}.", ephemeral=True)
            await cog.refresh(t["id"])
            await log_staff(cog.guild, f"Ticket {t['id']:04d} claimed by {member.mention}", kind="Tickets",
                            event="ticket_claimed")
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
            await interaction.response.defer(thinking=True)   # Open Cloud can take longer than Discord waits
            ok = await opencloud.unban(rid)
            await interaction.followup.send(
                f"Unbanned in game by {member.mention}." if ok else "Unban failed (Open Cloud key missing, no access "
                                                                    "or Roblox didn't answer).",
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
        db.run("UPDATE tickets SET status='open', last_activity=?, handed=COALESCE(handed, ?) WHERE id=?",
               db.now(), db.now(), t["id"])
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
        await log_staff(cog.guild, f"Ticket {self.tid:04d} rated {'★' * self.score} by the member", kind="Tickets",
                        event="ticket_rated")


class AssistButton(discord.ui.DynamicItem[discord.ui.Button], template=r"asbot:ta:(?P<id>\d+):(?P<ans>fixed|not|human|known)"):
    """The member's buttons under a support helper suggestion (in their DMs)."""
    LABELS = {"fixed": ("That fixed it", discord.ButtonStyle.success),
              "known": ("Yes, that's it", discord.ButtonStyle.success),
              "not": ("Still broken", discord.ButtonStyle.secondary),
              "human": ("Talk to a person", discord.ButtonStyle.secondary)}

    def __init__(self, tid: int, ans: str):
        label, style = self.LABELS[ans]
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=f"asbot:ta:{tid}:{ans}"))
        self.tid, self.ans = tid, ans

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]), match["ans"])

    async def callback(self, interaction: discord.Interaction):
        cog: Tickets = interaction.client.get_cog("Tickets")
        t = ticket(self.tid)
        if not t or t["user_id"] != interaction.user.id or t["status"] != "bot":
            return await interaction.response.send_message("A person is already looking at this one, just reply "
                                                           "here.", ephemeral=True)
        await interaction.response.defer()
        try:
            await interaction.message.edit(view=None)   # one answer per suggestion
        except discord.HTTPException:
            pass
        await cog.member_said(t, self.LABELS[self.ans][0])
        if self.ans in ("fixed", "known"):
            await cog.helper_fixed(t)
        elif self.ans == "not":
            await cog.helper_next(t, "")
        else:
            await cog.handoff(t, "the member asked for a person")


def helper_view(tid: int) -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    for a in ("fixed", "not", "human"):
        v.add_item(AssistButton(tid, a))
    return v


class SaveFixButton(discord.ui.DynamicItem[discord.ui.Button], template=r"asbot:tfa:(?P<id>\d+)"):
    """On a closed ticket in the Tickets log: no instant answer covered it, so offer to save the fix."""
    def __init__(self, tid: int):
        super().__init__(discord.ui.Button(label="Save the fix as an instant answer", style=discord.ButtonStyle.primary,
                                           custom_id=f"asbot:tfa:{tid}"))
        self.tid = tid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("Staff only.", ephemeral=True)
        await interaction.response.send_modal(AnswerModal(guess_keywords(card_data(self.tid).get("problem", "")),
                                                          db.kv_get(f"tlast:{self.tid}", ""), ticket_id=self.tid))


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
    e.add_field(name="Member", value=names.who(t["user_id"]))
    e.add_field(name="Handled by", value=names.who(t["claimed_by"]) if t["claimed_by"] else "Nobody claimed")
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
    def __init__(self, topic: dict, known_roblox: bool = False):
        super().__init__(title=topic["label"][:45])
        self.topic = topic
        extra = {"purchase": "Item name and when you bought it", "report": "Their Roblox username",
                 "bug": "Where in the game it happens"}.get(topic["key"])
        self.details = discord.ui.TextInput(label="What's going on?", style=discord.TextStyle.paragraph,
                                            min_length=10, max_length=1500)
        self.add_item(self.details)
        self.username = None
        if not known_roblox:   # verified members aren't asked: the card already shows their account
            self.username = discord.ui.TextInput(label="Your Roblox username", required=False, max_length=20)
            self.add_item(self.username)
        self.extra = None
        if extra:
            self.extra = discord.ui.TextInput(label=extra, required=False, max_length=200)
            self.add_item(self.extra)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.client.get_cog("Tickets").open(
            interaction, self.topic["key"], self.details.value, self.extra.value if self.extra else "",
            self.username.value if self.username else "")


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
        verified = role(interaction.guild, "verified")
        known = bool(db.one("SELECT 1 FROM links WHERE discord_id=?", interaction.user.id)
                     or (verified and verified in getattr(interaction.user, "roles", [])))
        await interaction.response.send_modal(IssueModal(topics()[self.values[0]], known))


class AppealButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Appeal a game ban", style=discord.ButtonStyle.secondary,
                         custom_id="asbot:support:appeal")

    async def callback(self, interaction: discord.Interaction):
        blocked = interaction.client.get_cog("Tickets").blocked_reason(interaction.user)
        if blocked:
            return await interaction.response.send_message(blocked, ephemeral=True)
        await interaction.response.send_modal(AppealModal())


class ApplyButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Apply for staff", style=discord.ButtonStyle.secondary,
                         custom_id="asbot:support:apply")

    async def callback(self, interaction: discord.Interaction):
        await interaction.client.get_cog("Applications").start(interaction)


class SupportCard(cards.CardView):
    """#support: banner, steps, the topic menu, then appeals and applications. The edge is calm grey
    and only turns amber or red when the game has problems."""

    def __init__(self, status_line: str = "", accent: int | None = None):
        super().__init__(cards.container(
            cards.title("Support", "Stuck, found a bug, or missing a purchase? We'll sort it out."),
            cards.small(status_line) if status_line else None,
            cards.divider(),
            cards.text("**1.** Pick a topic below\n"
                       "**2.** Describe the problem in a sentence or two\n"
                       "**3.** Our Support Agent replies in your DMs straight away, and a person joins if it can't fix it"),
            cards.row(TopicSelect()),
            cards.divider(),
            cards.section("Something else", "Banned in game and think it was a mistake? Want to join the team?"),
            cards.row(AppealButton(), ApplyButton()),
            cards.divider(),
            cards.small("Keep DMs from server members on, and please don't DM staff directly."),
            accent=accent if accent is not None else ui.SLATE, banner_name="support"))


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(SupportCard())
        bot.add_dynamic_items(TicketButton, RateButton, PrioritySelect, ConfirmClose, AssistButton,
                              transcripts.ViewButton, transcripts.HistoryButton, SaveFixButton,
                              ticketlist.BrowseButton)
        self._dm_nag: dict[int, float] = {}
        self.save_menu = app_commands.ContextMenu(name="Save as instant answer", callback=self.save_answer)
        self.save_menu.default_permissions = discord.Permissions(moderate_members=True)
        bot.tree.add_command(self.save_menu)
        self.monthly.start()
        self.timers.start()

    def cog_unload(self):
        self.monthly.cancel()
        self.timers.cancel()
        self.bot.tree.remove_command(self.save_menu.name, type=self.save_menu.type)

    async def save_answer(self, interaction: discord.Interaction, message: discord.Message):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("Staff only.", ephemeral=True)
        text = message.content or (message.embeds[0].description if message.embeds else "") or ""
        if text.startswith(NOTE_PREFIX):
            text = text[len(NOTE_PREFIX):].strip()
        if not text:
            return await interaction.response.send_message("That message has no text to save.", ephemeral=True)
        t = ticket_for_thread(interaction.channel_id) if isinstance(interaction.channel, discord.Thread) else None
        problem = card_data(t["id"]).get("problem", "") if t else ""
        await interaction.response.send_modal(AnswerModal(guess_keywords(problem or text), text,
                                                          ticket_id=t["id"] if t else None))

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
        await self.ensure_agent_icon()
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

    async def ensure_agent_icon(self):
        """Upload the headset icon once as an app emoji: a permanent link only support messages use."""
        global AGENT_ICON
        from ..paths import ROOT
        try:
            emoji = next((e for e in await self.bot.fetch_application_emojis() if e.name == "support_agent"), None)
            if emoji is None:
                emoji = await self.bot.create_application_emoji(
                    name="support_agent", image=(ROOT / "assets" / "support_agent.png").read_bytes())
            AGENT_ICON = emoji.url
        except (discord.HTTPException, OSError) as e:
            log.warning("agent icon: %s", e)

    async def ensure_inbox_guide(self):
        inbox = channel(self.guild, "inbox")
        if not isinstance(inbox, discord.ForumChannel):
            return
        view = cards.CardView(cards.container(
            cards.title("How the inbox works", "Every post here is one open ticket. Members talk to the bot in "
                                               "their DMs; you just type in the post."),
            cards.divider(),
            cards.section("Support Agent", "When an instant answer fits, the Support Agent chats with the member "
                                           "first (🎧 in the title). Don't reply yet: it hands over by itself when "
                                           "they ask for a person or nothing fits. Typing in the post or **Take "
                                           "over** takes it straight away."),
            cards.section("Replying", "Type in the post and it reaches the member. Start with `//` for a staff-only "
                                      "note."),
            cards.section("Quick commands", "`?close` · `?ask` · `?claim` · `?urgent` · `?takeover` · `?r hello` · "
                                            "`?history` · `?delete` (spam) · `?tickets` · `?t 142` · `?help` for all"),
            cards.section("Teach it", "Fixed something new? Press **Save fix as answer**: a few trigger words + the "
                                      "fix in rough notes, the bot writes the sentence. Or `/answer add` anywhere."),
            cards.section("Title dots", "🎧 Support Agent on it · 🟢 new · 🔵 claimed · 🟡 waiting on member · "
                                        "⏳ asked to close · 🔴 urgent"),
            cards.section("Buttons", "**Claim** · **Ask to close** (the member confirms) · **Close now** · "
                                     "**Send to dev team** · **Take over** · **Save fix as answer** · priority"),
            cards.section("Timers", "No reply in 30 min: Support Team gets one ping. Member silent 48 h: auto-close."),
            cards.divider(),
            cards.section("Finding tickets", "Closing stores the chat and removes the post. **Browse tickets** "
                                             "lists every ticket (open, closed, yours, search): pick one to jump to "
                                             "it or read it."),
            cards.row(ticketlist.BrowseButton()),
            accent=ui.NEUTRAL, banner_name="inbox"))
        await cards.forum_card("inbox_guide", inbox, "How the inbox works", view, banners=("inbox",))

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
        hcfg = cfg()["support"].get("helper", {})
        found = faq.match(f"{text} {extra}")
        issue = known_issue(f"{text} {extra}") if topic_key in ("help", "bug", "purchase", "other") else None
        helping = bool((found or issue) and topic_key in hcfg.get("topics", []))
        first_id = "issue" if issue else (found["id"] if found else None)
        first_text = issue_reply(issue) if issue else (found["answer"] if found else "")
        first_label = (f'known issue "{issue}"' if issue
                       else (f"instant answer {faq.label(found)}" if found else None))
        now = db.now()
        tid = db.run("INSERT INTO tickets(user_id, topic, status, created, last_activity, handed) "
                     "VALUES(?, ?, ?, ?, ?, ?)", user.id, topic_key, "bot" if helping else "open", now, now,
                     None if helping else now)
        label = topic_label(topic_key)
        pos, eta = queue_position(tid), eta_minutes()
        if helping:
            db.kv_set(f"tbot:{tid}", {"shown": [first_id], "said": f"{text} {extra}", "tries": 1})
            first = (user.display_name or "there").split()[0]
            intro = "Thanks for telling us." if issue else "I think I know this one:"
            buttons = ("known", "not", "human") if issue else ("fixed", "not", "human")
            parts = [cards.title(f"Ticket {tid:04d} · {label}", f"Hiya {first}! I'm the Support Agent. {intro}"),
                     cards.divider(),
                     cards.text(first_text[:1800]),
                     cards.row(*[AssistButton(tid, a) for a in buttons]),
                     cards.divider(),
                     cards.small("Not it? Reply here in your own words, or press Talk to a person. "
                                 "The team can see this chat.")]
        else:
            parts = [cards.title(f"Ticket {tid:04d} · {label}", "Thanks, the team has your message."),
                     cards.divider(),
                     cards.text(ui.kv(("Your place", f"#{pos} in line"), ("Usual first reply", f"~{eta} min"))),
                     cards.section("What now?", "Reply right here to add details or screenshots. Everything you "
                                                "send in this DM goes to the team, and their answers arrive here "
                                                "too."),
                     cards.divider(),
                     cards.row(TicketButton("uclose", tid))]
        if AGENT_ICON:   # the headset next to the title, so members know it's support talking
            parts[0] = discord.ui.Section(parts[0], accessory=discord.ui.Thumbnail(AGENT_ICON))
        hello = cards.CardView(cards.container(*parts, accent=ui.BRAND, banner_name="support"))
        try:
            await user.send(view=hello, files=cards.files_for("support"))
        except discord.HTTPException as e:
            drop_ticket(tid)
            return await interaction.followup.send(
                "I can't DM you. Turn on **Direct Messages** for this server (server name > Privacy Settings), "
                "then try again." if isinstance(e, discord.Forbidden) else
                "Something went wrong opening your ticket. Please try again in a minute.", ephemeral=True)
        made = None
        try:   # no inbox post = no ticket: a half-made one would block the member for good
            helper, flag = None, None
            if helping:
                helper = HELPER_ON + f"\n-# Sent {first_label}"
            elif found:
                flag = (f"-# ⚡ Instant answer {faq.label(found)} fits this, but {label.lower()} tickets always go "
                        "to a person.")
            else:
                flag = "-# No instant answer fits this one. Fixed it? Press **Save fix** so the next member gets it."
            if topic_key == "appeal" and username:
                hit = await roblox_user_by_name(username)
                if hit:
                    db.kv_set(f"ticket_roblox:{tid}", hit["id"])
                    ban = await opencloud.get_ban(hit["id"])
                    rec = (f"{ban.get('displayReason') or 'no public reason'} · private: {ban.get('privateReason') or '-'}"
                           if ban else ("not banned" if opencloud.available() else "Open Cloud key not set up, check "
                                                                                   "by hand"))
                    extra = (extra + "\n" if extra else "") + f"**Ban on record:** {rec}"
            profile = await gather_profile(self.guild, user, username or None)
            db.kv_set(f"tcard:{tid}", {"problem": text, "extra": extra, "profile": profile, "helper": helper,
                                       "flag": flag, "matched": bool(found or issue), "first": first_label})
            t = ticket(tid)
            tags = [tg for tg in inbox.available_tags if tg.name in tag_names(t)]
            made = await inbox.create_thread(name=title_of(t, user.name), view=staff_card(t), applied_tags=tags,
                                             allowed_mentions=discord.AllowedMentions.none())
            db.run("UPDATE tickets SET thread_id=? WHERE id=?", made.thread.id, tid)
        except Exception:
            log.exception("ticket %s open", tid)
            drop_ticket(tid)
            try:
                if made:
                    await made.thread.delete()
            except discord.HTTPException:
                pass
            try:
                await user.send(f"Sorry, ticket {tid:04d} didn't reach the team. Please open it again in a minute.")
            except discord.HTTPException:
                pass
            return await interaction.followup.send("Something went wrong opening your ticket. Please try again in a "
                                                   "minute.", ephemeral=True)
        if helping:
            try:
                await self.helper_note(made.thread, first_text, f"Sent {first_label}")
            except discord.HTTPException as e:
                log.warning("ticket %s helper note: %s", tid, e)
        await interaction.followup.send(f"Ticket {tid:04d} is open. Check your DMs.", ephemeral=True)
        await log_staff(self.guild, f"Ticket {tid:04d} opened by {user.mention} · {label}"
                                    + (" · Support Agent on it" if helping else ""), kind="Tickets", event="ticket_opened")
        try:
            from . import radar
            await radar.note_ticket(self.guild, tid, topic_key, user.id, f"{text} {extra}")
        except Exception:
            log.exception("incident radar")

    # ---------- support helper (rule based: instant answers only, no AI) ----------
    async def helper_note(self, thread, text: str, what: str = "Support helper"):
        """Show staff what the helper told the member."""
        e = embed(description=text[:4000], colour=discord.Colour(ui.SLATE))
        e.set_author(name=f"{AGENT} · {what}", icon_url=AGENT_ICON)
        await thread.send(embed=e, allowed_mentions=discord.AllowedMentions.none())

    async def helper_say(self, t, text: str, view=None, what: str = "Support helper"):
        e = embed(description=text, colour=discord.Colour(BLUE))
        e.set_author(name=AGENT, icon_url=AGENT_ICON)
        await self.send_to_user(t, e, view=view)
        thread = await self.fetch_thread(t["thread_id"])
        if thread:
            await self.helper_note(thread, text, what)

    async def member_said(self, t, words: str):
        """Mirror a button press into the post, so the chat reads naturally for staff."""
        thread = await self.fetch_thread(t["thread_id"])
        if thread:
            user = self.bot.get_user(t["user_id"])
            e = embed(description=f"*pressed* **{words}**", colour=discord.Colour(AMBER))
            e.set_author(name=f"{user.display_name if user else 'Member'} (member)",
                         icon_url=user.display_avatar.url if user else None)
            await thread.send(embed=e)

    async def helper_fixed(self, t):
        st = helper_state(t["id"])
        last = st["shown"][-1] if st["shown"] else None
        entry = next((e for e in faq.entries() if e["id"] == last), None)
        faq.counted(entry)
        db.kv_set("triage_solved", db.kv_get("triage_solved", 0) + 1)
        set_card(t["id"], solved_by_agent=True)
        db.run("UPDATE tickets SET status='closing', last_activity=? WHERE id=?", db.now(), t["id"])
        db.run("DELETE FROM kv WHERE key=?", f"tbot:{t['id']}")
        what = "Known issue confirmed" if last == "issue" else "Instant answer fixed it"
        set_card(t["id"], helper=f"✅ **{what}** ({faq.label(entry) if entry else 'known issue'}). "
                                 "Asked the member to close.")
        v = discord.ui.View(timeout=None)
        v.add_item(ConfirmClose(t["id"], "yes"))
        v.add_item(ConfirmClose(t["id"], "no"))
        text = ("Thanks for letting us know! The fix is on its way. Are you happy for this ticket to be closed?"
                if last == "issue" else "No worries, glad that sorted it! Are you happy for this ticket to be closed?")
        await self.helper_say(ticket(t["id"]), text, view=v, what="Asked to close")
        await self.refresh(t["id"])

    async def helper_next(self, t, said: str):
        """Try the next instant answer that fits everything the member said, else hand over."""
        st = helper_state(t["id"])
        st["said"] = f"{st.get('said', '')} {said}".strip()[-3000:]
        nxt = faq.match(st["said"], skip=set(st["shown"]))
        if nxt and st.get("tries", 1) < cfg()["support"].get("helper", {}).get("max_tries", 3):
            st["shown"].append(nxt["id"])
            st["tries"] = st.get("tries", 1) + 1
            db.kv_set(f"tbot:{t['id']}", st)
            db.run("UPDATE tickets SET last_activity=? WHERE id=?", db.now(), t["id"])
            set_card(t["id"], helper=HELPER_ON + f"\n-# Tried {len(st['shown'])} instant answers so far")
            await self.helper_say(t, f"Okay, let's try something else:\n\n{nxt['answer']}",
                                  view=helper_view(t["id"]), what=f"Sent instant answer {faq.label(nxt)}")
            return await self.refresh(t["id"])
        db.kv_set(f"tbot:{t['id']}", st)
        await self.handoff(t, "no instant answer fits their reply" if said else "the instant answer didn't fix it")

    async def handoff(self, t, reason: str, by: discord.Member | None = None):
        """Support helper -> a person. The 30-minute nudge starts now."""
        t = ticket(t["id"])
        if t["status"] != "bot":
            return
        now = db.now()
        db.run("UPDATE tickets SET status='open', handed=?, nudged=0, last_activity=? WHERE id=?", now, now, t["id"])
        set_card(t["id"], tried=[x for x in helper_state(t["id"]).get("shown", []) if x != "issue"])
        db.run("DELETE FROM kv WHERE key=?", f"tbot:{t['id']}")
        if by:
            set_card(t["id"], helper=f"👤 {by.mention} took over from the Support Agent.")
            await self.send_to_user(t, embed(description=f"**{by.display_name}** from the team has joined. "
                                                         "Carry on here.", colour=discord.Colour(GREEN)))
        else:
            set_card(t["id"], helper=f"👤 **Needs a person:** {reason}. You're up!")
            await self.helper_say(t, "Okay, I'll pass you over to a person. They'll reply right here, usually "
                                     f"within ~{eta_minutes()} min. Add anything that helps, like screenshots or "
                                     "where in the game it happens.", what="Handed over")
        await self.refresh(t["id"])
        why = f"taken over by {by.display_name}" if by else reason
        await log_staff(self.guild, f"Ticket {t['id']:04d}: Support Agent handed over ({why})", kind="Tickets",
                        event="ticket_handover")

    async def helper_reply(self, t, text: str):
        """The member typed while the helper is on: wants a person? says thanks? or a new detail to match."""
        low = text.lower()
        words = set(faq.WORD.findall(low))
        if words & HUMAN_WORDS or "talk to" in low or "real person" in low:
            return await self.handoff(t, "the member asked for a person")
        if len(text) < 80 and (words & THANKS_WORDS or "works now" in low) and not words & NOT_WORDS:
            return await self.helper_fixed(t)
        await self.helper_next(t, text)

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
            if starter.flags.components_v2:
                return await starter.edit(view=staff_card(t), allowed_mentions=discord.AllowedMentions.none())
            rest = starter.embeds
            if rest and rest[0].footer and (rest[0].footer.text or "").startswith(CARD_FOOTER[:20]):
                rest = rest[1:]   # drop the old control card, keep profile + problem
            await starter.edit(embeds=[control_card(t)] + rest[:9], view=staff_view(t))
        except discord.HTTPException as e:
            log.warning("control card: %s", e)

    async def send_to_user(self, t, e: discord.Embed, files=None, view=None) -> discord.Message | None:
        if not e.author:   # every support DM carries the agent header
            e.set_author(name=AGENT, icon_url=AGENT_ICON)
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

    async def close(self, t, by: discord.abc.User | None, reason: str | None = None, save: bool = True):
        """save=False (?delete): spam/troll tickets. The post goes without storing the chat or asking for a rating."""
        if ticket(t["id"])["status"] == "closed":
            return
        db.run("UPDATE tickets SET status='closed', closed=? WHERE id=?", db.now(), t["id"])
        t = ticket(t["id"])
        thread = await self.fetch_thread(t["thread_id"])
        who = reason or (f"closed by {by.display_name}" if by else "closed")
        saved = 0
        if thread:
            try:
                if save:
                    saved = await transcripts.archive(t["id"], thread, self.bot.user.id)
                await thread.delete()
            except Exception as e:  # noqa: BLE001 - keep the post if storing failed
                log.warning("ticket %s archive: %s", t["id"], e)
                await thread.send(f"Closed ({who}). Couldn't store the chat, so this post stays.")
                await thread.edit(archived=True, locked=True)
        transcripts.prune()
        db.run("DELETE FROM kv WHERE key IN (?, ?)", f"tbot:{t['id']}", f"ticket_dev:{t['id']}")
        if not save:
            db.kv_set(f"tdeleted:{t['id']}", True)
            await self.send_to_user(t, embed(f"Ticket {t['id']:04d} closed", colour=discord.Colour(GREY)))
            return await log_staff(self.guild, f"🗑️ Ticket {t['id']:04d} · {topic_label(t['topic'])} · "
                                               f"<@{t['user_id']}> deleted by {by.display_name if by else 'staff'} "
                                               "(chat not stored)", kind="Tickets")
        await self.send_to_user(t, embed(f"Ticket {t['id']:04d} closed",
                                         "Thanks for reaching out. How did we do?", colour=discord.Colour(GREY)),
                                view=rating_view(t["id"]))
        v = discord.ui.View(timeout=None)
        v.add_item(transcripts.ViewButton(t["id"]))
        d = card_data(t["id"])
        if t["first_reply"] and d and not d.get("matched") and db.kv_get(f"tlast:{t['id']}"):
            v.add_item(SaveFixButton(t["id"]))
        await log_staff(self.guild, f"✅ Ticket {t['id']:04d} · {topic_label(t['topic'])} · <@{t['user_id']}> "
                                    f"{who} · {saved} messages", kind="Tickets", view=v)

    ticket_group = app_commands.Group(name="ticket", description="Tickets (staff)",
                                      default_permissions=discord.Permissions(moderate_members=True))

    @ticket_group.command(name="list", description="Browse every ticket: open, closed, yours, search")
    @staff_only()
    async def ticket_list(self, interaction: discord.Interaction):
        await ticketlist.send(interaction)

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
        if t["status"] == "bot":
            db.run("UPDATE tickets SET last_activity=? WHERE id=?", db.now(), t["id"])
            if msg.content:
                await self.helper_reply(t, msg.content)
            return
        db.run("UPDATE tickets SET status='open', last_activity=?, warned=0, handed=COALESCE(handed, ?) WHERE id=?",
               db.now(), db.now(), t["id"])
        if t["status"] != "open":
            await self.refresh(t["id"])

    async def _from_staff(self, msg: discord.Message):
        t = ticket_for_thread(msg.channel.id)
        if not t or not is_staff(msg.author) or msg.content.startswith(NOTE_PREFIX) or is_command(msg.content):
            return
        if t["status"] == "closed":
            return await msg.reply("This ticket is closed. The member won't see this.", mention_author=False)
        if t["status"] == "bot":
            await self.handoff(t, "taken over", by=msg.author)
        ok = await self.relay_staff(t, msg.author, msg.content, await self._files(msg), src=msg)
        await msg.add_reaction("✅" if ok else "❌")
        if not ok:
            await msg.reply("Couldn't DM the member (DMs closed or they left).", mention_author=False)

    async def relay_staff(self, t, author: discord.Member, content: str, files, src=None) -> bool:
        e = embed(description=content[:4000] or "*(attachment)*", colour=discord.Colour(GREEN))
        e.set_author(name=f"{author.display_name} · {AGENT}", icon_url=author.display_avatar.url)
        sent = await self.send_to_user(t, e, files)
        if not sent:
            return False
        if src is not None:
            link_copy(src, sent, t["id"], "staff")
        if content:
            db.kv_set(f"tlast:{t['id']}", content[:1200])   # pre-fills "Save fix as answer"
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
        if content is None or not payload.data.get("edited_timestamp") or payload.data.get("author", {}).get("bot"):
            return   # link previews loading also fire this; only real edits have edited_timestamp
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
    @guarded
    async def timers(self):
        if not self.guild:
            return
        now = db.now()
        team = role_named(self.guild, "Support Team")
        for t in db.q("SELECT * FROM tickets WHERE status NOT IN ('closed', 'bot', 'closing') AND first_reply IS NULL "
                      "AND nudged=0 AND COALESCE(handed, created)<?", now - NUDGE_AFTER):
            thread = await self.fetch_thread(t["thread_id"])
            try:
                if thread:
                    await thread.send(f"⏰ No reply for 30 minutes. {team.mention if team else 'Support team'}, "
                                      "can someone take this?", allowed_mentions=discord.AllowedMentions(roles=True))
            except discord.HTTPException as e:   # one locked post must not hold up the rest
                log.warning("ticket %s nudge: %s", t["id"], e)
            db.run("UPDATE tickets SET nudged=1 WHERE id=?", t["id"])
        for t in db.q("SELECT * FROM tickets WHERE status='waiting' AND warned=0 AND last_activity<?", now - WARN_AFTER):
            await self.send_to_user(t, embed(f"Still need help with ticket {t['id']:04d}?",
                                             "Reply here if you do. Otherwise it closes by itself in 24 hours.",
                                             colour=discord.Colour(AMBER)))
            db.run("UPDATE tickets SET warned=1 WHERE id=?", t["id"])
        closes = [(t, "auto-closed after 48 h without a reply") for t in db.q(
                      "SELECT * FROM tickets WHERE status='waiting' AND warned=1 AND last_activity<?", now - CLOSE_AFTER)]
        closes += [(t, "closed, the member stopped answering the Support Agent") for t in db.q(
                      "SELECT * FROM tickets WHERE status='bot' AND last_activity<?", now - WARN_AFTER)]
        closes += [(t, "closed, the member didn't answer within 24 h") for t in db.q(
                      "SELECT * FROM tickets WHERE status='closing' AND last_activity<?", now - WARN_AFTER)]
        for t, why in closes:
            try:
                await self.close(t, by=None, reason=why)
            except Exception:
                log.exception("ticket %s auto-close", t["id"])

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
        await interaction.response.defer(thinking=True)   # answer first: a timed-out reply would get retried = sent twice
        ok = await self.relay_staff(t, interaction.user, text, [])
        await interaction.followup.send(f"{'Sent' if ok else 'Could not send'} saved reply `{name}`:\n{text}")

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
            lines.append(f"**{i}.** {names.who(r['claimed_by'])} · {r['n']} closed · {rating} · "
                         f"{round((r['f'] or 0) / 60)} min first reply")
        e.add_field(name="Leaderboard", value="\n".join(lines) or "No closed tickets yet.", inline=False)
        return e

    @tasks.loop(hours=6)
    @guarded
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
