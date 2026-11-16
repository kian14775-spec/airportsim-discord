# How the bot is built

Python 3.12 + discord.py, runs 24/7 on Railway (free plan, volume at /data).

## Folders
| Path | What it is |
|---|---|
| `bot.py` | Starts the bot, loads every feature (cog), logging |
| `layout.py` | CLI: `preview` / `export [--perms]` the server layout; `apply --from-pc` only as a fallback (below) |
| `layout/server.yaml` | The whole server: roles, sections, channels, permissions, forum tags |
| `config/bot.yaml` | Texts and settings: rules, FAQ answers, ticket topics, application questions |
| `asbot/ui.py` | The one visual style: palette + `card()`. Every message goes through it |
| `asbot/cards.py` | Components V2 cards: banner, sections, buttons inside one message |
| `asbot/core.py` | Shared helpers: settings, channel/role lookups by layout key, staff check, logs, Roblox API |
| `asbot/paths.py` | Where data lives (`DATA`, layout `STATE`) + the boot-time seed merge |
| `asbot/db.py` | SQLite on the volume (links, tickets, panels, applications...) |
| `asbot/panels.py` | Bot-owned messages that are posted once and edited in place |
| `asbot/points.py` | Staff Duty ledger: `award(member, kind, ref)` (idempotent, daily/weekly caps), ISO weeks, departments, breaks, on duty |
| `asbot/transcripts.py` | Closed-ticket history + the private viewer |
| `asbot/ticketlist.py` | The paged ticket browser (Open / Closed tabs) |
| `asbot/faq.py`, `asbot/ai.py` | Instant answers; optional free-tier Gemini wording polish (off without a key) |
| `asbot/names.py`, `asbot/timeline.py` | Readable names on staff cards; a member's history for the /staff lookup |
| `asbot/renamer.py` | Safe channel/thread renames (Discord allows 2 per 10 min) |
| `asbot/opencloud.py` | Roblox Open Cloud: game bans, DiscordPerks DataStore (needs a key) |
| `asbot/layout/` | Layout engine: model, planner (pure diff), executor, Discord backend, sync (state, preview, apply, backup) |
| `asbot/cogs/` | Features, one file each (below) |
| `seed/` | Shipped in the image: data for a fresh volume + ids new since the last deploy + one-off imports |
| `assets/banners/`, `assets/support_agent.png` | Card art (shipped). `assets/shots/` = source screenshots, PC only |
| `tools/` | PC scripts, not shipped: `selftest.py` (live permission/panel/ticket check), `automod_setup.py`, `roles_style.py` (role colours/order), `make_banners.py` |
| `tests/` | Offline tests: layout engine, sync (manual edits, backup, ids cache), seed merge, guard, logs |
| `deploy/`, `docs/` | Railway variable sync script; notes and audits (neither is shipped) |

## Features (`asbot/cogs/`)
| File | Feature |
|---|---|
| `verify.py` | Welcome message (rules, Verify, role menus), Roblox verify, welcome DM |
| `tickets.py` | DM tickets in #inbox: triage, control card, priority, timers, ask-to-close, /ticket |
| `answers.py` | Instant answers: /answer add (or "Save fix" on a ticket) / list / edit / remove / test |
| `radar.py` | Incident radar: 3+ members with the same problem within an hour -> card in #alerts; "It's real" puts it on the support card |
| `staffapps.py` | Staff applications forum |
| `staffcmds.py` | Quick `?` commands for staff in ticket posts and staff channels (`?help`) |
| `strikes.py` | Warn (context menus, /staff): strikes escalate by themselves (DM, 1 h, 24 h, 7 d timeouts), 30-day expiry |
| `news.py` | Announcements composer, game status on the support card, player count |
| `forums.py` | Ideas-and-bugs (votes, duplicates, digest), airlines (/rp), liveries contest board |
| `community.py` | Feedback votes + "your idea shipped", templates, contest start/end, lounge voice, perks |
| `profile.py` | /profile (and right-click > Apps > Profile): Roblox account, liveries, wins, airline, badges |
| `stats.py` | Staff #stats: live card (every 5 min) + daily report at midnight UTC |
| `hub.py` | /help (members), /staff (staff panel), live staff summary card |
| `modlog.py` | Logs -> #activity: messages (7-day text store, image copies, who deleted), one-line joins and leaves, boosts; names and voice only go on the timeline |
| `auditlog.py` | Audit log entries (staff action + who + reason) -> #actions, or #alerts when dangerous |
| `guard.py` | Anti-spam, fake links, hacked accounts, staff-ping nudges, raid lockdown (/lockdown), anti-nuke, setup check, live status card in #alerts |
| `duty.py` | Staff Duty: board in #stats (My week, Go on/off duty, Take a break), Monday close (DMs, #leads report, check-in + promotion cards with lead buttons), Thursday nudge, points from the audit log + public chat, 30-day staff report, Staff guide card in #staff-chat + team welcome DMs |
| `bugs.py` | Bug -> fix loop: QA card on Bug posts (reproduce / can't / duplicate / fixed next update), Confirmed + Fixed tags, game update -> Done + DMs, QA points |
| `pulse.py` | "Playing now" counter channel, one verify-reminder DM, question of the day, shot of the week, poll reminder, boost thank-you |
| `events.py` | Plan an event: Discord event + cover, #announcements post, #general reminder, auto start/end, #media recap, host points |
| `creators.py` | #creators-room card; YouTube RSS (no key) -> new game videos in #socials with credit + PR points; monthly top creators |
| `admin.py` | /layout preview / apply (admins) |
| `health.py` | Watchdog restart, error replies |

## Commands
Members: `/help`, `/suggest`, `/bug`, `/rp create`, `/profile`.
Team buttons (no commands needed, so PR/QA without Timeout members can use them): the Staff board in #stats,
the Staff guide in #staff-chat, the #creators-room card, QA buttons on bug posts, lead buttons in #leads.
Staff (hidden from members, need the "Timeout members" permission or an Integrations override):
`/staff`, `/ticket view|history|search|reply|close|stats|list`, `/answer ...`, `/lockdown`, `/template_add`,
`?` commands, right-click Warn / Profile. Admins: `/layout`.

## Layout changes
The server layout is `layout/server.yaml`; the bot keeps "layout key -> Discord id" in `ids_<guild>.json`
and the yaml's names/topics at the last apply in `applied_<guild>.json`, both in `STATE` (`asbot/paths.py`):
the volume (`/data/state`) on Railway. **The volume is the truth.**

1. Edit `layout/server.yaml` (keep `key`; add old names to `was`), `railway up --detach`.
2. In Discord: `/layout preview`, read every line, then `/layout apply` (runs ON Railway).
3. Before changing anything, apply saves the current values of everything the plan touches (names, topics,
   every overwrite with its target and type, role settings, positions) to `/data/backups/layout_<utc>.json`;
   the newest 10 are kept. The "Done" reply names the file.

What apply owns, and what it leaves alone:
- **Permissions:** a channel's overwrites for `@everyone` and the yaml's own roles are set to the preset;
  overwrites for anything else (Server Booster, Admin, other bots, single members, any role not in the yaml)
  are never changed or removed, even when the same channel's perms are rewritten. A stray overwrite for a
  role the yaml *does* list is drift and is removed. The preview shows the exact change per target, e.g.
  `@Traveler: allow -attach_files; deny +mention_everyone` (`+x` = added to that list, `-x` = taken off it)
  and lists the overwrites left as they are.
- **Hand edits:** a channel/section name or topic changed in Discord is kept on every later apply until the
  yaml itself changes that field (the preview says `kept manual change: ...`; copy it into the yaml).
- **External roles:** `- {key: booster, name: Server Booster, external: true}` in `roles:` references a role
  Discord or another bot owns. It is matched by name (`was` works) among all roles, never created, renamed or
  edited; missing = an error in the preview. It can be used in presets, groups and `perms_extra`, and its
  overwrite is managed only on channels that name it. Listing a Discord-managed role without
  `external: true` is an error (the bot would otherwise make a duplicate).
- **`perms_extra:`** per channel, merged on top of the channel's preset (or its section's):
  ```yaml
  - key: chat
    name: general
    perms_extra:
      verified: {deny: [attach_files]}           # Traveler keeps the rest of the preset
      booster:  {allow: [attach_files, embed_links]}
  ```
  Per target, the extra `allow`/`deny` flags win and the preset's other flags stay; a target the preset
  doesn't name gets exactly these flags. To encode a hand-made perm, copy its exact flags from
  `python layout.py export --perms` (read-only). Member overwrites stay out of the yaml (they survive applies).

Applying from the PC (fallback only, e.g. the bot is down): `python layout.py apply --from-pc`. On the PC,
`STATE` is `seed/state/`, which can be behind the volume. Afterwards commit `seed/state/` and deploy:
on boot the volume gets the keys that are new in the seed. An existing key whose id changed (a channel the PC
re-made) needs that key in `seed/state/force_<guild>.json`, which boot applies once. Then run `/layout preview`
on Railway: it must say "Server already matches the layout."

Seed rules on boot (`paths.seed_once`): a fresh volume gets all of `seed/`; otherwise `applied_*` is copied
only when the volume has none, ids keys are added only when new in the seed (`seeded_<guild>.json` remembers
what was offered, so a key the volume dropped is not brought back), and nothing on the volume is overwritten.

## Staff Duty and friends (2026-11-16)
- Points: features call `points.award(member, kind, ref)`; `ref` makes it count once, rules/caps/targets are
  `duty:` in config/bot.yaml. Only members of a department (`duty.departments`) score. Board refreshes ~2 min after
  any change (`points.dirty`), else hourly.
- Weeks are ISO weeks from Monday 00:00 UTC. The first (partial) week after the first boot is a warm-up and is
  never closed (`duty:start_week`). `duty:closed` = last closed week; the Monday loop sets it before the DMs.
- The bot never demotes/promotes on its own: #leads cards with buttons; leads = `duty.leads` role keys or admins.
- Hooks: tickets.py (solved = member wrote after the first staff reply; first reply <= 15 min), strikes.py (warn),
  duty.py (audit log ban/kick/timeout with a reason, timeout undone < 1 h = revoked; 5+ messages a day in
  `duty.chat_channels`), bugs.py, events.py, creators.py. news.py dispatches `game_updated` when Roblox's `updated` changes.
- Texts new staff read are `guide:` in config/bot.yaml (pages for the guide card, How this works, welcome DMs).

## Rules for new code
- Messages: `ui.card(...)` (or `core.embed`) only, never raw colours.
- Logs: `core.log_staff(guild, text, kind=..., event=...)` or `embed=core.log_card(...)`; kinds map to the 3 LOGS channels in `core.LOG_CHANNELS`; `logs: muted:` in config/bot.yaml switches events off. `timeline.add(user_id, kind, text)` adds a line to a member's history in the /staff lookup.
- Channels/roles: look up by layout key (`core.channel(guild, "inbox")`), never by name or id. The ids file is
  cached in memory and re-read only when it changes on disk.
- Long-lived buttons: persistent `custom_id` (`asbot:<feature>:...`), registered in the cog's `__init__`.
- Deploy: `railway up --detach` from this folder. Layout changes: see "Layout changes" above.
- Tests: `.venv\Scripts\python tests\<file>.py` for each file in `tests/` (or `pytest tests` after
  `pip install -r requirements-dev.txt`). Nothing in `tests/` connects to Discord.
