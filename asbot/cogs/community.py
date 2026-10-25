"""Feedback forum (votes + "your idea shipped" DMs), livery templates + Livery of the Month,
join-to-create party voice, and Discord perks for the game."""
from __future__ import annotations

import asyncio
import datetime as dt
import re
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from .. import db, opencloud
from ..core import ROOT, cfg, channel, embed, is_staff, log, log_staff, role, staff_only

UPVOTE = "👍"
TEMPLATES = ROOT / "data" / "templates"
TEMPLATE_EXT = {".png", ".jpg", ".jpeg", ".psd", ".zip", ".pdn", ".kra"}


def star() -> str:
    return cfg()["livery_contest"]["vote_emoji"]


def template_files() -> list[Path]:
    TEMPLATES.mkdir(parents=True, exist_ok=True)
    return sorted(p for p in TEMPLATES.iterdir() if p.suffix.lower() in TEMPLATE_EXT)


class FeedbackModal(discord.ui.Modal):
    def __init__(self, kind: str):
        super().__init__(title="New suggestion" if kind == "Suggestion" else "Report a bug")
        self.kind = kind
        self.head = discord.ui.TextInput(label="Short title", max_length=90)
        self.body = discord.ui.TextInput(label="Details" if kind == "Suggestion" else "What happens, and where?",
                                         style=discord.TextStyle.paragraph, max_length=2000)
        self.add_item(self.head)
        self.add_item(self.body)

    async def on_submit(self, interaction: discord.Interaction):
        forum = channel(interaction.guild, "feedback")
        if not isinstance(forum, discord.ForumChannel):
            return await interaction.response.send_message("Feedback isn't set up yet.", ephemeral=True)
        tags = [t for t in forum.available_tags if t.name == self.kind]
        made = await forum.create_thread(
            name=self.head.value, applied_tags=tags,
            content=f"{self.body.value}\n\n-# Posted by {interaction.user.mention}",
            allowed_mentions=discord.AllowedMentions.none())
        db.kv_set(f"feedback_author:{made.thread.id}", interaction.user.id)
        await interaction.response.send_message(f"Posted: {made.thread.mention}", ephemeral=True)


class PartyPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _owner_check(self, interaction) -> bool:
        row = db.one("SELECT owner_id FROM parties WHERE channel_id=?", interaction.channel_id)
        if row and (row["owner_id"] == interaction.user.id or is_staff(interaction.user)):
            return True
        await interaction.response.send_message("Only the party owner can do that.", ephemeral=True)
        return False

    @discord.ui.button(label="Lock", style=discord.ButtonStyle.secondary, custom_id="asbot:party:lock")
    async def lock(self, interaction: discord.Interaction, _):
        if not await self._owner_check(interaction):
            return
        ch = interaction.channel
        verified = role(interaction.guild, "verified") or interaction.guild.default_role
        ow = ch.overwrites_for(verified)
        locked = ow.connect is False
        ow.connect = None if locked else False
        await ch.set_permissions(verified, overwrite=ow)
        await interaction.response.send_message("Unlocked." if locked else "Locked. Nobody new can join.",
                                                ephemeral=True)

    @discord.ui.button(label="Rename", style=discord.ButtonStyle.secondary, custom_id="asbot:party:rename")
    async def rename(self, interaction: discord.Interaction, _):
        if await self._owner_check(interaction):
            await interaction.response.send_modal(PartyModal("name"))

    @discord.ui.button(label="Player limit", style=discord.ButtonStyle.secondary, custom_id="asbot:party:limit")
    async def limit(self, interaction: discord.Interaction, _):
        if await self._owner_check(interaction):
            await interaction.response.send_modal(PartyModal("limit"))


class PartyModal(discord.ui.Modal):
    def __init__(self, what: str):
        super().__init__(title="Rename party" if what == "name" else "Player limit")
        self.what = what
        self.value = discord.ui.TextInput(label="New name" if what == "name" else "Max players (0 = no limit)",
                                          max_length=40 if what == "name" else 2)
        self.add_item(self.value)

    async def on_submit(self, interaction: discord.Interaction):
        ch = interaction.channel
        if self.what == "name":
            await ch.edit(name=self.value.value[:40])
        else:
            n = int(self.value.value) if self.value.value.isdigit() else 0
            await ch.edit(user_limit=min(n, 99))
        await interaction.response.send_message("Done.", ephemeral=True)


class Community(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(PartyPanel())

    @property
    def guild(self) -> discord.Guild:
        return self.bot.get_guild(self.bot.guild_id)

    @commands.Cog.listener()
    async def on_ready(self):
        await self.ensure_templates_post()
        await self.cleanup_parties()

    # ---------- feedback ----------
    @app_commands.command(description="Suggest something for the game")
    async def suggest(self, interaction: discord.Interaction):
        await interaction.response.send_modal(FeedbackModal("Suggestion"))

    @app_commands.command(description="Report a bug in the game")
    async def bug(self, interaction: discord.Interaction):
        await interaction.response.send_modal(FeedbackModal("Bug"))

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        fb, lv = channel(thread.guild, "feedback"), channel(thread.guild, "liveries")
        if thread.parent_id not in {getattr(fb, "id", 0), getattr(lv, "id", 0)}:
            return
        emoji = UPVOTE if fb and thread.parent_id == fb.id else star()
        for _ in range(5):   # the starter message can lag a moment behind the thread
            try:
                starter = thread.starter_message or await thread.fetch_message(thread.id)
                await starter.add_reaction(emoji)
                return
            except discord.NotFound:
                await asyncio.sleep(1)
            except discord.HTTPException:
                return

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        fb = channel(after.guild, "feedback")
        if not fb or after.parent_id != fb.id:
            return
        had = {t.name for t in before.applied_tags}
        now = {t.name for t in after.applied_tags}
        if "Done" in now and "Done" not in had and not db.one("SELECT 1 FROM shipped WHERE thread_id=?", after.id):
            db.run("INSERT INTO shipped VALUES(?, ?)", after.id, db.now())
            asyncio.create_task(self.notify_shipped(after))

    async def notify_shipped(self, thread: discord.Thread):
        users: dict[int, discord.abc.User] = {}
        try:
            starter = await thread.fetch_message(thread.id)
            for r in starter.reactions:
                if str(r.emoji) == UPVOTE:
                    async for u in r.users():
                        if not u.bot:
                            users[u.id] = u
        except discord.HTTPException:
            pass
        author_id = db.kv_get(f"feedback_author:{thread.id}") or thread.owner_id
        if author_id and author_id != self.bot.user.id:
            users.setdefault(author_id, self.bot.get_user(author_id) or await self.bot.fetch_user(author_id))
        upd = channel(thread.guild, "updates")
        latest = None
        if upd:
            async for m in upd.history(limit=1):
                latest = m.jump_url
        e = embed("An idea you backed just shipped",
                  f"**{thread.name}** is now in Airport Simulator." + (f"\n[See the update]({latest})" if latest else "")
                  + f"\n[View the post]({thread.jump_url})")
        sent = 0
        for u in users.values():
            try:
                await u.send(embed=e)
                sent += 1
            except discord.HTTPException:
                pass
            await asyncio.sleep(1.2)   # stay well under DM rate limits
        await thread.send(f"Marked as done. Told {sent} supporter(s).")

    # ---------- liveries ----------
    async def ensure_templates_post(self):
        forum = channel(self.guild, "liveries")
        if not isinstance(forum, discord.ForumChannel):
            return
        files = template_files()
        names = "\n".join(f"• `{p.stem}`" for p in files) or "Templates are being added. Check back soon."
        text = ("**Livery templates**\nDownload a template, paint it, then post your livery here with the "
                "**Showcase** tag. Use `/template` anywhere to get one sent to you.\n\n" + names)
        tid = db.kv_get("templates_thread")
        thread = self.guild.get_thread(tid) if tid else None
        if thread is None and tid:
            try:
                thread = await self.bot.fetch_channel(tid)
            except discord.HTTPException:
                thread = None
        if thread is None:
            tags = [t for t in forum.available_tags if t.name == "Template"]
            made = await forum.create_thread(name="Templates", content=text, applied_tags=tags)
            thread = made.thread
            db.kv_set("templates_thread", thread.id)
            try:
                await thread.edit(pinned=True, locked=True)
            except discord.HTTPException as e:
                log.warning("templates post pin/lock: %s", e)
        else:
            try:
                starter = await thread.fetch_message(thread.id)
                if starter.content != text:
                    await starter.edit(content=text)
            except discord.HTTPException as e:
                log.warning("templates post edit: %s", e)

    @app_commands.command(description="Get a livery template sent to you")
    async def template(self, interaction: discord.Interaction, aircraft: str):
        match = next((p for p in template_files() if p.stem.lower() == aircraft.lower()), None)
        if not match:
            return await interaction.response.send_message("No template with that name yet.", ephemeral=True)
        await interaction.response.send_message(f"Here's the **{match.stem}** template.",
                                                file=discord.File(match), ephemeral=True)

    @template.autocomplete("aircraft")
    async def _tpl_names(self, interaction, current: str):
        return [app_commands.Choice(name=p.stem, value=p.stem) for p in template_files()
                if current.lower() in p.stem.lower()][:25]

    @app_commands.command(description="Add or replace a livery template (staff)")
    @staff_only()
    async def template_add(self, interaction: discord.Interaction, name: str, file: discord.Attachment):
        ext = Path(file.filename).suffix.lower()
        if ext not in TEMPLATE_EXT or file.size > 24_000_000:
            return await interaction.response.send_message(
                f"Use {', '.join(sorted(TEMPLATE_EXT))} under 24 MB.", ephemeral=True)
        safe = re.sub(r"[^A-Za-z0-9 _.-]", "", name).strip()[:40] or "template"
        for old in template_files():
            if old.stem.lower() == safe.lower():
                old.unlink()
        await file.save(TEMPLATES / f"{safe}{ext}")
        await self.ensure_templates_post()
        await interaction.response.send_message(f"Template **{safe}** saved.", ephemeral=True)

    contest = app_commands.Group(name="contest", description="Livery of the Month")

    @contest.command(name="start", description="Start this month's Livery of the Month")
    @staff_only()
    async def contest_start(self, interaction: discord.Interaction):
        db.kv_set("contest_start", db.now())
        forum = channel(interaction.guild, "liveries")
        ann = channel(interaction.guild, "announcements")
        text = (f"**Livery of the Month is open!** Post your livery in {forum.mention if forum else '#liveries'} "
                f"with the **Showcase** tag. Vote with {star()} on your favourites. The winner gets added to the game.")
        if ann:
            await ann.send(embed=embed("Livery of the Month", text))
        await interaction.response.send_message("Contest started.", ephemeral=True)

    @contest.command(name="end", description="Count the votes and crown the winner")
    @staff_only()
    async def contest_end(self, interaction: discord.Interaction):
        start = db.kv_get("contest_start")
        forum = channel(interaction.guild, "liveries")
        if not start or not isinstance(forum, discord.ForumChannel):
            return await interaction.response.send_message("No contest running.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        since = dt.datetime.fromtimestamp(start, dt.timezone.utc)
        threads = list(forum.threads)
        async for th in forum.archived_threads(limit=200):
            threads.append(th)
        best = (0, None, None)
        for th in threads:
            if not th.created_at or th.created_at < since or "Showcase" not in {t.name for t in th.applied_tags}:
                continue
            try:
                starter = await th.fetch_message(th.id)
            except discord.HTTPException:
                continue
            votes = sum(max(0, r.count - (1 if r.me else 0)) for r in starter.reactions if str(r.emoji) == star())
            if votes > best[0]:
                best = (votes, th, starter.author)
        db.kv_set("contest_start", None)
        if not best[1]:
            return await interaction.followup.send("No Showcase posts with votes.", ephemeral=True)
        votes, th, author = best
        winner = interaction.guild.get_member(author.id) if author else None
        win_role = role(interaction.guild, "livery_winner")
        if winner and win_role:
            for m in list(win_role.members):
                await m.remove_roles(win_role, reason="new Livery of the Month")
            await winner.add_roles(win_role, reason="Livery of the Month")
        text = f"{winner.mention if winner else 'Unknown'} wins with **{th.name}** ({votes} {star()}). {th.jump_url}"
        ann = channel(interaction.guild, "announcements")
        if ann:
            await ann.send(embed=embed("Livery of the Month winner", text))
        await th.send(f"This livery won Livery of the Month with {votes} votes!")
        await interaction.followup.send(f"Winner: {text}", ephemeral=True)
        await log_staff(interaction.guild, f"Livery of the Month: {text} (add it to the game)")

    # ---------- party voice ----------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before, after):
        hub = channel(member.guild, "create_party")
        if hub and after.channel and after.channel.id == hub.id:
            name = cfg()["party"]["name_format"].format(name=member.display_name)[:40]
            ow = dict(hub.overwrites)
            ow[member] = discord.PermissionOverwrite(view_channel=True, connect=True, move_members=True)
            vc = await member.guild.create_voice_channel(name, category=hub.category, overwrites=ow,
                                                         reason="party")
            db.run("INSERT INTO parties VALUES(?, ?)", vc.id, member.id)
            try:
                await member.move_to(vc)
            except discord.HTTPException:
                pass
            await vc.send(embed=embed("Your party", f"{member.mention} owns this channel. "
                                                    "It disappears when everyone leaves."), view=PartyPanel())
        if before.channel and before.channel != after.channel:
            await self._maybe_remove(before.channel)

    async def _maybe_remove(self, ch):
        if not db.one("SELECT 1 FROM parties WHERE channel_id=?", ch.id) or ch.members:
            return
        db.run("DELETE FROM parties WHERE channel_id=?", ch.id)
        try:
            await ch.delete(reason="party empty")
        except discord.HTTPException:
            pass

    async def cleanup_parties(self):
        for row in db.q("SELECT channel_id FROM parties"):
            ch = self.guild.get_channel(row["channel_id"])
            if ch is None:
                db.run("DELETE FROM parties WHERE channel_id=?", row["channel_id"])
            else:
                await self._maybe_remove(ch)

    # ---------- Discord perks for the game ----------
    @commands.Cog.listener()
    async def on_roblox_linked(self, member: discord.Member, roblox_id: int):
        await self.push_perks(member, roblox_id)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.premium_since != after.premium_since:
            link = db.one("SELECT roblox_id FROM links WHERE discord_id=?", after.id)
            if link:
                await self.push_perks(after, link["roblox_id"])

    async def push_perks(self, member: discord.Member, roblox_id: int):
        perks = {"member": True, "booster": member.premium_since is not None, "discordId": str(member.id),
                 "updated": int(db.now())}
        if not await opencloud.set_perks(roblox_id, perks) and opencloud.available():
            log.warning("perks: could not write for %s", roblox_id)


async def setup(bot):
    await bot.add_cog(Community(bot))
