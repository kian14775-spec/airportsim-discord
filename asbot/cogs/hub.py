"""/help for members and /staff for the team: one tidy panel each instead of many commands.
The live staff card (with its report menu) lives in stats.py.
"""
from __future__ import annotations

import datetime as dt

import discord
from discord import app_commands
from discord.ext import commands

from .. import cards, db, names, timeline, ui
from ..core import staff_only
from .community import template_files
from .forums import FeedbackModal, RpModal
from .news import PINGS, STATES, TARGETS
from .tickets import AppealModal, TopicSelect
from .verify import ROLE_GROUPS, RoleGroupSelect, start_verify


def _private(view_items) -> discord.ui.View:
    v = discord.ui.View(timeout=600)
    for item in view_items:
        v.add_item(item)
    return v


class Do(discord.ui.Button):
    """A button whose click runs `fn(interaction)` (for short-lived private panels)."""

    def __init__(self, label: str, fn, style=discord.ButtonStyle.secondary):
        super().__init__(label=label, style=style)
        self.fn = fn

    async def callback(self, interaction: discord.Interaction):
        await self.fn(interaction)


# ---------------- members: /help ----------------
async def _roles(interaction: discord.Interaction):
    items = [RoleGroupSelect(g) for g, (key, _, _) in ROLE_GROUPS.items()]
    await interaction.response.send_message("Pick what pings you and what you play on:",
                                            view=_private(items), ephemeral=True)


async def _profile(interaction: discord.Interaction):
    from .profile import profile_card
    await interaction.response.defer(ephemeral=True, thinking=True)
    await interaction.followup.send(view=await profile_card(interaction.user), ephemeral=True)


async def _support(interaction: discord.Interaction):
    await interaction.response.send_message("What do you need help with?",
                                            view=_private([TopicSelect(), AppealButton()]), ephemeral=True)


async def _airline(interaction: discord.Interaction):
    await interaction.response.send_message("What kind of server is it?", view=_private([AirlineKind()]),
                                            ephemeral=True)


async def _templates(interaction: discord.Interaction):
    files = template_files()
    if not files:
        return await interaction.response.send_message("Templates are being added. Check back soon.", ephemeral=True)
    await interaction.response.send_message("Which aircraft?", view=_private([TemplatePick(files)]), ephemeral=True)


def help_card() -> cards.CardView:
    return cards.CardView(cards.container(
        cards.title("How can we help?", "Everything you can do here, in one place. Only you can see this."),
        cards.divider(),
        cards.section("Your account", "**Verify** your Roblox account, set **your roles** and see **your "
                                      "profile** (show it off with `/profile`)."),
        cards.row(Do("Verify", start_verify, discord.ButtonStyle.success), Do("My roles", _roles),
                  Do("My profile", _profile), Do("Get support", _support, discord.ButtonStyle.primary)),
        cards.divider(),
        cards.section("The game", "Share an **idea**, report a **bug**, post **your airline** or grab a "
                                  "**livery template**."),
        cards.row(Do("Suggest an idea", lambda i: i.response.send_modal(FeedbackModal("Suggestion"))),
                  Do("Report a bug", lambda i: i.response.send_modal(FeedbackModal("Bug"))),
                  Do("My airline post", _airline), Do("Livery templates", _templates)),
        accent=ui.BRAND, banner_name="help"), timeout=None)   # members often click long after opening it


class AppealButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Appeal a game ban", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        blocked = interaction.client.get_cog("Tickets").blocked_reason(interaction.user)
        if blocked:
            return await interaction.response.send_message(blocked, ephemeral=True)
        await interaction.response.send_modal(AppealModal())


class AirlineKind(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder="Airline, airport or cargo?",
                         options=[discord.SelectOption(label=k) for k in ("Airline", "Airport", "Cargo")])

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RpModal(self.values[0], hiring=False, logo=None))


class TemplatePick(discord.ui.Select):
    def __init__(self, files):
        super().__init__(placeholder="Choose an aircraft",
                         options=[discord.SelectOption(label=p.stem[:100]) for p in files[:25]])

    async def callback(self, interaction: discord.Interaction):
        await interaction.client.get_cog("Community").send_template(interaction, self.values[0])


# ---------------- staff: /staff ----------------
ACTIONS = [("duty", "Staff Duty", "Your week, and for leads: give points, 30-day staff report"),
           ("tickets", "Tickets", "Browse open and closed tickets, read transcripts"),
           ("announce", "Post an announcement", "announcements, dev-logs or socials"),
           ("status", "Game status", "All good, issues, down, maintenance"),
           ("issues", "Known issues and next update", "What shows on the support card"),
           ("contest", "Livery of the Month", "Start, end or refresh the board"),
           ("event", "Plan an event", "Discord event, post, reminder and recap, all automatic"),
           ("guide", "Staff guide", "How everything works, in plain words"),
           ("member", "Look up a member", "Roblox link, tickets, strikes, timeline"),
           ("warn", "Warn a member", "Give a strike; timeouts happen by themselves"),
           ("answers", "Instant answers", "Add, see and remove the helper's answers"),
           ("today", "Today's numbers", "Joins, visits, group, tickets so far today"),
           ("stats", "Support numbers", "This month's tickets and ratings"),
           ("health", "Bot health", "Latency and uptime")]


class StaffMenu(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder="What do you want to do?", options=[
            discord.SelectOption(label=label, value=key, description=desc) for key, label, desc in ACTIONS])

    async def callback(self, interaction: discord.Interaction):
        bot, act = interaction.client, self.values[0]
        if act == "tickets":
            from .. import ticketlist
            return await ticketlist.send(interaction)
        if act == "duty":
            from .duty import staff_panel
            return await interaction.response.send_message(view=staff_panel(interaction.user), ephemeral=True)
        if act == "event":
            from .events import PlanModal
            return await interaction.response.send_modal(PlanModal())
        if act == "guide":
            from .duty import page_view
            return await interaction.response.send_message(view=page_view("start"), ephemeral=True)
        if act == "announce":
            return await interaction.response.send_message("Where, and who gets pinged?",
                                                           view=AnnounceSetup(), ephemeral=True)
        if act == "status":
            return await interaction.response.send_message("Set the game status:",
                                                           view=_private([StatusPick()]), ephemeral=True)
        if act == "issues":
            return await interaction.response.send_modal(IssuesModal())
        if act == "contest":
            return await interaction.response.send_message("Livery of the Month:", view=ContestView(), ephemeral=True)
        if act == "member":
            return await interaction.response.send_message("Who?", view=_private([MemberPick()]), ephemeral=True)
        if act == "today":   # 3 Roblox calls: answer first, they can take longer than Discord waits
            await interaction.response.defer(ephemeral=True, thinking=True)
            return await interaction.followup.send(view=await bot.get_cog("Stats").so_far(), ephemeral=True)
        if act == "warn":
            return await interaction.response.send_message("Who?", view=_private([WarnPick()]), ephemeral=True)
        if act == "answers":
            from .answers import list_card
            rows = db.q("SELECT * FROM faq ORDER BY hits DESC, id DESC")
            items = [AddAnswerButton()] + ([AnswerRemove(rows)] if rows else [])
            return await interaction.response.send_message(embed=list_card(), view=_private(items), ephemeral=True)
        if act == "stats":
            e = bot.get_cog("Tickets").stats_embed(dt.datetime.now(dt.timezone.utc))
            return await interaction.response.send_message(embed=e, ephemeral=True)
        if act == "health":
            return await interaction.response.send_message(bot.get_cog("Health").summary(), ephemeral=True)


class AnnounceSetup(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)
        self.where, self.ping = next(iter(TARGETS)), "none"

    @discord.ui.select(placeholder="Post in...", options=[discord.SelectOption(label=k) for k in TARGETS])
    async def pick_where(self, interaction: discord.Interaction, sel: discord.ui.Select):
        self.where = sel.values[0]
        await interaction.response.defer()

    @discord.ui.select(placeholder="Ping (optional)", options=[discord.SelectOption(label=k) for k in PINGS])
    async def pick_ping(self, interaction: discord.Interaction, sel: discord.ui.Select):
        self.ping = sel.values[0]
        await interaction.response.defer()

    @discord.ui.button(label="Write it", style=discord.ButtonStyle.primary)
    async def write(self, interaction: discord.Interaction, _):
        await interaction.client.get_cog("News").start_announce(interaction, self.where, self.ping)


class StatusPick(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder="Game status", options=[
            discord.SelectOption(label=v[0], value=k) for k, v in STATES.items()])

    async def callback(self, interaction: discord.Interaction):
        await interaction.client.get_cog("News").set_state(self.values[0])
        await interaction.response.send_message(f"Status set: {STATES[self.values[0]][0]}.", ephemeral=True)


class IssuesModal(discord.ui.Modal, title="Support card"):
    add = discord.ui.TextInput(label="Add a known issue", required=False, max_length=200)
    remove = discord.ui.TextInput(label="Remove issues containing (or 'all')", required=False, max_length=100)
    nxt = discord.ui.TextInput(label="Next update (blank = leave as is)", required=False, max_length=80,
                               placeholder="Fri 7 Nov, 6pm UK")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.client.get_cog("News").edit_status(
            add=self.add.value.strip(), remove=self.remove.value.strip(),
            next_update=self.nxt.value.strip() if self.nxt.value.strip() else None)
        await interaction.response.send_message("Support card updated.", ephemeral=True)


class ContestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success)
    async def start(self, interaction: discord.Interaction, _):
        await interaction.client.get_cog("Community").contest_start(interaction)

    @discord.ui.button(label="End and crown winner", style=discord.ButtonStyle.danger)
    async def end(self, interaction: discord.Interaction, _):
        await interaction.client.get_cog("Community").contest_end(interaction)

    @discord.ui.button(label="Refresh live board", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, _):
        await interaction.response.send_message("Refreshing.", ephemeral=True)
        await interaction.client.get_cog("Forums").contest_board()


class WarnPick(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder="Pick a member")

    async def callback(self, interaction: discord.Interaction):
        from .strikes import StrikeModal
        member = interaction.guild.get_member(self.values[0].id)
        if not member:
            return await interaction.response.send_message("They're not in the server.", ephemeral=True)
        await interaction.response.send_modal(StrikeModal(member))


class AddAnswerButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Add an answer", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        from .answers import AnswerModal
        await interaction.response.send_modal(AnswerModal())


class AnswerRemove(discord.ui.Select):
    def __init__(self, rows):
        super().__init__(placeholder="Remove an answer", options=[
            discord.SelectOption(label=f"#{r['id']} · {r['keywords'][:80]}", value=str(r["id"])) for r in rows[:25]])

    async def callback(self, interaction: discord.Interaction):
        from .. import faq
        faq.remove(int(self.values[0]))
        await interaction.response.send_message(f"Answer #{self.values[0]} removed.", ephemeral=True)


class MemberPick(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder="Pick a member")

    async def callback(self, interaction: discord.Interaction):
        m = self.values[0]
        link = db.one("SELECT * FROM links WHERE discord_id=?", m.id)
        tickets = db.q("SELECT id, status, rating FROM tickets WHERE user_id=? ORDER BY id DESC LIMIT 5", m.id)
        roblox = (f"[{link['roblox_name']}](https://www.roblox.com/users/{link['roblox_id']}/profile) · "
                  f"linked <t:{int(link['linked_at'])}:R>") if link else "Not linked"
        tl = ", ".join(f"{t['id']:04d}{' ★' + str(t['rating']) if t['rating'] else ''}" for t in tickets) or "None"
        joined = f"<t:{int(m.joined_at.timestamp())}:R>" if getattr(m, "joined_at", None) else "-"
        from .strikes import RemoveStrike, active, strikes_line
        e = ui.card(m.display_name, ui.kv(("Discord", m.mention), ("Roblox", roblox), ("Joined", joined),
                                          ("Tickets", tl), ("Strikes", strikes_line(m.id))), thumb=m.display_avatar.url)
        e.add_field(name="Timeline", value=names.swap(timeline.lines(m.id), 1024) or "Nothing yet.", inline=False)
        from ..transcripts import HistoryButton
        v = discord.ui.View(timeout=600)
        if tickets:
            v.add_item(HistoryButton(m.id))
        if active(m.id):
            v.add_item(RemoveStrike(active(m.id)))
        await interaction.response.send_message(embed=e, view=v, ephemeral=True)


# ---------------- cog ----------------
class Hub(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def guild(self) -> discord.Guild:
        return self.bot.get_guild(self.bot.guild_id)

    @app_commands.command(description="Everything you can do here, in one place")
    async def help(self, interaction: discord.Interaction):
        await interaction.response.send_message(view=help_card(), files=cards.files_for("help"), ephemeral=True)

    @app_commands.command(description="Staff tools")
    @app_commands.default_permissions(moderate_members=True)
    @staff_only()
    async def staff(self, interaction: discord.Interaction):
        view = cards.CardView(cards.container(
            cards.title("Staff tools", "Pick what you want to do. Everything here only shows to you."),
            cards.divider(),
            cards.text("\n".join(f"**{label}** · {desc}" for _, label, desc in ACTIONS)),
            cards.row(StaffMenu()), accent=ui.NEUTRAL), timeout=600)
        await interaction.response.send_message(view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Hub(bot))
