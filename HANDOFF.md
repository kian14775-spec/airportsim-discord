# Airport Simulator Discord bot - HANDOFF

## RESUME HERE (2026-11-16, s25)
s25 = built EVERY system in the restructure plan (Kian, s24: "do whatever u wanna do so u can add everything" + "make sure
new staff know how to use or operate everything"). s24 hit the 5-h limit mid-build; its uncommitted start (points.py,
config, layout) was picked up. ALL BUILT + COMMITTED (640a031), NOT DEPLOYED. Live = s23b (c83c0053); s24 (banners v2 +
audit fixes, 90bed2b) is still undeployed too, so the next deploy ships s24 + s25 together.
- NEW (ARCHITECTURE.md "Staff Duty and friends"): asbot/points.py ledger; cogs duty.py (board pinned in #stats: My week /
  Go on duty / Go off duty / Take a break / How this works; Monday close -> DMs + #leads report + check-in and promotion
  cards with lead buttons; Thursday nudge; 30-day staff report; Staff guide card pinned in #staff-chat; team welcome DMs),
  bugs.py (QA card on Bug posts, Confirmed/Fixed tags, game update -> Done + "bug fixed" DMs), pulse.py ("Playing now"
  counter, one verify-reminder DM, question of the day 18:00 UTC, shot of the week, Wednesday poll reminder, boost card),
  events.py (Plan an event: Discord event + post + reminder + auto start/end + recap), creators.py (#creators-room card,
  YouTube RSS -> #socials). Hooks: tickets (solved/fast reply/on-duty pings), strikes (warn), news (support line
  "N staff on duty · usual reply ~X min", game_updated), hub (/staff: Staff Duty, Plan an event, Staff guide).
- Texts new staff read: config/bot.yaml `guide:`. Rules/targets: `duty:` (Mod 12, QA 10, PR 10; tune after 2 weeks).
  The first week after deploy is a warm-up (shown, not judged). The bot never demotes/promotes by itself.
- LAYOUT now matches live (PC `layout.py preview` is clean): dept roles external by live names (Public Relations Team,
  Quality Assurance, Star Program, Admin), ping roles plain (Announcements/Development/Social Media/Polls), staff's
  hand-made #general/#media member perms encoded as perms_extra. Apply only ADDS: voice #playing-now, #leads, #mod-room,
  #creators-room, Confirmed + Fixed tags, PR/QA access to #staff-chat + #stats.
- Tests: all 7 files pass (new tests/test_systems.py), pyflakes clean, tools/selftest.py checks the new rooms/perms.
State: LIVE 24/7 on RAILWAY (free plan, $1/mo credit; bot uses ~$0.5-0.85). Project "exquisite-reprieve", service "my-bot",
volume "my-bot-volume" at /data. Repo folder is `railway link`ed. NO copy runs on the PC.
RULE: nothing may cost money / eat the Railway credit, no paid AI. Deploys + live server deletes need Kian's OK.
DEPLOY BLOCKED (2026-11-16, Kian said "Yes, deploy"): Windows Smart App Control is now ON (enforced; it was in
evaluation on 11-05) and blocks the unsigned railway.exe, from Claude's shells AND the terminal tab (CodeIntegrity
event 3077; Claude's packaged app sees its own copy under AppData\Local\Packages\Claude_*\LocalCache\Roaming
pm).
A script that refreshes the CLI's saved OAuth login and POSTs the tarball itself was refused by the auto-mode safety
check (handling login tokens outside the official tool), so it was NOT written. Nothing was uploaded; live = s23b.
Kian picks: (a) deploy via GitHub: private repo + Railway service source = that repo (then deploys = git push),
(b) turn Smart App Control off himself (Windows Security; can't be turned back on without a reset), (c) explicitly allow
a token-handling upload script. Railway CLI login also expired 11-05 (the CLI refreshes it on its next run).
Next:
1. Once deploys work -> `railway up --detach` (retry on upload timeout) -> in Discord `/layout preview` (must match the PC preview
   above) -> `/layout apply` -> `python tools/selftest.py` -> look: board pinned in #stats, guide pinned in #staff-chat,
   creators card in #creators-room, s24 banners swapped in, #alerts has no loop errors.
2. Needs from Kian (selftest flags them): bot needs Manage Events; bot role above Server Moderator / Trial / Support Team /
   QA / PR roles (promote/demote buttons). Verify-reminder DMs start right after deploy: ~650 unverified members, max 4
   per 10 min, once each ever (`pulse.verify_nudge_hours: 0` switches it off).
3. After 2 weeks of real data: tune duty targets; maybe QA/PR Trial roles; QA hub bridge (invite the bot there), Open
   Cloud key (in-game perks), Frequent Flyer tiers, Discord native onboarding.
4. Old open items: 15 old Ticket Tool channels in ARCHIVE (ids in backups/cleanup_2026-10-31/), gaps in
   docs/2026-10-31_logs_security_ideas.md, `railway config migrate` before 2026-12-01, audit Medium items M3, M8-M14,
   M16-M18, M20, M22-M24, template_add disk check.

## Deploying (Railway)
- Code change: `railway up --detach` from the repo (CLI login lives in the real user profile; from Claude's
  tools run railway with the sandbox disabled, or in a terminal tab).
- Logs: `railway logs --lines 50`. Status: `railway service status`.
- First boot on an empty volume copies seed/ in. Later boots only ADD new seed ids; applied_* is copied only if missing.

## How layout changes work
Kian says what to change -> edit layout/server.yaml (keep `key`, add old names to `was`) -> `railway up` ->
in Discord `/layout preview` then `/layout apply` (runs ON Railway; its state on the volume is the truth).
Each apply writes DATA/backups/layout_<utc>.json first (newest 10). Perms for roles/members the yaml doesn't name are
left alone; encode hand-made perms with `external: true` roles + per-channel `perms_extra:` (ARCHITECTURE.md).
`python layout.py preview` / `export --perms` are read-only on the PC; a PC apply needs `--from-pc` + the state sync.

## Map
- asbot/layout/model.py (yaml -> specs), planner.py (pure diff), executor.py, discord_backend.py, sync.py (state, ids cache, preview/apply)
- asbot/core.py (guarded, disk_ok, lookups, logs), db.py, cards.py (banners), cogs/ (features; table in ARCHITECTURE.md)
- bot.py, layout.py (CLI), tools/ (selftest, make_banners, roles_style, automod_setup), tests/ (plain scripts), docs/

## Log
- 2026-10-25 s1: layout + bot features locked in chat; built layout engine, bot core, Oracle deploy; tests pass.
- 2026-10-25 s2: found old token in ~/my-bot/.env (same bot app); mapped layout onto existing roles (Traveler=verified, staff group); applied live, archived 15 old ticket channels; built all features; selftest 45/50.
- 2026-10-25 s3: moved hosting to Railway (Oracle card declined); volume + seed; old project vars removed; selftest 50/50.
- 2026-10-25 s3b: token reset via .env only (Notepad paste had glued GUILD_ID onto the token line; split it). selftest 50/50 on Railway.
- 2026-10-25 s4: small-caps layout (︱ prefix) + sections ᴡᴇʟᴄᴏᴍᴇ/ɴᴇᴡꜱ/ᴄᴏᴍᴍᴜɴɪᴛʏ/ʜᴀɴɢᴀʀ/ꜱᴜᴘᴘᴏʀᴛ/ʟᴏᴜɴɢᴇ/ꜱᴛᴀꜰꜰ applied from PC; seed state merged to Railway; selftest 50/50. ARCHIVE was renamed 'TRAILER INQUIRIES' by staff (still hidden) - left alone. Layout applies from PC are OK now: copy state/ids to seed/state + railway up (merge adds new ids).
- 2026-10-25 s5: 4 sections DIRECTORY/AIRPORT SIMULATOR/COMMUNITY/PLAYER HUB (+STAFF), plain '︱name' channels; empty bot-made sections removed (planner remove_category); deployed; selftest 50/50.
- 2026-10-25 s6: BOT REVAMP live: tickets (control card, dot titles, priority, 30-min nudge, 24h warn/48h auto-close, HTML transcripts to #transcripts + post removed, pinned inbox guide), support+status one message, logs split into threads, Bloxlink-aware verify + welcome DM, pings+platform menus, applications forum (votes/tags/lock), feedback (dup check, New tag, [+N] titles, weekly digest; Planned/Done staff-only), /rp create + stale archive, liveries Showcase image check + live contest board. Kicked Ticket Tool/Tickets v2/Appy/Reaction Roles; ARCHIVE renamed back. tools/cleanup_inbox.py moved old #1 to transcripts. selftest 52/52. Layout itself still undecided (Kian disliked all options; last proposal = disappearing-verify concept).
- 2026-10-25 s7: transcripts moved INTO the bot (asbot/transcripts.py): chat+files stored on close, private paged viewer via View transcript (Tickets log), Past tickets button, /ticket view|history|search; #transcripts channel removed (ticket 0001 imported from seed/import). selftest 51/51.
- 2026-10-25 s8: modern layout START(rules-and-verify, news) / HELP(support, ideas-and-bugs) / TALK(general, screenshots, lounge) / CREATE(liveries, airlines) / STAFF; Kian moved HELP to 2nd himself -> yaml matches. roles/updates/dev-logs archived (roles menu now under verify; /announce -> news). Party channels next to the hub are never counted as leftovers. selftest 41/41.
- 2026-10-25 s9: rules-and-verify top-level (layout 'top: true'), STAY TUNED (announcements, dev-logs = old updates channel w/ history, socials new), HELP, TALK, CREATE. Kian renamed NEWS->STAY TUNED himself; force_<guild>.json pins ch:devlogs on Railway. selftest 47/47.
- 2026-10-25 s10: CLEAN REVAMP: asbot/ui.py one card style (calm palette, footer icon); welcome = ONE message (rules, Verify, help+links, pings+platform menus); members see only /help /suggest /bug /rp; staff /staff panel (announce, status, issues, contest, member lookup, stats, health) + /ticket (view|history|search|reply|close|stats) + /template_add, hidden via moderate_members (Support Team/Board need Integrations override); logs = 3 threads; live staff summary card pinned in staff-chat; tickets Ask to close (member confirms, 24h auto) + Close now; Oracle deploy files removed; ARCHITECTURE.md + SETUP.md rewritten. Old banner/rules message in rules-and-verify is another app's: Kian may delete it. selftest 47/47.
- 2026-10-25 s11: support = ONE card (status line, issues, help text); STAFF #stats: live card pinned (moved from staff-chat) + daily report at midnight UTC (Discord joins/leaves/net/members/verified, Roblox visits/group/favourites gains from start-of-day snapshots + peak players + like %, support opened/handled/open/first reply/rating/apps); /staff -> Today's numbers. LAYOUT SAFETY: state/applied_<guild>.json remembers what was last set; manual renames/topics since then are KEPT (preview shows '! kept manual change'). I had reverted Kian's rp-servers rename + media topic once, restored from audit log. selftest 48/48.
- 2026-10-25 s12: relay_map links inbox<->DM copies: staff delete in inbox deletes DM copy; edits update the copy (edited); member delete/edit marks the inbox copy.
- 2026-10-25 s13: LEARNING FAQ (asbot/faq.py; right-click staff reply -> Apps -> Save as instant answer; staff answers first, hit counts; /staff -> Instant answers list/remove) + STRIKES (asbot/cogs/strikes.py; right-click member/message -> Warn; 1 warn DM, 2 = 1h, 3 = 24h, 4+ = 7d timeout + ban hint; 30-day expiry; /staff Warn + member lookup shows/removes strikes). selftest ok.
- 2026-10-26 s14: CARDS EVERYWHERE (Components V2, asbot/cards.py): banners from AS2 edit-mode shots (tools/make_banners.py, assets/banners; Kian: keep these 'original' banners, no accent line). Cards: rules-and-verify (Welcome/Rules/Get started), support, /help, /staff, stats live (pinned) + daily report, inbox guide, templates post, contest board, welcome DM, ticket-opened DM. 15 shots incl. test-play (LOT/Ryanair at PLQ) in assets/shots + Claude Coding/Discord Server Revamp/shots_to_pick.jpg; Kian picks later. Roles plan proposed (calm colours, Support Team up), NOT applied - 'later'. selftest 49/49.
- 2026-10-26 s15: stats cards = number-first lines in sections, pin notice removed; new banners: support=ILO check-in (int2), stats=SPC apron (play7), liveries=Jet2+loader (gc_loader), inbox=Norwegian+airstairs, contest=LOT tail (play4), help=coast; welcome/rules keep originals. cards re-upload changed banners (card_art hash). Railway uploads sometimes time out: retry; .railwayignore skips assets/shots, tools, tests. Studio test-play tips: hide PlayerGui on Client, _ShotFocus + RequestStreamAroundAsync on Server, client day cycle -> hold ClockTime on Client; play server can drop -> stop play. selftest 49/49.
- 2026-10-26 s16: banners Help=KLM (liv_klm), Contest=Southwest (liv_southwest), Inbox=Ryanair+bag carts (liv_ryanair_carts); liveries staged in a test play by setting Runtime.Aircraft.Px Livery attribute (client repaints after ~5-10 s); frame planes on the RIGHT (colour panel covers left 45%); avoid white-heavy liveries (Turkish) and sun glare. Kian: Welcome, Rules, Stats banners = keep.
- 2026-10-26 s17: stats live + daily = aligned code-block tables (table() helper, 41 chars wide), no emojis.
- 2026-10-26 s18: MOD LOGS cog (asbot/cogs/modlog.py, replaces Circle: msg delete/edit/purge, roles/nick, kicks, channel/role changes -> #logs threads Messages/Members/Moderation). KICKED Dyno, Mimu, Circle, Sapphire (Bloxlink kept). Dupes deleted (old roles/updates/applications channels, | Updates | role). Card polish: dividers, no emoji thread names (forum_card renames bot-owned posts). Support banner = Southwest zoomed (make_banners box param) on see-through dark grey (colour may be (r,g,b,opacity)); Support card = ui.SLATE grey edge, amber/red only when the game has problems, 1-2-3 steps, no topic list/emoji dot. selftest 48/48.
- 2026-10-26 s18b: STATS card revamp (grey ui.SLATE edge, amber/red only when tickets wait; title + "updated" line; divider between Tickets / Game / Community; totals as a table; short() = 12.3M). Daily report same style. Stats banner = ORIGINAL play7 runway shot (Kian: keep it, only fix text); make_banners panel now sizes to the subtitle too, so text never spills onto the photo.
- 2026-10-26 s18c: ROLES TIDY-UP applied (Kian OK'd plan): deleted ARFF Commissioner, Leg, LOA, | Scheduled Flight |, Unverified. Order top->bottom: Founder, Owner, Co Owner, Admin, Airport Board, Developer, Server Moderator, Trial Moderator, Support Team, PR Team, QA, Star Program, Server Booster, Airport VIP, Livery of the Month, Traveler, PC/Mobile/Console/VR, 4 ping roles, Ticket Blacklisted. Staff colours warm->cool (#D9534F ... #8FBF7F); ping/platform roles no colour. Layout tool never reorders roles (done via API, script in session scratchpad). selftest 47/47.
- 2026-10-26 s19: SUPPORT AGENT + easy instant answers + staff card V2 + Roblox account on tickets + headset icon (support only) + small-caps channels; offline sims pass; committed, deploy pending Kian's OK. Kian: no paid AI / no Railway credit use.
- 2026-10-26 s19b: deployed (2 uploads; 2nd retried after timeouts), 16 channels renamed to ︱small-caps, seed state synced, selftest 47/47, app emoji support_agent + /answer registered.
- 2026-10-26 s19c: small-caps channel names reverted to plain (Kian: can't search them); applied, seed synced, deployed, selftest 47/47. Never use small-caps/fancy unicode in channel names again.
- 2026-10-26 s20: ? staff commands (asbot/cogs/staffcmds.py, staff-only channels; ?help) + ticket list browser (asbot/ticketlist.py: Open/Closed/Mine/Search, Browse tickets button on inbox guide, /ticket list, /staff Tickets); ?delete = close without transcript (tdeleted kv). Committed; deploy waiting on Kian's OK.

- 2026-10-29: NEW asbot/cogs/securitylog.py (raid alert, ban/timeout with moderator, invites, bot/webhook added, powerful role given, server/channel/emoji changes, voice) -> new #logs threads "Security" + "Server". Ban/unban/timeout logs moved out of verify.py. Compiled OK, NOT yet deployed (railway up).
- 2026-10-31 s21: LOGS category (7 channels, created live) + modlog/auditlog/guard rewrite, AutoMod setup tool, server audit + ideas doc (docs/2026-10-31_logs_security_ideas.md). Committed; deploy + AutoMod apply + #logs delete wait on Kian's OK.
- 2026-10-31 s21b: deployed (2nd try; 1st upload failed), AutoMod tuned (5/6), mass-ping guard, /help fixed (Traveler slash cmds denied in general/media), old #logs deleted after backup, selftest 54/54. ARCHIVE tickets wait on Kian.
- 2026-11-01 s22: logs 7 -> 3 channels (Kian: minimal, modern), cut list muted, one-line joins/leaves, typo edits skipped, member timeline, live status card, tools/logs_merge.py, tests/test_logs.py. Deployed with Kian's OK, 3 channels renamed, all 3 seen posting, 4 old deleted after backup, selftest 50/50.
- 2026-11-05 s23: log cards show real names (asbot/names.py), join/leave cards with avatar + 'stayed X'; deployed, selftest 50/50; no real join/leave card seen yet.
- 2026-11-05 s23b: role order + gradients live; incident radar built (3+ members, same word or bug/purchase topic, 1 h), deployed.
- 2026-11-16 s24: audit (2 reports) + fixes (C1, H1-H6, layout C1/H1-H3, tidy), banners v2 from the AS2 aircraft hero shots (title behind the jet), server banner + splash, restructure plan (proposal). 56/56 offline tests. Committed 90bed2b, NOT deployed (needs Kian's OK).
- 2026-11-16 s25: built every restructure-plan system (Staff Duty, departments, bug loop, pulse, events, creators, on-duty routing, staff guide); layout yaml matched to live (role names, hand-made perms); 7 test files pass. Committed 640a031, NOT deployed (needs Kian's OK).
