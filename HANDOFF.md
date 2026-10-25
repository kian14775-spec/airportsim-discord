# Airport Simulator Discord bot - HANDOFF

## RESUME HERE (2026-10-25)
State: layout-as-code engine + bot core DONE and tested offline (7 tests). Not run against Discord yet.
Layout = layout/server.yaml (locked draft, 12 public channels). Features list = Claude Coding/Discord Server Revamp/PLAN.md.
Next:
1. Kian does SETUP.md steps 1-3 (bot token + GUILD_ID in .env, Community on, Oracle VM). He never pastes the token in chat.
2. `python layout.py preview` on a test server, then the real one; apply (`--archive-extras` hides old channels, never deletes).
3. Build features as cogs in asbot/cogs/ (v1: verify, roles panel, inbox tickets + fix-it-first triage, status board).
Blockers: none on our side; waiting for Kian's setup.

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
