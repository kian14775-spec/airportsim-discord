"""Bug -> fix loop in #ideas-and-bugs.

Every post tagged Bug gets a small QA card with buttons (QA and devs only):
  I can reproduce / Can't reproduce / Duplicate / Fixed next update.
The first "I can reproduce" adds the Confirmed tag and tells the reporter. Devs press Fixed (tag Fixed).
When the game updates (news.py sees Roblox's `updated` change), every Fixed bug becomes Done: the
existing "shipped" DM in community.py then tells the reporter and the upvoters, and each tester who
confirmed it gets points again. Tags set by hand in Discord are followed too.
Settings: config/bot.yaml `bugs:`. Points: `duty: rules:` bug_confirmed / bug_fixed.
"""
from __future__ import annotations

import json
import re

import discord
from discord.ext import commands

from .. import cards, db, names, points, ui
from ..core import cfg, channel, log, role

SCHEMA = """
CREATE TABLE IF NOT EXISTS bugs (thread_id INTEGER PRIMARY KEY, reporter_id INTEGER, card_id INTEGER,
    state TEXT NOT NULL DEFAULT 'new', repro TEXT NOT NULL DEFAULT '[]', norepro TEXT NOT NULL DEFAULT '[]',
    note TEXT NOT NULL DEFAULT '', created REAL NOT NULL, fixed_at REAL, done_at REAL);
"""
db.conn().executescript(SCHEMA)
THREAD_URL = re.compile(r"discord(?:app)?\.com/channels/\d+/(\d+)")


def settings() -> dict:
    return cfg().get("bugs", {})


def tag_names(thread: discord.Thread) -> set[str]:
    return {t.name for t in thread.applied_tags}


def forum_tags(forum: discord.ForumChannel, *wanted: str) -> list[discord.ForumTag]:
    return [t for t in forum.available_tags if t.name in wanted]


def bug(tid: int):
    return db.one("SELECT * FROM bugs WHERE thread_id=?", tid)


def _ids(row, col: str) -> list[int]:
    return json.loads(row[col]) if row else []


def can_test(member: discord.abc.User) -> bool:
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator or points.is_lead(member):
        return True
    have = {r.id for r in member.roles}
    return any((r := role(member.guild, k)) and r.id in have for k in settings().get("qa_roles", []))


def can_fix(member: discord.abc.User) -> bool:
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator or points.is_lead(member):
        return True
    have = {r.id for r in member.roles}
    return any((r := role(member.guild, k)) and r.id in have for k in settings().get("fix_roles", ["developer"]))


def card_view(tid: int) -> cards.CardView:
    row = bug(tid)
    rep, nor = _ids(row, "repro"), _ids(row, "norepro")
    state = row["state"] if row else "new"
    if state == "dup":
        status = "Closed as a duplicate. " + (row["note"] or "")
    elif state == "done":
        status = "Fixed and out in the game. Thanks for testing!"
    elif state == "fixed":
        status = "**Fixed in the next update.** Reporter and upvoters get a DM when it's out."
    elif rep:
        status = f"**Confirmed** by {', '.join(names.who(u) for u in rep[:5])}."
    else:
        status = "Not tested yet."
    tested = f"{len(rep)} can reproduce · {len(nor)} can't" if (rep or nor) else ""
    parts = [cards.text("**QA check**\n" + status)]
    if tested:
        parts.append(cards.small(tested))
    if state not in ("dup", "done"):
        parts += [cards.small("For QA and devs: test it in game, then press one."),
                  cards.row(BugButton("repro", tid), BugButton("norepro", tid), BugButton("dup", tid),
                            BugButton("fixed", tid))]
    return cards.CardView(cards.container(*parts, accent=ui.GOOD if state in ("fixed", "done") else ui.SLATE))


async def refresh_card(thread: discord.Thread) -> None:
    row = bug(thread.id)
    if not row or not row["card_id"]:
        return
    try:
        msg = thread.get_partial_message(row["card_id"])
        await msg.edit(view=card_view(thread.id))
    except discord.HTTPException as e:
        log.warning("bug card %s: %s", thread.id, e)


async def set_tags(thread: discord.Thread, add: tuple[str, ...] = (), drop: tuple[str, ...] = ()) -> None:
    forum = thread.parent
    if not isinstance(forum, discord.ForumChannel):
        return
    keep = [t for t in thread.applied_tags if t.name not in drop and t.name not in add]
    new = (keep + forum_tags(forum, *add))[:5]
    if {t.id for t in new} == {t.id for t in thread.applied_tags}:
        return
    try:
        await thread.edit(applied_tags=new, archived=False)
    except discord.HTTPException as e:
        log.warning("bug tags %s: %s", thread.id, e)


async def tell(user_id: int | None, client: discord.Client, title: str, body: str) -> None:
    if not user_id or user_id == client.user.id:
        return
    try:
        u = client.get_user(user_id) or await client.fetch_user(user_id)
        await u.send(view=cards.CardView(cards.container(cards.title(title), cards.text(body), accent=ui.SLATE)))
    except discord.HTTPException:
        pass


class DupModal(discord.ui.Modal, title="Duplicate of..."):
    link = discord.ui.TextInput(label="Link to the original post", placeholder="https://discord.com/channels/...",
                                max_length=200)

    def __init__(self, thread: discord.Thread):
        super().__init__()
        self.thread = thread

    async def on_submit(self, interaction: discord.Interaction):
        m = THREAD_URL.search(self.link.value)
        target = interaction.guild.get_thread(int(m.group(1))) if m else None
        if target is None and m:
            try:
                target = await interaction.guild.fetch_channel(int(m.group(1)))
            except discord.HTTPException:
                target = None
        if not isinstance(target, discord.Thread) or target.parent_id != self.thread.parent_id or target.id == self.thread.id:
            return await interaction.response.send_message("That isn't another post in this forum. Copy the link with "
                                                           "right-click on the post > Copy Link.", ephemeral=True)
        db.run("UPDATE bugs SET state='dup', note=? WHERE thread_id=?", f"Original: {target.jump_url}", self.thread.id)
        points.award(interaction.user, "bug_confirmed", f"bug:{self.thread.id}:{interaction.user.id}", note="duplicate")
        await interaction.response.send_message(f"Marked as a duplicate of {target.jump_url}.", ephemeral=True)
        await refresh_card(self.thread)
        try:
            await self.thread.send(f"This was already reported here: {target.jump_url}\n"
                                   "Upvote that post so it rises. Closing this one.")
            await self.thread.edit(archived=True, locked=True)
        except discord.HTTPException as e:
            log.warning("bug dup %s: %s", self.thread.id, e)


class BugButton(discord.ui.DynamicItem[discord.ui.Button], template=r"asbot:bug:(?P<act>[a-z]+):(?P<tid>\d+)"):
    LABELS = {"repro": ("I can reproduce", discord.ButtonStyle.success),
              "norepro": ("Can't reproduce", discord.ButtonStyle.secondary),
              "dup": ("Duplicate", discord.ButtonStyle.secondary),
              "fixed": ("Fixed next update", discord.ButtonStyle.primary)}

    def __init__(self, act: str, tid: int):
        label, style = self.LABELS[act]
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=f"asbot:bug:{act}:{tid}"))
        self.act, self.tid = act, tid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["act"], int(match["tid"]))

    async def callback(self, interaction: discord.Interaction):
        thread, u = interaction.channel, interaction.user
        if not isinstance(thread, discord.Thread):
            return await interaction.response.send_message("This only works inside a bug post.", ephemeral=True)
        row = bug(self.tid)
        if row is None:
            return await interaction.response.send_message("This bug isn't tracked any more.", ephemeral=True)
        if self.act == "fixed":
            if not can_fix(u):
                return await interaction.response.send_message("Only devs can mark a bug fixed.", ephemeral=True)
            db.run("UPDATE bugs SET state='fixed', fixed_at=? WHERE thread_id=?", db.now(), self.tid)
            await interaction.response.edit_message(view=card_view(self.tid))
            return await set_tags(thread, add=(settings().get("fixed_tag", "Fixed"),), drop=("New", "Under review"))
        if not can_test(u):
            return await interaction.response.send_message("These buttons are for QA. Thanks for looking! "
                                                           "Upvote the post if it happens to you too.", ephemeral=True)
        if self.act == "dup":
            return await interaction.response.send_modal(DupModal(thread))
        rep, nor = _ids(row, "repro"), _ids(row, "norepro")
        first = self.act == "repro" and not rep
        rep, nor = [x for x in rep if x != u.id], [x for x in nor if x != u.id]
        (rep if self.act == "repro" else nor).append(u.id)
        state = "confirmed" if rep and row["state"] == "new" else row["state"]
        db.run("UPDATE bugs SET repro=?, norepro=?, state=? WHERE thread_id=?", json.dumps(rep), json.dumps(nor),
               state, self.tid)
        got = points.award(u, "bug_confirmed", f"bug:{self.tid}:{u.id}", note=thread.name[:80])
        await interaction.response.edit_message(view=card_view(self.tid))
        await interaction.followup.send("Thanks for testing!" + (f" +{got} points." if got else ""), ephemeral=True)
        if first:
            await set_tags(thread, add=(settings().get("confirmed_tag", "Confirmed"),), drop=("New",))
            await tell(db.kv_get(f"feedback_author:{self.tid}") or row["reporter_id"], interaction.client,
                       "QA confirmed your bug",
                       f"**{thread.name}**: our testers can see it too, so it's on the devs' list now. "
                       f"Thanks for reporting!\n{thread.jump_url}")


class Bugs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_dynamic_items(BugButton)

    @property
    def guild(self) -> discord.Guild | None:
        return self.bot.get_guild(self.bot.guild_id)

    def is_bug_post(self, thread: discord.Thread) -> bool:
        fb = channel(thread.guild, settings().get("forum", "feedback"))
        return bool(fb and thread.parent_id == fb.id and settings().get("bug_tag", "Bug") in tag_names(thread))

    async def track(self, thread: discord.Thread) -> None:
        if bug(thread.id):
            return
        reporter = db.kv_get(f"feedback_author:{thread.id}") or thread.owner_id
        db.run("INSERT OR IGNORE INTO bugs(thread_id, reporter_id, created) VALUES(?, ?, ?)", thread.id, reporter, db.now())
        try:
            msg = await thread.send(view=card_view(thread.id), allowed_mentions=discord.AllowedMentions.none())
            db.run("UPDATE bugs SET card_id=? WHERE thread_id=?", msg.id, thread.id)
        except discord.HTTPException as e:
            log.warning("bug card %s: %s", thread.id, e)

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        if self.is_bug_post(thread):
            await self.track(thread)

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        if not self.is_bug_post(after):
            return
        if not bug(after.id):                           # tagged Bug later
            return await self.track(after)
        had, now, s = tag_names(before), tag_names(after), settings()
        fixed, done = s.get("fixed_tag", "Fixed"), s.get("done_tag", "Done")
        row = bug(after.id)
        if fixed in now and fixed not in had and row["state"] not in ("fixed", "done"):   # set by hand
            db.run("UPDATE bugs SET state='fixed', fixed_at=? WHERE thread_id=?", db.now(), after.id)
            await refresh_card(after)
        if done in now and done not in had and row["state"] != "done":
            await self.mark_done(after)

    async def mark_done(self, thread: discord.Thread) -> None:
        row = bug(thread.id)
        db.run("UPDATE bugs SET state='done', done_at=? WHERE thread_id=?", db.now(), thread.id)
        for uid in _ids(row, "repro"):                  # testers who confirmed it get credit when it ships
            m = thread.guild.get_member(uid)
            if m:
                points.award(m, "bug_fixed", f"bugfixed:{thread.id}:{uid}", note=thread.name[:80])
        await refresh_card(thread)

    @commands.Cog.listener()
    async def on_game_updated(self, old: str, new: str):
        """A new game version is out: every Fixed bug becomes Done (the Done tag sends the shipped DMs)."""
        g = self.guild
        if g is None:
            return
        s = settings()
        n = 0
        for row in db.q("SELECT thread_id FROM bugs WHERE state='fixed'"):
            th = g.get_thread(row["thread_id"])
            if th is None:
                try:
                    th = await g.fetch_channel(row["thread_id"])
                except discord.HTTPException:
                    db.run("UPDATE bugs SET state='done', done_at=? WHERE thread_id=?", db.now(), row["thread_id"])
                    continue
            await set_tags(th, add=(s.get("done_tag", "Done"),), drop=(s.get("fixed_tag", "Fixed"), "New",
                                                                         "Under review", "Planned"))
            if bug(th.id)["state"] != "done":            # on_thread_update may already have done it
                await self.mark_done(th)
            n += 1
        if n:
            log.info("game updated (%s -> %s): %d fixed bugs marked done", old, new, n)


async def setup(bot):
    await bot.add_cog(Bugs(bot))
