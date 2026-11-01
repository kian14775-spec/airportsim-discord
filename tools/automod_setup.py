"""Tune Discord's own AutoMod (it keeps working even when the bot is down).

    python tools/automod_setup.py          show what would change
    python tools/automod_setup.py apply    change it

- every rule's alerts go to #actions (they used to land in #staff-chat)
- custom words: fixes an entry that had a pasted @mention glued to it
- mention spam: 20 -> 8 mentions per message, plus a 10 min timeout and an alert
- profile names: drops the one-word entries ('in', 'my', 'Look', 'bio') that matched almost
  everyone (why it was switched off), then switches it on
- adds "Server invites" (except in rp-servers, where airlines share theirs) and
  "Scam links" (free nitro/robux, @everyone + link) rules
Staff roles are exempt from the two new rules. Safe to run again.
"""
import asyncio
import datetime as dt
import os
import sys
from pathlib import Path

import discord
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from asbot.core import cfg  # noqa: E402
from asbot.layout import sync  # noqa: E402

T = discord.AutoModRuleTriggerType
ACT = discord.AutoModRuleActionType
OWN_INVITES = ["discord.gg/x94F9TfFd4", "discord.gg/AzTRXmmjFB"]   # the server's own (most used) invites
INVITES = r"(?i)(discord\.gg|discord(app)?\.com/invite|dsc\.gg)/[a-z0-9-]+"
SCAMS = [r"(?i)free\s*(nitro|robux).{0,40}(https?://|www\.)",   # Discord caps regex size: keep the gaps short
         r"(?i)(https?://|www\.).{0,60}free\s*(nitro|robux)",
         r"(?i)(steam|nitro)\s*gift.{0,40}https?://",
         r"(?i)@(everyone|here).{0,60}https?://"]
PROFILE_DROP = {"*look*", "*in*", "*my*", "*bio*"}


def alert(ch_id: int) -> discord.AutoModRuleAction:
    return discord.AutoModRuleAction(type=ACT.send_alert_message, channel_id=ch_id)


async def main(apply: bool):
    load_dotenv(ROOT / ".env")
    c = discord.Client(intents=discord.Intents.none())
    await c.login(os.environ["DISCORD_TOKEN"])
    g = await c.fetch_guild(int(os.environ["GUILD_ID"]))
    state = sync.load_state(g.id)
    modlogs, rp = state.get("ch:log_mod"), state.get("ch:rp_servers")
    if not modlogs:
        sys.exit("No #actions id in state/: create the LOGS channels first.")
    roles = {r.name: r for r in await g.fetch_roles()}
    staff = [roles[n] for n in cfg().get("staff_role_names", []) if n in roles]
    rules = {r.name: r for r in await g.fetch_automod_rules()}
    plan = []

    for r in rules.values():
        acts = [a for a in r.actions if a.type != ACT.send_alert_message]
        new = None
        if r.trigger.type == T.mention_spam:
            trig = discord.AutoModTrigger(type=T.mention_spam, mention_limit=8, mention_raid_protection=True)
            acts = [discord.AutoModRuleAction(type=ACT.block_message),
                    discord.AutoModRuleAction(duration=dt.timedelta(minutes=10)), alert(modlogs)]
            new = {"trigger": trig, "actions": acts}
            plan.append(f"{r.name}: limit {r.trigger.mention_limit} -> 8, + 10 min timeout, alerts -> #actions")
        elif r.trigger.type == T.keyword:
            words = [("beaner" if "beaner" in w and "<@" in w else w) for w in r.trigger.keyword_filter]
            new = {"actions": acts + [alert(modlogs)]}
            if words != r.trigger.keyword_filter:
                new["trigger"] = discord.AutoModTrigger(type=T.keyword, keyword_filter=words,
                                                        regex_patterns=r.trigger.regex_patterns,
                                                        allow_list=r.trigger.allow_list)
                plan.append(f"{r.name}: fixed the entry with a pasted @mention")
            plan.append(f"{r.name}: alerts -> #actions")
        elif r.trigger.type == T.member_profile:
            words = [("*beaner*" if "beaner" in w else w) for w in r.trigger.keyword_filter
                     if w.lower() not in PROFILE_DROP]
            # short *word* entries match inside normal names ('*Abo*' hits "Abood", "about"): whole word only
            words = [w.strip("*") if w.startswith("*") and w.endswith("*") and len(w.strip("*")) <= 4 else w
                     for w in words]
            new = {"trigger": discord.AutoModTrigger(type=T.member_profile, keyword_filter=words,
                                                     regex_patterns=r.trigger.regex_patterns,
                                                     allow_list=r.trigger.allow_list),
                   "enabled": True}
            plan.append(f"{r.name}: removed {sorted(PROFILE_DROP)} (matched almost every name), switched ON")
        elif any(a.type == ACT.send_alert_message for a in r.actions):
            new = {"actions": acts + [alert(modlogs)]}
            plan.append(f"{r.name}: alerts -> #actions")
        if new and apply:
            try:
                await r.edit(**new, reason="Bot security setup: alerts to #actions")
            except discord.HTTPException as e:   # "Block Mention Spam" (made by an old app in 2022) answers 404
                plan.append(f"{r.name}: Discord refused the edit ({e.status}), change it in Server Settings > AutoMod")

    for name, trig, msg, exempt_ch in (
            ("Server invites", discord.AutoModTrigger(type=T.keyword, regex_patterns=[INVITES], allow_list=OWN_INVITES),
             "Server invites aren't allowed here. Airlines can share theirs in rp-servers.", [rp] if rp else []),
            ("Scam links", discord.AutoModTrigger(type=T.keyword, regex_patterns=SCAMS),
             "That looks like a scam link, so it was blocked.", [])):
        if name in rules:
            plan.append(f"{name}: already there")
            continue
        plan.append(f"{name}: new rule (block + alert in #actions; staff exempt)")
        if apply:
            await g.create_automod_rule(
                name=name, event_type=discord.AutoModRuleEventType.message_send, trigger=trig, enabled=True,
                actions=[discord.AutoModRuleAction(custom_message=msg), alert(modlogs)],
                exempt_roles=staff, exempt_channels=[discord.Object(x) for x in exempt_ch],
                reason="Bot security setup")
    print(("APPLIED:\n" if apply else "Would change (run with 'apply'):\n") + "\n".join("- " + p for p in plan))
    await c.close()


if __name__ == "__main__":
    asyncio.run(main(len(sys.argv) > 1 and sys.argv[1] == "apply"))
