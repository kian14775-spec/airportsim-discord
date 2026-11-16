"""Staff Duty: the work staff already do becomes points on one board, with a calm weekly rhythm.

Board (#stats, pinned): every team's week, who is on duty and who is on a break, plus the buttons
My week / Go on duty / Go off duty / Take a break / How this works.
Monday 00:00 UTC: last week closes. Everyone gets a short DM, #leads gets the report, and cards for
2 quiet weeks in a row (check-in) or 4 strong weeks as Trial (promotion). Leads press the buttons:
the bot never demotes or promotes by itself, and every press is logged in #actions.
Thursday: one friendly reminder, only to people far from their goal.
Points come from tickets.py (solved, fast reply), strikes.py (warns), the audit log here (actions with
a reason), public chat (active days) and bugs/events/creators. Rules + targets: config/bot.yaml `duty:`.
Staff guide: a pinned card in #staff-chat with a topic menu; joining a team sends its pages by DM.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import uuid

import discord
from discord.ext import commands, tasks

from .. import cards, db, names, points, ui
from ..core import cfg, channel, guarded, is_staff, log, log_staff, role, staff_roles

A = discord.AuditLogAction
WORDS = {"green": "goal", "amber": "close", "red": "low", "leave": "break"}
MOD_ACTIONS = (A.ban, A.kick, A.unban, A.member_update, A.message_delete, A.message_bulk_delete)


# ---------------------------------------------------------------- guide
def guide_pages() -> dict[str, dict]:
    return cfg().get("guide", {})


def page_text(key: str) -> tuple[str, str]:
    pg = guide_pages().get(key) or guide_pages().get("start") or {"title": "Staff guide", "body": ""}
    targets = ", ".join(f"{d['name']} {d.get('target', 10)}" for d in points.departments().values())
    return pg["title"], pg["body"].replace("{targets}", targets).strip()


def page_view(key: str) -> cards.CardView:
    t, body = page_text(key)
    return cards.CardView(cards.container(cards.title(t), cards.text(body), cards.divider(),
                                          cards.row(GuidePick(persistent=False)), accent=ui.SLATE), timeout=900)


class GuidePick(discord.ui.Select):
    def __init__(self, persistent: bool = True):
        super().__init__(placeholder="Pick a topic", custom_id="asbot:guide:pick" if persistent else None,
                         options=[discord.SelectOption(label=p["title"][:100], value=k)
                                  for k, p in list(guide_pages().items())[:25]] or
                         [discord.SelectOption(label="Staff guide", value="start")])

    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        if interaction.message and interaction.message.flags.ephemeral:   # already a private page: swap it
            return await interaction.response.edit_message(view=page_view(key))
        await interaction.response.send_message(view=page_view(key), ephemeral=True)


class GuideCard(cards.CardView):
    """#staff-chat, pinned: the whole staff guide behind one menu."""

    def __init__(self):
        pages = "\n".join(f"**{p['title']}**" for p in guide_pages().values())
        super().__init__(cards.container(
            cards.title("Staff guide", "New here, or forgot how something works? Pick a topic. "
                                       "The page only shows to you."),
            cards.divider(),
            cards.text(pages or "No pages yet."),
            cards.row(GuidePick()),
            accent=ui.SLATE))


# ---------------------------------------------------------------- board
def _next_monday(week: str) -> int:
    return int(points.week_start(week) + 7 * 86400)


def _name(m: discord.Member, width: int = 14) -> str:
    n = "".join(ch for ch in m.display_name if ch.isprintable() and ord(ch) < 0x2000).strip() or m.name[:width]
    return (n[:width - 1] + ".") if len(n) > width else n.ljust(width)


def board_rows(guild: discord.Guild, dept: str, week: str) -> str:
    tot, tgt = points.totals(week), points.target(dept)
    ms = sorted(points.members_of(guild, dept), key=lambda m: (-tot.get(m.id, 0), m.display_name.lower()))
    rows, right = [], 11 + len(f"{0:>2}/{tgt}")        # the bar + the points column, so every row lines up
    for m in ms[:25]:
        pts = tot.get(m.id, 0)
        if points.on_leave(m.id):
            rows.append(f"{_name(m)} {'on a break':>{right}}")
        else:
            rows.append(f"{_name(m)} {points.bar(pts, tgt)} {pts:>2}/{tgt}")
    if len(ms) > 25:
        rows.append(f"... and {len(ms) - 25} more")
    return "```\n" + "\n".join(rows) + "\n```" if rows else "-# Nobody in this team yet."


class MyWeek(discord.ui.Button):
    def __init__(self):
        super().__init__(label="My week", style=discord.ButtonStyle.primary, custom_id="asbot:duty:week")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(view=my_week_view(interaction.user), ephemeral=True)


class GoOn(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Go on duty", style=discord.ButtonStyle.success, custom_id="asbot:duty:on")

    async def callback(self, interaction: discord.Interaction):
        if not (points.dept_of(interaction.user) or is_staff(interaction.user)):
            return await interaction.response.send_message("This is for the staff team.", ephemeral=True)
        until = points.go_on_duty(interaction.user.id)
        await interaction.response.send_message(
            f"You're on duty until <t:{int(until)}:t>. New tickets will ping you first. "
            "Press **Go off duty** when you stop, or it switches off by itself.", ephemeral=True)


class GoOff(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Go off duty", style=discord.ButtonStyle.secondary, custom_id="asbot:duty:off")

    async def callback(self, interaction: discord.Interaction):
        was = points.go_off_duty(interaction.user.id)
        await interaction.response.send_message("You're off duty. Thanks for helping!" if was
                                                else "You weren't on duty.", ephemeral=True)


class BreakModal(discord.ui.Modal, title="Take a break"):
    days = discord.ui.TextInput(label="How many days? (1-60)", max_length=2, placeholder="14")
    reason = discord.ui.TextInput(label="Why? (only leads see this)", max_length=120, placeholder="Exams")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            n = int(self.days.value.strip())
        except ValueError:
            n = 0
        if not 1 <= n <= 60:
            return await interaction.response.send_message("Pick between 1 and 60 days.", ephemeral=True)
        until = points.set_leave(interaction.user.id, n, self.reason.value.strip() or "break")
        points.go_off_duty(interaction.user.id)
        await interaction.response.send_message(
            f"Enjoy your break! It ends by itself <t:{int(until)}:R>. Your weeks aren't counted until then. "
            "Back early? **My week** > **End my break**.", ephemeral=True)
        await log_staff(interaction.guild, f"{interaction.user.mention} is on a break for {n} days: "
                                           f"{self.reason.value.strip()}", kind="Members", event="duty_break")


class TakeBreak(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Take a break", style=discord.ButtonStyle.secondary, custom_id="asbot:duty:break")

    async def callback(self, interaction: discord.Interaction):
        if not points.dept_of(interaction.user):
            return await interaction.response.send_message("Breaks are for team members.", ephemeral=True)
        await interaction.response.send_modal(BreakModal())


class HowItWorks(discord.ui.Button):
    def __init__(self):
        super().__init__(label="How this works", style=discord.ButtonStyle.secondary, custom_id="asbot:duty:guide")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(view=page_view("duty"), ephemeral=True)


class EndBreak(discord.ui.Button):
    def __init__(self):
        super().__init__(label="End my break", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        points.end_leave(interaction.user.id)
        await interaction.response.edit_message(view=my_week_view(interaction.user))
        await log_staff(interaction.guild, f"{interaction.user.mention} ended their break", kind="Members",
                        event="duty_break")


def board_buttons() -> discord.ui.ActionRow:
    return cards.row(MyWeek(), GoOn(), GoOff(), TakeBreak(), HowItWorks())


def board_view(guild: discord.Guild) -> cards.CardView:
    wk = points.week_of()
    parts = [cards.title("Staff board", f"Week {int(wk[-2:])} · new week <t:{_next_monday(wk)}:R> · "
                                        f"updated <t:{int(db.now())}:R>")]
    if db.kv_get("duty:start_week") == wk:
        parts.append(cards.small("Warm-up week: this one shows on the board but isn't judged."))
    for key, d in points.departments().items():
        parts += [cards.divider(), cards.section(f"{d['name']} · goal {points.target(key)}",
                                                 board_rows(guild, key, wk))]
    on = [f"<@{uid}>" for uid in points.on_duty_ids()]
    parts += [cards.divider(),
              cards.text("**On duty now:** " + (", ".join(on) if on else "nobody yet. Got a moment? Press **Go on duty**.")),
              board_buttons()]
    return cards.CardView(cards.container(*parts, accent=ui.SLATE))


def my_week_view(user: discord.abc.User) -> cards.CardView:
    wk, dept = points.week_of(), points.dept_of(user)
    if not dept:
        return cards.CardView(cards.container(cards.title("My week"), cards.text(
            "You're not in a team that has a weekly goal, so there's nothing to count. "
            "Leads and the board see everyone's week in #stats."), accent=ui.SLATE), timeout=600)
    tgt = points.target(dept)
    pts = points.totals(wk).get(user.id, 0)
    lines = points.week_lines(user.id, wk)
    left = tgt - pts
    head = f"**{pts} / {tgt}** · {points.departments()[dept]['name']}\n`{points.bar(pts, tgt, 20)}`\n" + \
           ("Goal reached. Nice work!" if left <= 0 else f"{left} to go before <t:{_next_monday(wk)}:f>.")
    past = db.q("SELECT week, pts, target, status FROM duty_weeks WHERE user_id=? ORDER BY week DESC LIMIT 4", user.id)
    hist = " · ".join(f"W{int(r['week'][-2:])} {r['pts']}/{r['target']} {WORDS.get(r['status'], r['status'])}"
                      for r in past)
    leave, items = points.on_leave(user.id), []
    if leave:
        items.append(EndBreak())
    parts = [cards.title("My week", head), cards.divider(),
             cards.section("What counted", "\n".join(lines) if lines else
                           "Nothing yet this week. Solved tickets, warnings with a reason, bug checks, videos and "
                           "events all count by themselves.")]
    if hist:
        parts += [cards.divider(), cards.small("Last weeks: " + hist)]
    if leave:
        parts += [cards.divider(), cards.text(f"**On a break** until <t:{int(leave['until'])}:f>.")]
    if user.id in points.on_duty_ids():
        parts.append(cards.small("You're on duty right now."))
    if items:
        parts.append(cards.row(*items))
    return cards.CardView(cards.container(*parts, accent=ui.SLATE), timeout=600)


# ---------------------------------------------------------------- lead cards
def lead_result(title: str, body: str, done: str) -> cards.CardView:
    return cards.CardView(cards.container(cards.title(title), cards.text(body), cards.divider(),
                                          cards.small(done), accent=ui.SLATE))


async def _dm(user: discord.abc.User, view: cards.CardView) -> bool:
    try:
        await user.send(view=view)
        return True
    except discord.HTTPException:
        return False


def dm_card(title: str, body: str, tone: int = ui.SLATE) -> cards.CardView:
    return cards.CardView(cards.container(cards.title(title), cards.text(body), accent=tone))


async def _swap_role(member: discord.Member, remove: list[discord.Role], add: list[discord.Role], why: str) -> str | None:
    try:
        if remove:
            await member.remove_roles(*remove, reason=why)
        if add:
            await member.add_roles(*add, reason=why)
    except discord.HTTPException as e:
        return f"Couldn't change their roles ({e.text or e.status}). My role must be above theirs."
    return None


class ReviewButton(discord.ui.DynamicItem[discord.ui.Button], template=r"asbot:dr:(?P<act>[a-z]+):(?P<uid>\d+)"):
    """Check-in card for 2+ quiet weeks: Give a break / Final warning / Demote / Keep."""
    LABELS = {"brk": ("Give a 14-day break", discord.ButtonStyle.secondary),
              "warn": ("Final warning", discord.ButtonStyle.secondary),
              "demote": ("Demote", discord.ButtonStyle.danger),
              "keep": ("Keep, all good", discord.ButtonStyle.success)}

    def __init__(self, act: str, uid: int):
        label, style = self.LABELS[act]
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=f"asbot:dr:{act}:{uid}"))
        self.act, self.uid = act, uid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["act"], int(match["uid"]))

    async def callback(self, interaction: discord.Interaction):
        if not points.is_lead(interaction.user):
            return await interaction.response.send_message("Only leads can decide this.", ephemeral=True)
        g, lead = interaction.guild, interaction.user
        m = g.get_member(self.uid)
        title = f"Check-in: {m.display_name if m else names.who(self.uid, link=False)}"
        if m is None:
            return await interaction.response.edit_message(view=lead_result(
                title, "They've left the server.", f"Closed by {lead.display_name}"))
        what, problem = "", None
        if self.act == "brk":
            points.set_leave(m.id, 14, f"break given by {lead.display_name}")
            await _dm(m, dm_card("A break from your lead", f"{lead.display_name} gave you a 14-day break from your "
                                 "weekly goal. Take care of yourself. It ends by itself, and you can end it early "
                                 "with **My week** in #stats."))
            what = "gave a 14-day break to"
        elif self.act == "warn":
            ok = await _dm(m, dm_card("A check-in about your staff role",
                                      f"Hi {m.display_name}, your last weeks were quiet on the Staff board. If you're busy, "
                                      "that's okay: press **Take a break** in #stats so it doesn't count. If nothing "
                                      "changes in the next weeks, the leads may give your spot to someone new. "
                                      "Questions? Ask in #staff-chat.", ui.WAIT))
            what = "sent a final warning to" + ("" if ok else " (DMs closed, tell them yourself)")
        elif self.act == "demote":
            d = points.dept_of(m)
            dc = points.departments().get(d or "", {})
            trial = role(g, dc["trial"]) if dc.get("trial") else None
            full = role(g, dc.get("member", "")) if dc.get("member") else None
            if trial and full and full in m.roles:
                problem = await _swap_role(m, [full], [trial], f"Staff Duty: demoted by {lead}")
                what = f"moved to {trial.name}:"
            else:
                problem = await _swap_role(m, [r for r in points.dept_roles(g, d) if r in m.roles] if d else [], [],
                                           f"Staff Duty: removed by {lead}")
                what = "removed from the team:"
        else:
            what = "kept"
        if problem:
            return await interaction.response.send_message(problem, ephemeral=True)
        db.kv_set(f"duty:review_done:{self.uid}", points.week_of())
        await interaction.response.edit_message(view=lead_result(
            title, f"{m.mention} had quiet weeks on the Staff board.", f"{lead.display_name} {what} {m.display_name}"))
        await log_staff(g, f"Staff Duty: {lead.mention} {what} {m.mention}", kind="Moderation", event="duty_review")


class PromoButton(discord.ui.DynamicItem[discord.ui.Button], template=r"asbot:dp:(?P<act>yes|no):(?P<uid>\d+)"):
    def __init__(self, act: str, uid: int):
        label, style = ("Promote", discord.ButtonStyle.success) if act == "yes" else ("Not yet", discord.ButtonStyle.secondary)
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=f"asbot:dp:{act}:{uid}"))
        self.act, self.uid = act, uid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["act"], int(match["uid"]))

    async def callback(self, interaction: discord.Interaction):
        if not points.is_lead(interaction.user):
            return await interaction.response.send_message("Only leads can decide this.", ephemeral=True)
        g, lead, m = interaction.guild, interaction.user, interaction.guild.get_member(self.uid)
        title = f"Ready for promotion: {m.display_name if m else self.uid}"
        if m is None:
            return await interaction.response.edit_message(view=lead_result(title, "They've left the server.",
                                                                            f"Closed by {lead.display_name}"))
        if self.act == "no":
            await interaction.response.edit_message(view=lead_result(title, f"{m.mention} had strong weeks as Trial.",
                                                                     f"{lead.display_name}: not yet"))
            return await log_staff(g, f"Staff Duty: {lead.mention} kept {m.mention} as Trial for now",
                                   kind="Moderation", event="duty_review")
        d = points.dept_of(m)
        dc = points.departments().get(d or "", {})
        trial = role(g, dc["trial"]) if dc.get("trial") else None
        full = role(g, dc.get("member", "")) if dc.get("member") else None
        if not (trial and full):
            return await interaction.response.send_message("This team has no Trial step set in the config.",
                                                           ephemeral=True)
        problem = await _swap_role(m, [trial] if trial in m.roles else [], [full], f"Staff Duty: promoted by {lead}")
        if problem:
            return await interaction.response.send_message(problem, ephemeral=True)
        await interaction.response.edit_message(view=lead_result(title, f"{m.mention} had strong weeks as Trial.",
                                                                 f"{lead.display_name} promoted them to {full.name}"))
        await _dm(m, dm_card("You've been promoted!", f"Your lead {lead.display_name} promoted you to **{full.name}**. "
                             "Thanks for all the work you put in.", ui.GOOD))
        await log_staff(g, f"Staff Duty: {lead.mention} promoted {m.mention} to {full.mention}", kind="Moderation",
                        event="duty_review")


def review_card(m: discord.Member, weeks: int, week: str) -> cards.CardView:
    hist = db.q("SELECT week, pts, target FROM duty_weeks WHERE user_id=? ORDER BY week DESC LIMIT 4", m.id)
    lines = " · ".join(f"W{int(r['week'][-2:])} {r['pts']}/{r['target']}" for r in hist)
    return cards.CardView(cards.container(
        cards.title(f"Check-in: {m.display_name}",
                    f"{m.mention} was under half their goal for {weeks} weeks in a row."),
        cards.small(lines),
        cards.text("Maybe they're busy, maybe they lost interest. A short DM usually tells you which. "
                   "Then pick one:"),
        cards.row(*(ReviewButton(a, m.id) for a in ("brk", "warn", "demote", "keep"))),
        accent=ui.WAIT))


def promo_card(m: discord.Member, weeks: int) -> cards.CardView:
    return cards.CardView(cards.container(
        cards.title(f"Ready for promotion: {m.display_name}",
                    f"{m.mention} hit their goal {weeks} weeks in a row as Trial."),
        cards.row(PromoButton("yes", m.id), PromoButton("no", m.id)), accent=ui.GOOD))


def report_view(guild: discord.Guild, week: str, results: list[dict]) -> cards.CardView:
    parts = [cards.title(f"Staff week {int(week[-2:])}", f"Week of <t:{int(points.week_start(week))}:D>. "
                                                          "Goal = target hit, close = half or more, low = under half.")]
    for key, d in points.departments().items():
        rows = [r for r in results if r["dept"] == key]
        if not rows:
            continue
        rows.sort(key=lambda r: -r["pts"])
        body = "\n".join(f"{_name(m) if (m := guild.get_member(r['user_id'])) else str(r['user_id'])[:14]:<14} "
                         f"{r['pts']:>3}/{r['target']:<3} {WORDS[r['status']]}" for r in rows[:30])
        hit = sum(r["status"] == "green" for r in rows)
        parts += [cards.divider(), cards.section(f"{d['name']} · {hit}/{len(rows)} hit the goal", f"```\n{body}\n```")]
    return cards.CardView(cards.container(*parts, accent=ui.SLATE))


# ---------------------------------------------------------------- 30-day staff report
async def staff_report(guild: discord.Guild, days: int = 30) -> cards.CardView:
    """Who did what in the last `days`: solved tickets, audit-log actions, warnings, messages (7 days)."""
    since = db.now() - days * 86400
    people: dict[int, discord.Member] = {m.id: m for _, m in points.team(guild)}
    for r in staff_roles(guild):
        people.update({m.id: m for m in r.members if not m.bot})
    tickets = {r["claimed_by"]: r["n"] for r in db.q(
        "SELECT claimed_by, COUNT(*) n FROM tickets WHERE status='closed' AND closed>=? AND claimed_by IS NOT NULL "
        "GROUP BY claimed_by", since)}
    try:
        warns = {r["by_id"]: r["n"] for r in db.q(
            "SELECT by_id, COUNT(*) n FROM strikes WHERE created>=? GROUP BY by_id", since)}
    except Exception:   # noqa: BLE001 - strikes table not made yet
        warns = {}
    try:
        msgs = {r["author_id"]: (r["n"], r["last"]) for r in db.q(
            "SELECT author_id, COUNT(*) n, MAX(created) last FROM msglog GROUP BY author_id")}
    except Exception:   # noqa: BLE001
        msgs = {}
    actions: dict[int, int] = {}
    after = dt.datetime.fromtimestamp(since, dt.timezone.utc)
    for act in MOD_ACTIONS:
        try:
            async for e in guild.audit_logs(action=act, after=after, limit=1000):
                if not e.user_id or e.user_id == guild.me.id:
                    continue
                if act == A.member_update and getattr(e.after, "timed_out_until", None) is None:
                    continue
                actions[e.user_id] = actions.get(e.user_id, 0) + 1
        except discord.HTTPException as ex:
            log.warning("staff report audit %s: %s", act, ex)
    rows, idle = [], []
    for uid, m in sorted(people.items(), key=lambda kv: kv[1].display_name.lower()):
        t, a, w = tickets.get(uid, 0), actions.get(uid, 0), warns.get(uid, 0)
        n7 = msgs.get(uid, (0, None))[0]
        if not (t or a or w or n7):
            idle.append(m)
        rows.append((t + a + w, f"{_name(m, 13)} {t:>4} {a:>5} {w:>4} {n7:>5}"))
    rows.sort(key=lambda r: -r[0])
    table = "```\n" + f"{'':13} {'tick':>4} {'acts':>5} {'warn':>4} {'msg7d':>5}\n" + \
            "\n".join(r for _, r in rows[:40]) + "\n```"
    parts = [cards.title(f"Staff report · last {days} days",
                         "tick = tickets solved · acts = bans, kicks, timeouts, deletes · warn = warnings · "
                         "msg7d = messages in the last 7 days"),
             cards.divider(), cards.text(table if rows else "No staff found.")]
    if idle:
        parts += [cards.divider(), cards.section(f"No activity found ({len(idle)})",
                                                 ", ".join(m.mention for m in idle[:60]))]
    parts.append(cards.small(f"Made <t:{int(db.now())}:f>. A quiet number can mean voice or in-game help: "
                             "ask before judging."))
    return cards.CardView(cards.container(*parts, accent=ui.SLATE), timeout=900)


class CreditModal(discord.ui.Modal, title="Give points"):
    pts = discord.ui.TextInput(label="Points (1-10)", max_length=2, placeholder="4")
    why = discord.ui.TextInput(label="What for? (they see this)", max_length=120, placeholder="Ran a test session")

    def __init__(self, member: discord.Member):
        super().__init__()
        self.member = member

    async def on_submit(self, interaction: discord.Interaction):
        try:
            n = int(self.pts.value.strip())
        except ValueError:
            n = 0
        if not 1 <= n <= 10:
            return await interaction.response.send_message("Pick 1 to 10 points.", ephemeral=True)
        got = points.award(self.member, "manual", f"manual:{uuid.uuid4().hex}", pts=n, note=self.why.value.strip(),
                           by_id=interaction.user.id)
        if not got:
            return await interaction.response.send_message(f"{self.member.mention} isn't in a team with a weekly goal.",
                                                           ephemeral=True)
        await interaction.response.send_message(f"Gave {n} points to {self.member.mention}.", ephemeral=True)
        await _dm(self.member, dm_card(f"+{n} points from {interaction.user.display_name}", self.why.value.strip(), ui.GOOD))
        await log_staff(interaction.guild, f"Staff Duty: {interaction.user.mention} gave {n} points to "
                                           f"{self.member.mention}: {self.why.value.strip()}", kind="Moderation",
                        event="duty_credit")


class CreditPick(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder="Give points to...")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CreditModal(self.values[0]))


class ReportButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Staff report (30 days)", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)   # reads the audit log: can take a while
        await interaction.followup.send(view=await staff_report(interaction.guild), ephemeral=True)


def staff_panel(member: discord.Member) -> cards.CardView:
    """/staff > Staff Duty: your week + lead tools."""
    board = channel(member.guild, "stats")
    parts = [cards.title("Staff Duty", "The board in " + (board.mention if board else "#stats") +
                         " shows everyone's week. Points count by themselves."),
             cards.divider()]
    parts.append(cards.row(MyWeek(), HowItWorks()))
    if points.is_lead(member):
        parts += [cards.divider(),
                  cards.section("Lead tools", "**Give points** for work the bot can't see. **Staff report** "
                                              "shows the last 30 days per person."),
                  cards.row(CreditPick()), cards.row(ReportButton())]
    return cards.CardView(cards.container(*parts, accent=ui.SLATE), timeout=900)


# ---------------------------------------------------------------- cog
class Duty(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._chat: dict[tuple[int, str], int] = {}
        self._timeouts: dict[int, tuple[str, float]] = {}   # target id -> (award ref, when) for the 1 h undo rule
        bot.add_view(cards.CardView(cards.container(board_buttons())))
        bot.add_view(GuideCard())
        bot.add_dynamic_items(ReviewButton, PromoButton)
        self.weekly.start()
        self.board.start()

    def cog_unload(self):
        self.weekly.cancel()
        self.board.cancel()

    @property
    def guild(self) -> discord.Guild | None:
        return self.bot.get_guild(self.bot.guild_id)

    # ---------- the board + the guide card ----------
    async def render(self):
        g = self.guild
        out = channel(g, "stats") if g else None
        if not out or not points.settings().get("enabled", True):
            return
        points.dirty = False
        msg = await cards.post_card("duty_board", out, board_view(g))
        if msg and not msg.pinned:
            try:
                await msg.pin()
                async for m in out.history(limit=5):
                    if m.type == discord.MessageType.pins_add and m.author.id == g.me.id:
                        await m.delete()
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_ready(self):
        g = self.guild
        if g is None:
            return
        if db.kv_get("duty:start_week") is None:     # first boot: this partial week is a warm-up
            wk = points.week_of()
            db.kv_set("duty:start_week", wk)
            db.kv_set("duty:closed", wk)
        chat = channel(g, "staff_chat")
        msg = await cards.post_card("staff_guide", chat, GuideCard()) if chat else None
        await cards.pin_quietly(msg)
        await self.render()

    @tasks.loop(minutes=2)
    @guarded
    async def board(self):
        last = db.kv_get("duty:rendered_at", 0)
        if points.dirty or db.now() - last > 3600:      # soon after a change, else hourly (the times move)
            await self.render()
            db.kv_set("duty:rendered_at", db.now())

    # ---------- weekly rhythm ----------
    @tasks.loop(minutes=10)
    @guarded
    async def weekly(self):
        g = self.guild
        if g is None or not points.settings().get("enabled", True) or db.kv_get("duty:start_week") is None:
            return                                       # on_ready sets the warm-up week first
        wk = points.week_of()
        last = points.prev_week(wk)
        closed = db.kv_get("duty:closed")
        if closed is not None and closed < last:
            db.kv_set("duty:closed", last)               # first: a crash mid-way must not repeat the DMs
            await self.close_week(g, last)
        now = dt.datetime.now(dt.timezone.utc)
        day = points.settings().get("nudge_day", "thu")[:3].lower()
        if now.strftime("%a").lower() == day and now.hour >= 15 and db.kv_get("duty:nudged") != wk \
                and db.kv_get("duty:start_week") != wk:
            db.kv_set("duty:nudged", wk)
            await self.nudge(g, wk)
        if db.kv_get("duty:baseline") is None and channel(g, "leads"):   # pick #1: the 30-day report, once
            db.kv_set("duty:baseline", db.now())
            try:
                await channel(g, "leads").send(view=await staff_report(g), allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as e:
                log.warning("baseline report: %s", e)

    @weekly.before_loop
    @board.before_loop
    async def _ready(self):
        await self.bot.wait_until_ready()

    async def close_week(self, g: discord.Guild, week: str):
        results = points.close_week(g, week)
        s = points.settings()
        leads = channel(g, "leads")
        if leads and results:
            try:
                await leads.send(view=report_view(g, week, results), allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as e:
                log.warning("duty report: %s", e)
        for r in results:
            m = g.get_member(r["user_id"])
            if m is None:
                continue
            if r["status"] != "leave":
                lines = points.week_lines(m.id, week)
                if r["status"] == "green":
                    msg = "You hit your goal. Thank you!"
                elif r["status"] == "amber":
                    msg = "Close! A couple more next week gets you there."
                else:
                    msg = ("A quiet week. If you're busy, press **Take a break** in #stats so it doesn't count. "
                           "Need help getting started? Ask in #staff-chat.")
                await _dm(m, dm_card(f"Your week: {r['pts']} / {r['target']}",
                                     msg + ("\n\n" + "\n".join(lines) if lines else "")))
                await asyncio.sleep(1.2)
            red = points.streak(m.id, week, "red")
            need = int(s.get("review_after_red", 2))
            if leads and red >= need and (red - need) % 2 == 0:
                await leads.send(view=review_card(m, red, week), allowed_mentions=discord.AllowedMentions.none())
            green = points.streak(m.id, week, "green")
            want = int(s.get("promote_after_green", 4))
            if leads and points.is_trial(m) and green and green % want == 0:
                await leads.send(view=promo_card(m, green), allowed_mentions=discord.AllowedMentions.none())
        points.touch()

    async def nudge(self, g: discord.Guild, week: str):
        below = float(points.settings().get("nudge_below", 0.4))
        tot = points.totals(week)
        for dept, m in points.team(g):
            tgt = points.target(dept)
            pts = tot.get(m.id, 0)
            if pts >= below * tgt or points.on_leave(m.id):
                continue
            await _dm(m, dm_card("A friendly check-in", f"Hey {m.display_name}! You're on **{pts} of {tgt}** points this "
                                 "week. A few tickets, warnings or bug checks before Monday would get you there.\n"
                                 "Busy week? Press **Take a break** in #stats and it won't count."))
            await asyncio.sleep(1.2)

    # ---------- points from what staff already do ----------
    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        g = entry.guild
        if not entry.user_id or entry.user_id == g.me.id:
            return
        target_id = getattr(entry.target, "id", None)
        if entry.action == A.member_update:
            before = getattr(entry.before, "timed_out_until", None)
            after = getattr(entry.after, "timed_out_until", None)
            if after is None and before is not None:     # timeout removed: undo it if it was under an hour
                ref, at = self._timeouts.pop(target_id, (None, 0))
                if ref and db.now() - at < 3600:
                    points.revoke(ref)
                return
            if after is None:
                return
        elif entry.action not in (A.ban, A.kick):
            return
        if not (entry.reason or "").strip():
            return
        member = entry.user if isinstance(entry.user, discord.Member) else g.get_member(entry.user_id)
        if member is None:
            return
        ref = f"audit:{entry.id}"
        if points.award(member, "mod_action", ref, note=f"{entry.action.name}: {entry.reason[:80]}") \
                and entry.action == A.member_update and target_id:
            self._timeouts[target_id] = (ref, db.now())

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        if msg.guild is None or msg.author.bot or not isinstance(msg.author, discord.Member):
            return
        cid = msg.channel.parent_id if isinstance(msg.channel, discord.Thread) else msg.channel.id
        g = msg.guild
        socials = channel(g, "socials")
        if socials and cid == socials.id and points.dept_of(msg.author) == "pr":
            points.award(msg.author, "social_post", f"social:{msg.id}")
            return
        keys = points.settings().get("chat_channels", ["chat"])
        if not any((c := channel(g, k)) and c.id == cid for k in keys) or not points.dept_of(msg.author):
            return
        day = points.day_of()
        k = (msg.author.id, day)
        self._chat[k] = self._chat.get(k, 0) + 1
        if self._chat[k] == 5:
            points.award(msg.author, "chat_day", f"chat:{msg.author.id}:{day}")
        if len(self._chat) > 2000:                      # keep only today
            self._chat = {kk: v for kk, v in self._chat.items() if kk[1] == day}

    # ---------- a team welcome: the guide pages for that team, by DM ----------
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles == after.roles or after.bot:
            return
        added = {r.id for r in after.roles} - {r.id for r in before.roles}
        if not added:
            return
        for dept, d in points.departments().items():
            if not any(r.id in added for r in points.dept_roles(after.guild, dept)):
                continue
            key = f"duty:welcomed:{after.id}:{dept}"
            if db.kv_get(key):
                continue
            db.kv_set(key, db.now())
            pages = {"mod": ["tickets", "moderation"], "qa": ["qa"], "pr": ["pr"]}.get(dept, [])
            t0, body0 = page_text("start")
            parts = [cards.title(f"Welcome to {d['name']}!", d.get("about", "")), cards.divider(),
                     cards.section(t0, body0)]
            for p in pages + ["duty"]:
                t, body = page_text(p)
                parts += [cards.divider(), cards.section(t, body)]
            parts.append(cards.small("You can read all of this again any time: the Staff guide is pinned in #staff-chat."))
            await _dm(after, cards.CardView(cards.container(*parts, accent=ui.BRAND)))
            points.touch()


async def setup(bot):
    await bot.add_cog(Duty(bot))
