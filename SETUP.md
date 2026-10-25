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
- Health check: `.venv\Scripts\python tools\selftest.py`
