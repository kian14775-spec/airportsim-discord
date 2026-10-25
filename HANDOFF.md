# Airport Simulator Discord bot - HANDOFF

## RESUME HERE (2026-10-25, s2)
State: LIVE on the Airport Simulator server (guild 1347984080842788914). Layout applied (server matches
layout/server.yaml; pre-change backup in backups/). All features built: verify, roles panel, inbox tickets
(+triage, profile, queue, ratings, saved replies, notes, dev handoff, monthly leaderboard), appeals,
applications, /announce, status board + player count, /suggest /bug + shipped DMs, templates + Livery of
the Month, party voice, perks writer. tools/selftest.py = 45/50 PASS (the 5 fails = role height).
Bot currently runs on Kian's PC (background process from the Claude session), NOT 24/7 yet.
Next:
1. DONE 2026-10-25: bot role dragged up; selftest 50/50.
2. Kian: reset the bot token (it was printed once in chat) and paste the new one in .env; Oracle VM per SETUP.md -> deploy\deploy.ps1 -FirstTime.
3. Kian: Open Cloud key (group-owned, Airport Simulator: user-restrictions r/w + DataStore r/w) -> .env ROBLOX_OPENCLOUD_KEY (appeals ban info/unban, perks). Game-side perks script not installed yet.
4. Later: remove Ticket Tool / Tickets v2 / Appy / Reaction Roles bots (replaced); click-test verify + a ticket with a real account.

## How layout changes work
Kian says what to change -> edit layout/server.yaml (keep `key`, add old names to `was`) ->
`deploy\deploy.ps1 -Layout preview` -> show him -> `-Layout apply`. Before the Oracle VM exists: `python layout.py preview|apply` on the PC.
State (key -> Discord id) lives in state/ids_<guild>.json ON THE MACHINE THAT APPLIED; features look up channels with sync.channel_id(guild, key).

## Map
- asbot/layout/model.py (yaml -> specs), planner.py (pure diff), executor.py (runs plan on a backend), discord_backend.py, sync.py (state + preview/apply)
- asbot/cogs/admin.py (/layout preview|apply, admins), health.py (watchdog exit -> systemd restart, error replies, /ping)
- bot.py, layout.py (CLI), deploy/ (systemd unit, server_setup.sh, deploy.ps1), tests/test_layout.py

## Log
- 2026-10-25 s1: layout + bot features locked in chat; built layout engine, bot core, Oracle deploy; tests pass.
- 2026-10-25 s2: found old token in ~/my-bot/.env (same bot app); mapped layout onto existing roles (Traveler=verified, staff group); applied live, archived 15 old ticket channels; built all features; selftest 45/50.
