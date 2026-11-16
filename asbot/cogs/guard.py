"""Guard: keeps raids, spam, scams and hacked accounts out without staff lifting a finger.
Staff (and anyone who can manage messages) are never touched by the automatic parts.
Settings: `guard:` in config/bot.yaml.

Spam        floods, repeated messages and fake links (lookalike Discord/Roblox/Steam
            sites) are deleted and the member is timed out. Hits in 24 h escalate:
            10 min -> 1 h -> 24 h.
Hacked      the same message in several channels, or a scam/@everyone message with a
            link = a hacked account: everything it posted is deleted, 24 h timeout, a DM
            on how to secure the account, and a card with Remove timeout / Ban.
Pings       members pinging senior staff (who aren't in the conversation) get a short
            nudge to use support; 3 in 30 min = timeout.
AutoMod     3 messages blocked by Discord's AutoMod in 10 min = timeout.
Raids       a join wave (or many brand-new accounts) pauses invites and DMs (Discord's own
            pause, it lifts by itself), pings staff, and offers End lockdown / Kick the
            unverified accounts from the wave. /lockdown does the same by hand.
Nukes       someone deleting channels/roles/webhooks or banning in bulk (a hacked staff
            account or a bad bot) loses their dangerous roles at once; bots are kicked.
Setup       on start: Discord's own raid/DM-spam alerts go to #alerts, and missing
            permissions or roles above the bot's (it can't protect against those) are reported.
Status      a pinned card in #alerts: all clear, or what's going on. Edited only when it changes.
"""
from __future__ import annotations

import datetime as dt
import re
import time
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .. import cards, db, timeline, ui
from ..core import (ALERT_HOURS, cfg, channel, guarded, is_staff, log, log_card, log_channel, log_staff, role,
                    role_named)

A = discord.AuditLogAction
DANGER = ("administrator", "manage_guild", "manage_roles", "manage_channels", "manage_webhooks",
          "ban_members", "kick_members", "mention_everyone")
NEEDED = ("view_audit_log", "manage_guild", "manage_roles", "manage_messages", "moderate_members",
          "kick_members", "ban_members", "manage_webhooks")
DESTROY = {A.channel_delete, A.role_delete, A.webhook_create, A.emoji_delete, A.sticker_delete}
REMOVE = {A.ban, A.kick, A.member_prune}
ESCALATE = (None, 60, 1440)   # minutes for the 2nd and 3rd hit in 24 h (1st = spam.timeout_minutes)
HANDLE_SECONDS = 60           # one card per burst: more hits from the same member this soon are only deleted

# ---------- links ----------
OFFICIAL = ("discord.com", "discord.gg", "discord.gift", "discordapp.com", "discordapp.net", "discord.media",
            "discord.new", "discord.dev", "discordstatus.com", "dis.gd", "roblox.com", "ro.blox.com", "rbxcdn.com",
            "robloxlabs.com", "blox.link", "steampowered.com", "steamcommunity.com", "steamstatic.com",
            "fandom.com", "github.com", "github.io")   # last three: wikis and docs with a brand in the name
BRANDS = ("discord", "roblox", "robux", "steamcommun", "steampower")
NEAR = ("discord", "roblox", "steamcommunity", "steampowered")
URL = re.compile(r"https?://([^\s/<>?#]+)", re.I)
SCAM_TEXT = re.compile(r"free\s*(nitro|robux)|nitro\s*(for\s*)?free|(steam|nitro)\s*gift|gift\s*(card|nitro)|"
                       r"\b(i'?m|im|i am)\s*(leaving|quitting).{0,60}(giv|gift)|airdrop|claim\s*(your|it|now)|"
                       r"(onlyfans|nudes?|18\+)", re.I)
EVERYONE = re.compile(r"@(everyone|here)\b")
LEET = str.maketrans({"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "$": "s", "|": "l"})


def host_of(raw: str) -> str:
    h = raw.lower().split("@")[-1].split(":")[0].strip(".")
    return h[4:] if h.startswith("www.") else h


def official(host: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in OFFICIAL)


def near(a: str, b: str) -> bool:
    """One typo apart (missing, extra, swapped or wrong letter)."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]]
                                  and a[diff[1]] == b[diff[0]])
    short, long_ = sorted((a, b), key=len)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def fake_host(host: str) -> bool:
    """A site pretending to be Discord, Roblox or Steam: dlscord-gift.com, rob1ox.com.ge..."""
    if official(host):
        return False
    flat = host.replace("-", "").replace(".", "").replace("rn", "m").translate(LEET)
    variants = {flat, flat.replace("l", "i"), flat.replace("i", "l")}
    if any(b in v for v in variants for b in BRANDS):
        return True
    labels = re.split(r"[.\-]", host.translate(LEET))
    return any(near(lab, n) for lab in labels if len(lab) >= 5 for n in NEAR)


def scam_reason(text: str, can_ping_everyone: bool = False) -> tuple[str, bool] | None:
    """(reason, looks hacked) for a message, or None when it's fine."""
    hosts = [host_of(h) for h in URL.findall(text or "")]
    if not hosts:
        return None
    outside = any(not official(h) for h in hosts)   # scams link off-site; a roblox.com game link is just hype
    if not can_ping_everyone and EVERYONE.search(text) and outside:
        return "@everyone with a link", True
    if SCAM_TEXT.search(text):
        return "scam message", outside
    bad = next((h for h in hosts if fake_host(h)), None)
    return (f"fake link ({bad})", False) if bad else None


def signature(m: discord.Message) -> str:
    """What makes two messages 'the same': the text, or the same files (name + size)."""
    text = re.sub(r"\s+", " ", (m.content or "").lower()).strip()
    files = ",".join(sorted(f"{a.filename}:{a.size}" for a in m.attachments))
    return f"{text}|{files}" if len(text) >= 6 or files else ""


def gs() -> dict:
    g = cfg().get("guard") or {}
    return {
        "alert_roles": g.get("alert_roles", ["Admin", "Server Moderator"]),
        "protect_pings": g.get("protect_pings", []),
        "raid": {"joins": 10, "seconds": 60, "new_joins": 6, "new_days": 7, "new_seconds": 300,
                 "lockdown_minutes": 30, **(g.get("raid") or {})},
        "spam": {"messages": 6, "seconds": 6, "repeats": 3, "repeat_seconds": 60, "channels": 3, "mentions": 8,
                 "timeout_minutes": 10, **(g.get("spam") or {})},
        "nuke": {"deletes": 3, "removals": 5, "seconds": 60, **(g.get("nuke") or {})},
    }


def exempt(m: discord.abc.User) -> bool:
    return not isinstance(m, discord.Member) or m.bot or is_staff(m) or m.guild_permissions.manage_messages


def alert_ping(guild: discord.Guild) -> tuple[str, discord.AllowedMentions]:
    roles = [r for r in (role_named(guild, n) for n in gs()["alert_roles"]) if r]
    return " ".join(r.mention for r in roles), discord.AllowedMentions(roles=roles)


def status_parts(lockdown: dict | None, alerts: list, setup: list, now: float) -> tuple[str, str, int]:
    """(headline, body, accent) for the live status card in #alerts."""
    recent = [a for a in alerts if now - a[0] < ALERT_HOURS * 3600]
    lines = [f"<t:{int(t)}:R> {title}" for t, title in recent[-5:][::-1]]
    if lockdown:
        head, accent = "Lockdown on", ui.BAD
        lines.insert(0, f"Lifts <t:{int(lockdown['until'])}:R> · {lockdown.get('reason', '')}")
    elif recent:
        head, accent = f"{len(recent)} alert{'' if len(recent) == 1 else 's'} in the last {ALERT_HOURS} h", ui.WAIT
    else:
        head, accent = "All clear", ui.SLATE
        lines.append(f"No alerts in the last {ALERT_HOURS} h.")
    if setup:
        lines += ["", "**Setup to fix**"] + [f"• {p}" for p in setup]
    return head, "\n".join(lines), accent


# ---------- buttons on security cards (work after restarts) ----------
class MemberAction(discord.ui.DynamicItem[discord.ui.Button], template=r"asbot:guard:(?P<act>untime|ban):(?P<uid>\d+)"):
    def __init__(self, act: str, uid: int):
        label, style = ("Remove timeout", discord.ButtonStyle.secondary) if act == "untime" else \
            ("Ban", discord.ButtonStyle.danger)
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=f"asbot:guard:{act}:{uid}"))
        self.act, self.uid = act, uid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["act"], int(match["uid"]))

    async def callback(self, interaction: discord.Interaction):
        perms = interaction.user.guild_permissions
        if not (perms.ban_members if self.act == "ban" else perms.moderate_members):
            return await interaction.response.send_message("You can't do that.", ephemeral=True)
        g = interaction.guild
        try:
            if self.act == "ban":
                await g.ban(discord.Object(self.uid), delete_message_seconds=86400,
                            reason=f"Security card: banned by {interaction.user}")
                done = "Banned"
            else:
                m = g.get_member(self.uid) or await g.fetch_member(self.uid)
                await m.timeout(None, reason=f"Security card: timeout removed by {interaction.user}")
                done = "Timeout removed"
        except discord.HTTPException as e:
            return await interaction.response.send_message(f"Couldn't: {e.text or e}", ephemeral=True)
        await interaction.response.send_message(f"{done}.", ephemeral=True)
        await log_staff(g, "", kind="Moderation", embed=log_card(
            done, f"<@{self.uid}> from a security card", tone="bad" if self.act == "ban" else "good", by=interaction.user))


class LockdownAction(discord.ui.DynamicItem[discord.ui.Button], template=r"asbot:guard:(?P<act>end|kickraid)"):
    def __init__(self, act: str, count: int = 0):
        label = "End lockdown" if act == "end" else f"Kick unverified joins ({count})" if count else "Kick unverified joins"
        style = discord.ButtonStyle.success if act == "end" else discord.ButtonStyle.danger
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=f"asbot:guard:{act}"))
        self.act = act

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["act"])

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.kick_members:
            return await interaction.response.send_message("You can't do that.", ephemeral=True)
        cog: Guard = interaction.client.get_cog("Guard")
        await interaction.response.defer(ephemeral=True)
        if self.act == "end":
            await cog.end_lockdown(interaction.guild, by=interaction.user)
            return await interaction.followup.send("Lockdown ended.", ephemeral=True)
        n = await cog.kick_raid(interaction.guild, by=interaction.user)
        await interaction.followup.send(f"Kicked {n} unverified account(s) from the join wave.", ephemeral=True)


class Guard(commands.Cog):
    lockdown = app_commands.Group(name="lockdown", description="Pause joins and DMs during a raid (staff)",
                                  default_permissions=discord.Permissions(kick_members=True))

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.history: dict[int, deque] = defaultdict(lambda: deque(maxlen=30))   # user -> (time, message, sig)
        self.hits: dict[int, deque] = defaultdict(deque)          # user -> auto-timeouts in 24 h
        self.pings: dict[int, deque] = defaultdict(deque)         # user -> staff pings in 30 min
        self.nudged: dict[int, float] = {}
        self.automod: dict[int, deque] = defaultdict(deque)
        self.joins: deque = deque()                               # (time, member id, new account)
        self.staff_seen: dict[tuple[int, int], float] = {}        # (channel, staff id) -> last message
        self.actions: dict[int, deque] = defaultdict(deque)       # actor -> (time, destroy|remove)
        self.contained: dict[int, float] = {}
        self.handling: dict[tuple[int, str], float] = {}           # (user, "punish"|"hacked") -> when it started
        self.checked = False
        self.tick.start()

    def cog_unload(self):
        self.tick.cancel()

    # ---------- setup check ----------
    @commands.Cog.listener()
    async def on_ready(self):
        if self.checked:
            return
        self.checked = True
        g = self.bot.get_guild(self.bot.guild_id)
        sec = log_channel(g, "Security") if g else None
        if not sec:
            return
        if g._safety_alerts_channel_id != sec.id:
            try:   # Discord's own raid / DM-spam alerts land next to ours
                await g.edit(safety_alerts_channel=sec, reason="Security alerts -> #alerts")
            except discord.HTTPException as e:
                log.warning("safety alerts channel: %s", e)
        me = g.me
        problems = [f"Missing permission: {p.replace('_', ' ')}" for p in NEEDED if not getattr(me.guild_permissions, p)]
        above = [r for r in g.roles if r >= me.top_role and not r.managed and not r.is_default()
                 and any(getattr(r.permissions, p) for p in DANGER)]
        if above:
            problems.append(f"{' '.join(r.mention for r in above)} {'is' if len(above) == 1 else 'are'} above my role: "
                            "if one of those accounts gets hacked I can't stop it. Drag my role above them.")
        if db.kv_get("guard:setup") != problems:
            db.kv_set("guard:setup", problems)
            if problems:
                await sec.send(embed=log_card("Security setup check", "\n".join(f"• {p}" for p in problems), tone="wait"))

    async def update_status(self, g: discord.Guild):
        """Edits only when the words change, so a quiet day makes no requests."""
        sec = log_channel(g, "Security")
        if not sec:
            return
        now = time.time()
        recent = [a for a in db.kv_get("alerts:recent", []) if now - a[0] < ALERT_HOURS * 3600]
        head, body, accent = status_parts(db.kv_get("guard:lockdown"), recent, db.kv_get("guard:setup") or [], now)
        row, shown = db.panel("alerts_status"), f"{sec.id}|{head}|{body}"
        if row and row["channel_id"] == sec.id and db.kv_get("alerts:shown") == shown:
            return
        msg = await cards.post_card("alerts_status", sec, cards.CardView(cards.container(cards.title(head, body), accent=accent)))
        db.kv_set("alerts:shown", shown)
        if msg and not msg.pinned:
            await msg.pin()
            async for m in sec.history(limit=5):   # drop the "pinned a message" notice under it
                if m.type == discord.MessageType.pins_add and m.author.id == g.me.id:
                    await m.delete()

    # ---------- messages ----------
    @commands.Cog.listener()
    async def on_message(self, m: discord.Message):
        if not m.guild or m.author.bot or m.webhook_id:
            return
        if exempt(m.author):
            self.staff_seen[(m.channel.id, m.author.id)] = time.time()
            return
        await self.check(m)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.guild and not exempt(after.author) and before.content != after.content:
            await self.check(after, edited=True)

    async def check(self, m: discord.Message, edited: bool = False):
        s, now = gs()["spam"], time.time()
        scam = scam_reason(m.content, m.channel.permissions_for(m.author).mention_everyone)
        if scam and scam[1]:
            return await self.hacked(m, scam[0], [m])
        if edited:
            if scam:
                await self.punish(m.author, scam[0], [m])
            return
        h, sig = self.history[m.author.id], signature(m)
        h.append((now, m, sig))
        same = [x for x in h if sig and x[2] == sig and now - x[0] <= 120]
        spread = len({x[1].channel.id for x in same})
        if spread >= s["channels"]:   # hacked accounts blast a link or image everywhere; text alone is cross-posting
            why, msgs = f"the same message in {spread} channels", [x[1] for x in same]
            if URL.search(m.content) or m.attachments:
                return await self.hacked(m, why, msgs)
            return await self.punish(m.author, why, msgs)
        if scam:
            return await self.punish(m.author, scam[0], [m])
        pinged = len({u.id for u in m.mentions if u.id != m.author.id} | {r.id for r in m.role_mentions})
        if pinged >= s["mentions"]:
            return await self.punish(m.author, f"mass ping ({pinged} people/roles)", [m])
        burst =[x[1] for x in h if now - x[0] <= s["seconds"]]
        if len(burst) >= s["messages"]:
            return await self.punish(m.author, f"{len(burst)} messages in {s['seconds']} s", burst)
        repeats = [x[1] for x in same if now - x[0] <= s["repeat_seconds"]]
        if len(repeats) >= s["repeats"]:
            return await self.punish(m.author, f"the same message {len(repeats)} times", repeats)
        await self.check_pings(m)

    async def remove(self, msgs: list[discord.Message]):
        by_channel: dict[int, list[discord.Message]] = defaultdict(list)
        for x in msgs:
            by_channel[x.channel.id].append(x)
        for batch in by_channel.values():
            try:
                if len(batch) == 1:
                    await batch[0].delete()
                else:
                    await batch[0].channel.delete_messages(batch)
            except discord.HTTPException:
                pass

    def busy(self, member: discord.Member, *kinds: str) -> bool:
        """Already timed out, or one of `kinds` is being handled for them: the caller only deletes.
        Otherwise marks kinds[0]. No await in here, so hits that land together can't each start one."""
        now = time.time()
        if (isinstance(member, discord.Member) and member.is_timed_out()) or \
                any(now - self.handling.get((member.id, k), 0) < HANDLE_SECONDS for k in kinds):
            return True
        self.handling[(member.id, kinds[0])] = now
        return False

    async def punish(self, member: discord.Member, reason: str, msgs: list[discord.Message]):
        if self.busy(member, "punish", "hacked"):
            return await self.remove(msgs)
        self.history.pop(member.id, None)   # before any await: messages arriving meanwhile start a fresh count
        now = time.time()
        hits = self.hits[member.id]
        while hits and now - hits[0] > 86400:
            hits.popleft()
        hits.append(now)
        n = len(hits)
        minutes = gs()["spam"]["timeout_minutes"] if n == 1 else ESCALATE[min(n, 3) - 1]
        await self.remove(msgs)
        note = ""
        try:
            await member.timeout(dt.timedelta(minutes=minutes), reason=f"Auto: {reason}")
        except discord.HTTPException:
            note = " (couldn't time out: their role is above mine)"
        try:
            await member.send(embed=log_card(f"You've been timed out for {minutes} min",
                                             f"In **{member.guild.name}**: {reason}. Please slow down and keep to the rules.",
                                             tone="wait"))
        except discord.HTTPException:
            pass
        where = f" in {msgs[0].channel.mention}" if msgs else ""
        evidence = f"\n> {(msgs[0].content or '(files)')[:300]}" if msgs else ""
        timeline.add(member.id, "auto", f"Auto-timeout {minutes} min: {reason}")
        await log_staff(member.guild, "", kind="Moderation", embed=log_card(
            "Auto-timeout", f"{member.mention}{where} · {reason}{evidence}\n"
                            f"Timed out **{minutes} min**{note} · hit {n} in 24 h" + (" · consider a ban" if n >= 3 else ""),
            tone="wait", user=member))

    async def hacked(self, m: discord.Message, reason: str, msgs: list[discord.Message]):
        member = m.author
        if self.busy(member, "hacked"):   # the same scam in 8 channels = one alert, not 8
            return await self.remove(msgs)
        self.history.pop(member.id, None)
        await self.remove(msgs)
        note = ""
        try:
            await member.timeout(dt.timedelta(hours=24), reason=f"Auto: looks hacked ({reason})")
        except discord.HTTPException:
            note = "\nCouldn't time them out: their role is above mine."
        sup = channel(member.guild, "support")
        try:
            await member.send(embed=log_card(
                "Your account may be hacked",
                f"Your account just posted {reason} in **{member.guild.name}**, so you've been timed out for 24 h "
                "to stop it spreading.\n\n1. Change your Discord password (this logs out every device)\n"
                "2. Turn on two-factor authentication\n3. Check Settings → Authorized Apps and remove anything you don't know\n\n"
                "Once it's safe, open a ticket in " + (sup.mention if sup else "#support") +
                " and staff will lift the timeout early.", tone="bad"))
        except discord.HTTPException:
            note += "\nCouldn't DM them."
        text, mentions = alert_ping(member.guild)
        v = discord.ui.View(timeout=None)
        v.add_item(MemberAction("untime", member.id))
        v.add_item(MemberAction("ban", member.id))
        sample = (msgs[0].content or "(files only)")[:500]
        timeline.add(member.id, "auto", f"Looked hacked, timed out 24 h: {reason}")
        await log_staff(member.guild, text, kind="Security", allowed_mentions=mentions, view=v, embed=log_card(
            "Hacked account or scammer", f"{member.mention} · {reason}\n> {sample}\n"
                                         f"Deleted **{len(msgs)}** message(s), timed out **24 h**, sent them how to secure it.{note}",
            tone="bad", user=member))

    async def check_pings(self, m: discord.Message):
        names = set(gs()["protect_pings"])
        if not names or not (m.mentions or m.role_mentions):
            return
        reply_to = m.reference.resolved.author.id if m.reference and isinstance(m.reference.resolved, discord.Message) else None
        explicit = {int(x) for x in re.findall(r"<@!?(\d+)>", m.content)}
        now = time.time()

        def protected(mem) -> bool:
            return isinstance(mem, discord.Member) and any(r.name in names for r in mem.roles) \
                and mem.id != reply_to and now - self.staff_seen.get((m.channel.id, mem.id), 0) > 600

        hit = any(protected(u) for u in m.mentions if u.id in explicit) or any(r.name in names for r in m.role_mentions)
        if not hit:
            return
        dq = self.pings[m.author.id]
        while dq and now - dq[0] > 1800:
            dq.popleft()
        dq.append(now)
        if len(dq) >= 3:
            dq.clear()
            return await self.punish(m.author, "kept pinging staff", [])
        if now - self.nudged.get(m.author.id, 0) > 600:
            self.nudged[m.author.id] = now
            sup = channel(m.guild, "support")
            try:
                await m.reply(f"Please don't ping staff. For help, open a ticket in {sup.mention if sup else '#support'}.",
                              delete_after=20, mention_author=False)
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_automod_action(self, ex: discord.AutoModAction):
        if ex.action.type != discord.AutoModRuleActionType.block_message:
            return
        g = self.bot.get_guild(ex.guild_id)
        member = ex.member or (g.get_member(ex.user_id) if g else None)
        if not member or exempt(member):
            return
        now, dq = time.time(), self.automod[member.id]
        while dq and now - dq[0] > 600:
            dq.popleft()
        dq.append(now)
        if len(dq) >= 3:
            dq.clear()
            await self.punish(member, "3 messages blocked by AutoMod in 10 min", [])

    # ---------- raids ----------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        r, now, g = gs()["raid"], time.time(), member.guild
        new = (discord.utils.utcnow() - member.created_at).days < r["new_days"]
        self.joins.append((now, member.id, new))
        while self.joins and now - self.joins[0][0] > max(r["seconds"], r["new_seconds"]):
            self.joins.popleft()
        state = db.kv_get("guard:lockdown")
        if state:
            state["joined"] = list(dict.fromkeys(state.get("joined", []) + [member.id]))
            return db.kv_set("guard:lockdown", state)
        recent = [j for j in self.joins if now - j[0] <= r["seconds"]]
        fresh = [j for j in self.joins if j[2] and now - j[0] <= r["new_seconds"]]
        if len(recent) >= r["joins"] or len(fresh) >= r["new_joins"]:
            await self.start_lockdown(g, r["lockdown_minutes"],
                                      f"Join wave: **{len(recent)}** joins in {r['seconds']} s, **{len(fresh)}** accounts "
                                      f"under {r['new_days']} days old in {r['new_seconds'] // 60} min",
                                      joined=[j[1] for j in self.joins])

    async def start_lockdown(self, g: discord.Guild, minutes: int, reason: str, by=None, joined=()) -> bool:
        """False if one is already on. The state is saved before the first await, so joins that land
        while Discord is being told see it and are added to this lockdown instead of starting another."""
        if db.kv_get("guard:lockdown"):
            return False
        until = discord.utils.utcnow() + dt.timedelta(minutes=min(minutes, 1440))
        state = {"until": until.timestamp(), "reason": reason, "joined": list(joined), "by": getattr(by, "id", None)}
        db.kv_set("guard:lockdown", state)
        note = ""
        try:
            await g.edit(invites_disabled_until=until, dms_disabled_until=until, reason=f"Lockdown: {reason[:400]}")
        except discord.HTTPException as e:
            note = f"\nCouldn't pause invites/DMs: {e.text or e}"
        text, mentions = alert_ping(g)
        v = discord.ui.View(timeout=None)
        v.add_item(LockdownAction("end"))
        v.add_item(LockdownAction("kickraid", len(joined)))
        await log_staff(g, text, kind="Security", allowed_mentions=mentions, view=v, embed=log_card(
            "Lockdown on", f"{reason}\n\nNew joins and DMs between members are paused until "
                           f"<t:{int(until.timestamp())}:t> (<t:{int(until.timestamp())}:R>), it lifts by itself.{note}",
            tone="bad", by=by))
        return True

    async def end_lockdown(self, g: discord.Guild, by=None, timer: bool = False):
        state = db.kv_get("guard:lockdown")
        db.run("DELETE FROM kv WHERE key='guard:lockdown'")
        if not timer:
            try:
                await g.edit(invites_disabled_until=None, dms_disabled_until=None, reason="Lockdown ended")
            except discord.HTTPException as e:
                log.warning("end lockdown: %s", e)
        joined = len((state or {}).get("joined", []))
        await log_staff(g, "", kind="Security", embed=log_card(
            "Lockdown ended", f"{'Timer ran out. ' if timer else ''}Invites and DMs are back on. "
                              f"{joined} account(s) joined during the wave.", tone="good", by=by))

    async def kick_raid(self, g: discord.Guild, by) -> int:
        state = db.kv_get("guard:lockdown") or db.kv_get("guard:last_raid") or {}
        verified = role(g, "verified")
        n = 0
        for uid in state.get("joined", []):
            m = g.get_member(uid)
            if m and not m.bot and (verified is None or verified not in m.roles) and not exempt(m):
                try:
                    await m.kick(reason=f"Raid clean-up by {by}")
                    n += 1
                except discord.HTTPException:
                    pass
        await log_staff(g, "", kind="Security", embed=log_card(
            "Raid clean-up", f"Kicked **{n}** unverified account(s) that joined during the wave.", tone="bad", by=by))
        return n

    @tasks.loop(minutes=1)
    @guarded
    async def tick(self):
        state = db.kv_get("guard:lockdown")
        g = self.bot.get_guild(self.bot.guild_id)
        if state and g and time.time() >= state["until"]:
            db.kv_set("guard:last_raid", state)   # "Kick unverified joins" still works after the timer
            await self.end_lockdown(g, timer=True)
        now = time.time()
        for d in (self.history, self.hits, self.pings, self.automod, self.actions):
            for k in [k for k, q in d.items() if not q or now - (q[-1][0] if isinstance(q[-1], tuple) else q[-1]) > 86400]:
                d.pop(k, None)
        self.staff_seen = {k: t for k, t in self.staff_seen.items() if now - t < 3600}
        self.handling = {k: t for k, t in self.handling.items() if now - t < HANDLE_SECONDS}
        if g:
            try:
                await self.update_status(g)
            except discord.HTTPException as e:
                log.warning("alerts status card: %s", e)

    @tick.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @lockdown.command(name="start", description="Pause new joins and member DMs")
    @app_commands.describe(minutes="How long (it lifts by itself)", reason="Shown in #alerts")
    async def lockdown_start(self, interaction: discord.Interaction, minutes: app_commands.Range[int, 5, 1440] = 60,
                             reason: str = "Started by staff"):
        if not interaction.user.guild_permissions.kick_members:
            return await interaction.response.send_message("You can't do that.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        if not await self.start_lockdown(interaction.guild, minutes, reason, by=interaction.user):
            return await interaction.followup.send("A lockdown is already on. `/lockdown end` first to start a new "
                                                   "one.", ephemeral=True)
        await interaction.followup.send(f"Lockdown on for {minutes} min. Details in #alerts.", ephemeral=True)

    @lockdown.command(name="end", description="Let people join and DM again")
    async def lockdown_end(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.kick_members:
            return await interaction.response.send_message("You can't do that.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await self.end_lockdown(interaction.guild, by=interaction.user)
        await interaction.followup.send("Lockdown ended.", ephemeral=True)

    # ---------- nukes ----------
    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        g, uid = entry.guild, entry.user_id
        kind = "destroy" if entry.action in DESTROY else "remove" if entry.action in REMOVE else None
        if not kind or uid in (None, self.bot.user.id, g.owner_id):
            return
        n_cfg, now = gs()["nuke"], time.time()
        dq = self.actions[uid]
        dq.append((now, kind))
        while dq and now - dq[0][0] > n_cfg["seconds"]:
            dq.popleft()
        n = sum(1 for _, k in dq if k == kind)
        if n >= (n_cfg["deletes"] if kind == "destroy" else n_cfg["removals"]) and now - self.contained.get(uid, 0) > 600:
            self.contained[uid] = now
            what = "channels/roles/webhooks deleted or made" if kind == "destroy" else "members banned or kicked"
            await self.contain(g, uid, f"**{n}** {what} in {n_cfg['seconds']} s")

    async def contain(self, g: discord.Guild, uid: int, why: str):
        m = g.get_member(uid)
        done = []
        if m is None:
            done.append("They're not in the server any more.")
        elif m.bot:
            try:
                await m.kick(reason=f"Anti-nuke: {why}")
                done.append("Bot kicked.")
            except discord.HTTPException:
                done.append("Couldn't kick the bot (its role is above mine).")
        else:
            strip = [r for r in m.roles if not r.is_default() and not r.managed and r < g.me.top_role
                     and any(getattr(r.permissions, p) for p in DANGER)]
            if strip:
                try:
                    await m.remove_roles(*strip, reason=f"Anti-nuke: {why}")
                    done.append("Removed " + " ".join(r.mention for r in strip))
                except discord.HTTPException:
                    done.append("Couldn't remove their roles.")
            try:
                await m.timeout(dt.timedelta(hours=24), reason=f"Anti-nuke: {why}")
                done.append("Timed out 24 h.")
            except discord.HTTPException:
                pass
            if m.top_role >= g.me.top_role or m.guild_permissions.administrator:
                done.append("**Their top role is above mine or they're an admin, so I can't fully stop them.**")
        text, mentions = alert_ping(g)
        body = f"<@{uid}> · {why}\n" + "\n".join(done)
        await log_staff(g, text, kind="Security", allowed_mentions=mentions, embed=log_card(
            "Anti-nuke stopped someone", body, tone="bad", user=m))
        owner = g.owner or self.bot.get_user(g.owner_id)
        if owner:
            try:
                await owner.send(embed=log_card(f"Anti-nuke in {g.name}", body, tone="bad"))
            except discord.HTTPException:
                pass


async def setup(bot):
    bot.add_dynamic_items(MemberAction, LockdownAction)
    await bot.add_cog(Guard(bot))
