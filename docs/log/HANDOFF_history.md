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

## (archived) RESUME (2026-11-16, s24)
s24 = Kian's 3 asks: banners redo, code cleanup, restructure plan. ALL BUILT + COMMITTED (90bed2b), NOT DEPLOYED.
- BANNERS v2 (Kian: "only use the aircraft hero shots you made", "completely different", "peak"): tools/make_banners.py
  (PC only, `python -I tools/make_banners.py [name|server]`). Source = Claude Coding/GFX - AS2 Role Renders/Exterior_Aircraft_Shots
  (Final_9 v2 No_Logo + Heroes Final_post). Style: full-bleed hero shot, giant Bahnschrift Bold title BEHIND the jet (sky-distance
  matte), tracked captions bottom corners ("AIRPORT SIMULATOR" / per-channel), post = sun+rays, temp zoning, jet local contrast,
  bloom, grade, vignette, fringe, grain. 1500x500. Map: welcome=Ryanair touchdown, rules=KLM head-on, support=Southwest,
  help=United climb, stats=Delta touchdown, liveries=American climb, contest=Turkish climb, inbox=Delta climb.
  assets/server/ = server_banner 960x540 (needs boost lvl 2) + invite_splash (lvl 1), Ryanair aerial, upload by hand.
  Copies + sheet: Claude Coding/Discord Server Revamp/Banners v2 (2026-11-16)/. cards.py swaps attachments on deploy (hash).
  NOTE: the shots were made for the AS2 role-renders commission; Kian chose to reuse them here.
- CODE: audit docs/2026-11-16_audit/ (core_cogs.md, layout_tools_structure.md) + fix logs FIXES_cogs.md / FIXES_layout.md.
  Fixed C1 (loops die silently -> core.guarded + watchdog restart), H1 ticket rollback, H2 disk budget, H3-H5 guard races,
  H6 defer-first, M2/M4/M5/M6/M7/M15/M21; layout C1 (apply keeps perms for roles/members the yaml doesn't name), H1 (manual
  edits kept every apply), H2 (seed no longer overwrites volume state), H3 (backup + readable preview), ids cache,
  `external: true` roles + `perms_extra:`; dead tools + force file removed. 56/56 offline tests (tests/*.py run as scripts).
- PLAN: docs/2026-11-16_restructure_plan.md (+ copy in Claude Coding/Discord Server Revamp/). Proposal only: hybrid depts
  (Mod + PR in main server STAFF rooms, QA keeps its hub, archive dead staff hub), Staff Duty points system, Discord-native
  board (not Trello: 10-collaborator free cap; not a website: Railway RAM cost), activity + boost ideas, 7 new systems.
State: LIVE 24/7 on RAILWAY (free plan, $1/mo credit; bot uses ~$0.5-0.85). Project "exquisite-reprieve", service "my-bot",
volume "my-bot-volume" at /data. Repo folder is `railway link`ed. NO copy runs on the PC. Live = s23b code (c83c0053).
RULE: nothing may cost money / eat the Railway credit, no paid AI. Deploys + live server deletes need Kian's OK.
Next:
1. Kian OK -> `railway up --detach` (retry if the upload times out), then `python tools/selftest.py`, check in Discord that the
   new banners swapped in (rules-and-verify, support, help, stats, liveries, contest, inbox) and #alerts has no loop errors.
   Then `/layout preview` BEFORE any `/layout apply` (volume applied_* is the stale 10-26 copy; expect no perms lines on
   #general/#media/#dev-logs unless @everyone/Traveler/staff really differ).
2. Kian picks from the plan's pick list (top: staff baseline report -> departments -> Staff Duty v1 -> verify nudge + daily pulse).
3. Old open items: delete 15 old Ticket Tool channels in ARCHIVE (ids in backups/cleanup_2026-10-31/), gaps in
   docs/2026-10-31_logs_security_ideas.md (Trial Mod ban perm, `*` role above bot, 37 invites, webhook), `railway config
   migrate` before 2026-12-01, Open Cloud key (perks, ban sync). Audit Medium/Cleanup items left: M3, M8-M14, M16-M18,
   M20 (application row before post, same shape as H1), M22-M24, template_add disk check.
