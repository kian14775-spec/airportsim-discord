# How the bot is built

Python 3.12 + discord.py, runs 24/7 on Railway (free plan, volume at /data).

## Folders
| Path | What it is |
|---|---|
| `bot.py` | Starts the bot, loads every feature (cog), logging |
| `layout.py` | CLI: `preview` / `apply` the server layout from `layout/server.yaml` |
| `layout/server.yaml` | The whole server: roles, sections, channels, permissions, forum tags |
| `config/bot.yaml` | Texts and settings: rules, FAQ answers, ticket topics, application questions |
| `asbot/ui.py` | The one visual style: palette + `card()`. Every message goes through it |
| `asbot/core.py` | Shared helpers: settings, channel/role lookups by layout key, staff check, logs, Roblox API |
| `asbot/db.py` | SQLite on the volume (links, tickets, panels, applications...) |
| `asbot/panels.py` | Bot-owned messages that are posted once and edited in place |
| `asbot/transcripts.py` | Closed-ticket history + the private viewer |
| `asbot/renamer.py` | Safe channel/thread renames (Discord allows 2 per 10 min) |
| `asbot/opencloud.py` | Roblox Open Cloud: game bans, DiscordPerks DataStore (needs a key) |
| `asbot/layout/` | Layout engine: model, planner (pure diff), executor, Discord backend |
| `asbot/cogs/` | Features, one file each (below) |
| `seed/` | First-boot data for an empty volume + one-off imports |
| `tools/selftest.py` | Live check of permissions, panels, tickets, roles (`python tools/selftest.py`) |
| `tests/` | Offline tests for the layout engine |

## Features (`asbot/cogs/`)
| File | Feature |
|---|---|
| `verify.py` | Welcome message (rules, Verify, role menus), Roblox verify, welcome DM, join/leave + mod logs |
| `tickets.py` | DM tickets in #inbox: triage, control card, priority, timers, ask-to-close, /ticket |
| `staffapps.py` | Staff applications forum |
| `news.py` | Announcements composer, game status on the support card, player count |
| `forums.py` | Ideas-and-bugs (votes, duplicates, digest), airlines (/rp), liveries contest board |
| `community.py` | Feedback votes + "your idea shipped", templates, contest start/end, lounge voice, perks |
| `hub.py` | /help (members), /staff (staff panel), live staff summary card |
| `admin.py` | /layout preview / apply (admins) |
| `health.py` | Watchdog restart, error replies |

## Commands
Members: `/help`, `/suggest`, `/bug`, `/rp create`.
Staff (hidden from members, need the "Timeout members" permission or an Integrations override):
`/staff`, `/ticket view|history|search|reply|close|stats`, `/template_add`. Admins: `/layout`.

## Rules for new code
- Messages: `ui.card(...)` (or `core.embed`) only, never raw colours.
- Channels/roles: look up by layout key (`core.channel(guild, "inbox")`), never by name or id.
- Long-lived buttons: persistent `custom_id` (`asbot:<feature>:...`), registered in the cog's `__init__`.
- Deploy: `railway up --detach` from this folder. Layout changes: edit the yaml, `python layout.py apply`,
  copy `state/ids_*.json` into `seed/state/`, then deploy (see HANDOFF.md).
