# Airport Simulator Discord bot - HANDOFF

## RESUME HERE (2026-10-25, s3)
State: LIVE 24/7 on RAILWAY (free plan, $1/mo credit; bot uses ~$0.5-0.85). Project "exquisite-reprieve"
(free plan = 1 project, reused), service "my-bot", volume "my-bot-volume" at /data (db, layout ids, templates,
logs). Repo folder is `railway link`ed. Selftest 50/50 against the Railway bot. NO copy runs on the PC.
Next:
1. DONE 2026-10-25: token reset privately (never in chat), pushed to Railway; selftest 50/50.
2. Kian: Open Cloud key (group-owned, user-restrictions r/w + DataStore r/w) -> .env ROBLOX_OPENCLOUD_KEY -> deployailway_vars.ps1.
3. Before 2026-12-01: Railway drops railway.json (config-as-code) -> run `railway config migrate`.
4. Later: remove Ticket Tool / Tickets v2 / Appy / Reaction Roles bots; in-game perks script (Studio).

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
