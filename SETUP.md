# Setup

Already done: the bot runs 24/7 on Railway (free plan), linked from this folder.

## Things only you can do
- **Let Support Team and Airport Board see staff commands:** Server Settings → Integrations →
  Airport Simulator → `/staff` and `/ticket` → Add roles or members → add those roles.
  (Moderators and admins see them already.)
- **Optional: Open Cloud key** for game ban info, the Unban button and in-game Discord perks.
  Create it under the Airport Simulator Community group with user-restrictions and DataStore
  read/write, put it in `.env` as `ROBLOX_OPENCLOUD_KEY=...`, then run `deploy\railway_vars.ps1`.
- **New bot token:** reset it in the Developer Portal, paste it into `.env` (never in chat), then
  run `deploy\railway_vars.ps1`.

## Everyday
- Logs: `railway logs --lines 50` · status: `railway service status`
- Deploy code: `railway up --detach`
- Layout changes: edit `layout/server.yaml`, deploy, then `/layout preview` + `/layout apply` in Discord
  (details: ARCHITECTURE.md "Layout changes"; backups land in `/data/backups/layout_*.json`)
- Health check: `.venv\Scripts\python tools\selftest.py` (reads layout ids from `seed/state/` on the PC,
  the copy that ships to the bot; a channel made by `/layout apply` after the last seed refresh shows as missing)
- Offline tests: `.venv\Scripts\python tests\test_layout.py` (same for test_sync, test_seed, test_guard,
  test_logs), or `pip install -r requirements-dev.txt` once and `.venv\Scripts\python -m pytest tests`
