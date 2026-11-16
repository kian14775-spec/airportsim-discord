# HANDOFF history (old RESUME blocks, verbatim)

## RESUME HERE (2026-11-05, s23)
s23b ROLES APPLIED LIVE: tools/roles_style.py = order + gradients (backup backups/roles_2026-11-05/roles.json, git-ignored). Founder, Admin, Support Team now shown apart. Layout planner no longer touches role colour/hoist, so an apply won't flatten gradients. `*` role is above the bot, untouched (#607D8B). Re-made roles still there: ARFF Commissioner, Leg, Unverified.
s23b INCIDENT RADAR: LIVE (deploy c83c0053, 2026-11-05, selftest 50/50). asbot/cogs/radar.py, settings `radar:` in bot.yaml. Buttons untested in Discord until the first real alert; watch for false alarms and tune min_members.
s23 NAMES FIX: LIVE (deploy 5650897b, 2026-11-05, selftest 50/50 via PC with a temp ASBOT_DATA + seed/state; railway ssh needs a key, none set up). Embed <@id> only renders if the viewer's app knows the user, so people who left showed raw ids + Discord's no-access box. asbot/names.py (names table, seeded from members on_ready, kept on join/leave, fetch_user for older ids) rewrites <@id> in everything through log_staff; ticket/stats cards call names.who() directly. Join/leave = avatar + name + id cards, 'stayed 59 min' (the old 'joined 7 hours ago' drifted). After deploy CHECK in Discord: profile links open for members who are here; old cards stay as they were. tests/test_logs.py 13/13.

State: LIVE 24/7 on RAILWAY (free plan, $1/mo credit; bot uses ~$0.5-0.85). Project "exquisite-reprieve",
service "my-bot", volume "my-bot-volume" at /data (54/500 MB). Repo folder is `railway link`ed. NO copy runs on the PC.
RULE: nothing may cost money / eat the Railway credit, no paid AI. Deploys + live server deletes need Kian's OK
(auto-mode blocks them otherwise). Railway uploads sometimes fail with no build: just `railway up` again.
s22 LOGS 7 -> 3 LIVE (deploy 5a767308, selftest 50/50): #alerts (log_security, pinned live status card) / #actions (log_mod) /
#activity (log_messages). Keys kept, so ids + history stayed. Muted events: `logs: muted:` in config/bot.yaml (NOT `off:`, YAML
reads that as false). Member timeline in /staff lookup (asbot/timeline.py), filling from 2026-11-01 19:38 UTC; not yet looked at in
Discord. 4 old channels deleted; backup backups/logs_merge_2026-11-01/ (git-ignored; 115 msgs; image links may expire).
s21 LIVE (deploy 20f2e318, 2026-10-31, selftest 54/54): LOGS category (security/mod/member/message/server/voice/
ticket-logs, staff read-only, keys log_*), core.log_staff -> core.LOG_CHANNELS + log_card(); modlog.py (msglog
7-day store, image copies in DATA/logcache, who-deleted, purge .txt, ghost pings, invite used, profile/boost, voice),
auditlog.py (every audit entry + actor + reason; own-bot skipped except layout), guard.py (spam/fake links/mass ping/
hacked accounts/ping nudge/AutoMod escalation/raid lockdown + /lockdown/anti-nuke/setup check). Safety alerts ->
security-logs. AutoMod tuned via tools/automod_setup.py (mention-spam rule refuses API edits: 404).
Old #logs DELETED (backup: backups/cleanup_2026-10-31/, 23.6k msgs). /help fix: Traveler had Use Application
Commands DENIED in #general/#media (hand-made booster-perk overwrites) -> allowed; /help card buttons no longer expire.
Next:
1. Kian OK -> delete the 15 old Ticket Tool channels in ARCHIVE (ids in backups/cleanup_2026-10-31/*.json names;
   already backed up) + the then-empty ARCHIVE category. Pattern delete was blocked: delete by explicit ids.
2. Kian decides the gaps in docs/2026-10-31_logs_security_ideas.md: Trial Mod has ban (Server Mod doesn't), `*` role
   above the bot (setup check card says so), 37 member invites, webhook in #announcements, re-made roles (ARFF
   Commissioner, Leg, Unverified - Unverified is used by verify.py, keep), layout drift (keep or revert).
3. Kian picks new systems (top: live player count, update radar, Frequent Flyer trust tiers, verify nudge, shot of the week).
4. Before 2026-12-01: `railway config migrate`. Open Cloud key still pending (perks, ban sync).
NOTE: keep_manual_edits only protects names/topics. A full `layout apply` would REVERT the hand-made perms on
#general/#media (booster perks), #dev-logs (a user block) and the Livery role colour - ask first or encode them.
