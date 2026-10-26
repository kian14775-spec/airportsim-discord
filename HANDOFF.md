# Airport Simulator Discord bot - HANDOFF

## RESUME HERE (2026-10-26, s19)
State: LIVE 24/7 on RAILWAY (free plan, $1/mo credit; bot uses ~$0.5-0.85). Project "exquisite-reprieve"
(free plan = 1 project, reused), service "my-bot", volume "my-bot-volume" at /data (db, layout ids, templates,
logs). Repo folder is `railway link`ed. NO copy runs on the PC.
s19 (committed, NOT DEPLOYED yet, needs Kian's OK): Support Agent (rule-based DM helper, no AI, $0), /answer
add|list|edit|remove|test (trigger words + rough notes -> sentence), new Components-V2 staff ticket card with the
verified Roblox account (bot link -> Bloxlink API if BLOXLINK_KEY -> nickname), "answer shown / none fits" flags,
Save fix button + "save the fix" prompt on close, headset icon (assets/support_agent.png, uploaded as app emoji)
on support messages only, small-caps channel names in layout/server.yaml.
RULE (Kian 2026-10-26): nothing may cost money or eat the Railway credit. No paid AI. asbot/ai.py polish is
OFF unless a FREE-tier GEMINI_API_KEY is set (the game-factory key's project is prepaid + empty: don't use it).
Next:
1. `railway up --detach`, then `python layout.py apply` (16 channel renames to ︱small-caps), copy state/ids to
   seed/state, deploy again; run tools/selftest.py; open a test ticket ("stuck loading") to see the Agent flow.
2. Optional: BLOXLINK_KEY (free, Bloxlink dashboard > API) in Railway vars = exact verified account on tickets.
3. Kian: Open Cloud key (group-owned, user-restrictions r/w + DataStore r/w) -> .env ROBLOX_OPENCLOUD_KEY -> deployailway_vars.ps1.
4. Before 2026-12-01: Railway drops railway.json (config-as-code) -> run `railway config migrate`.
5. Later: remove Ticket Tool / Tickets v2 / Appy / Reaction Roles bots; in-game perks script (Studio).

## Deploying (Railway)
- Code change: `railway up --detach` from the repo (CLI login lives in the real user profile; from Claude's
  tools run railway with the sandbox disabled, or in a terminal tab).
- Logs: `railway logs --lines 50`. Status: `railway service status`.
- First boot on an empty volume copies seed/ (layout ids + panel ids) in; never overwrites live data.

## How layout changes work
Kian says what to change -> edit layout/server.yaml (keep `key`, add old names to `was`) -> `railway up` ->
in Discord `/layout preview` then `/layout apply` (runs ON Railway, so its state on the volume stays the truth).
Don't use `python layout.py apply` on the PC any more: the PC state copy would drift from the server's.
State (key -> Discord id) lives in state/ids_<guild>.json ON THE MACHINE THAT APPLIED; features look up channels with sync.channel_id(guild, key).

## Map
- asbot/layout/model.py (yaml -> specs), planner.py (pure diff), executor.py (runs plan on a backend), discord_backend.py, sync.py (state + preview/apply)
- asbot/cogs/admin.py (/layout preview|apply, admins), health.py (watchdog exit -> systemd restart, error replies, /ping)
- bot.py, layout.py (CLI), deploy/ (systemd unit, server_setup.sh, deploy.ps1), tests/test_layout.py

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
