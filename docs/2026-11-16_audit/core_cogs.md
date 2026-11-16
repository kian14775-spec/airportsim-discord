# Audit 2026-11-16: bot.py, asbot/*.py, asbot/cogs/*

Read-only audit. Scope: `bot.py`, `asbot/{core,db,ui,cards,panels,names,timeline,transcripts,ticketlist,faq,ai,opencloud,paths,renamer}.py`,
every file in `asbot/cogs/` (tickets.py read in full). Layout engine, tools/ and tests/ are another agent's scope
(only touched where a cog calls into them).

How it was checked: every finding was traced in the code around it. Extra checks:
- `python -m compileall -q asbot bot.py`: OK. `pytest` is not installed in `.venv`, so the tests were not run.
- discord.py 2.7.1 source in `.venv`: `tasks.Loop` only retries `OSError, GatewayNotFound, ConnectionClosed,
  aiohttp.ClientError, asyncio.TimeoutError` (tasks/__init__.py:162-168). Any other exception, `discord.HTTPException`
  included, stops the loop for good (tasks/__init__.py:249-282). Ephemeral views with `timeout=None` are forced to
  15 min (interactions.py:1102).
- SQLite timing in a scratch DB, same settings as `db.py` (autocommit + WAL, default `synchronous=2`/FULL):
  2,000 upserts through `with conn: executemany(...)` took **1.14 s**. Inside `BEGIN/COMMIT` they took **0.001 s**.
- Persistent `custom_id`s against their registrations: no mismatches. SupportCard, WelcomeCard, CheckView and
  PartyPanel are all `add_view`'d. Every DynamicItem template is registered and they don't overlap.

Confidence: **CONFIRMED** = the failure follows directly from the code (and library source where cited).
**LIKELY** = depends on timing, Discord behaviour or config, but the path exists.

---

## Critical

**C1. Background loops die for good on the first non-network error, and nothing notices.** CONFIRMED
- `asbot/cogs/tickets.py:1191-1214` (`timers`), `:1272-1283` (`monthly`); `asbot/cogs/news.py:135-150` (`poll_game`:
  only `game_stats()` is inside try, `render_status()`/`post_card` is not); `asbot/cogs/stats.py:179-205` (`tick` ->
  `update_live` -> `cards.post_card`, not guarded); `asbot/cogs/forums.py:215-232` (`weekly`), `:234-253` (`daily`:
  `th.send`/`th.edit` not guarded); `asbot/cogs/community.py:280-293` (`contest_auto` -> `end_contest` -> `th.send`,
  `ann.send`); `asbot/cogs/modlog.py:127-138` (`prune`: a `sqlite3.OperationalError` such as disk full kills it).
- What goes wrong: one Discord 5xx (`DiscordServerError` after discord.py's retries) or a 403 on `thread.send` during
  a 5-minute `timers` run ends the task. After that there are no 30-min nudges, no 24 h warnings and no 48 h
  auto-closes, the support card and stats card freeze, and nothing comes back until the next deploy.
  `health.watchdog` only checks the gateway (`health.py:30-37`), so the bot still looks healthy.
- Fix: wrap each loop body in `try/except Exception: log.exception(...)` (or `loop.add_exception_type(discord.HTTPException)`
  plus an `@loop.error` that restarts). Have Health check `loop.is_running()` for every loop and post to #alerts.

---

## High

**H1. `tickets.open()` can leave a ticket that blocks the member for good.** CONFIRMED
- `asbot/cogs/tickets.py:812` (the DM is sent and the row already exists), then three unguarded calls:
  `:828` `roblox_user_by_name` (appeals), `:831` `opencloud.get_ban` (aiohttp errors are not caught in
  `opencloud.py:29-38`) and `:841` `inbox.create_thread`.
- What goes wrong: Roblox times out or returns HTML 5xx during an appeal, or `create_thread` fails. The member already
  has "the team has your message", but no inbox post exists and `thread_id` stays NULL. `blocked_reason` then answers
  every retry with "You already have ticket 0042 open", and `_from_member` (`:1098-1100`) replies "can't be reached"
  to every DM. `timers` never closes status `open`, so this lasts forever. The member's only way out is the DM
  "Close my ticket" button, which isn't there in helper mode.
- Fix: wrap everything from the DM to `create_thread` in try/except. On failure, delete the row and kv keys and tell
  the member. Or create the post first and DM the member after.

**H2. The disk budgets add up to more than the 500 MB volume.** CONFIRMED (arithmetic). How soon it fills: LIKELY over months.
- `asbot/transcripts.py:136` keeps ticket files up to **350 MB**. `asbot/cogs/modlog.py:44` (`keep_images_mb: 150`)
  keeps image copies up to **150 MB**. That is already 500 MB before `bot.db` (7 days of `msglog`, kv, WAL) and the
  logs (`bot.py:26`, 6 x 2 MB). The log cache also has no cap when files are written (`modlog.py:161-170`); it is only
  pruned every 10 min.
- What goes wrong: the volume fills, every `db.run` raises `database or disk is full`, tickets can't open, all
  listeners that write fail, and `modlog.prune` dies (C1), so nothing frees space again.
- Fix: one total budget checked with `shutil.disk_usage(DATA)` (e.g. ticket files 200 MB + log cache 80 MB), checked
  before saving a file and not only in a 10-min prune.

**H3. Hacked-account alert storm.** CONFIRMED
- `asbot/cogs/guard.py:294-296`: every `@everyone`+link or scam message calls `hacked()` on its own, and
  `hacked()` clears the history only after an await (`:367-368`).
- What goes wrong: a hacked account posts the same scam in 8 channels within 2 s. That gives up to 8 #alerts cards,
  each pinging Admin + Server Moderator, plus 8 DMs, 8 timeout calls and 8 delete passes.
- Fix: at the top of `hacked()`/`punish()`, check and set an in-memory "handling" mark per user (and skip if
  `member.is_timed_out()`), and pop the history before the first await.

**H4. `punish()` race escalates one burst to a 24 h timeout.** CONFIRMED
- `asbot/cogs/guard.py:336-345`: the hit is recorded, then `await self.remove(msgs)` runs, and only then is the
  history popped.
- What goes wrong: a spammer's 7th and 8th messages arrive during the bulk delete. Each one still sees the 6-message
  burst and calls `punish()` again, so the hits in 24 h go 1 -> 2 -> 3 and the timeout goes 10 min -> 60 min ->
  24 h for one burst. Staff get 3 log cards and the member gets 3 DMs.
- Fix: same in-flight guard as H3. Pop the history synchronously before awaiting.

**H5. Several lockdowns from one raid.** CONFIRMED
- `asbot/cogs/guard.py:453-473`: the state check is at `:453`, then `start_lockdown` awaits `g.edit(...)` (`:469`)
  before writing `guard:lockdown` (`:473`).
- What goes wrong: during a raid, every join that arrives while `g.edit` is running starts its own lockdown. That
  means several "Lockdown on" cards pinging Admin/Mod and several guild edits.
- Fix: write the lockdown state (or an in-memory flag) before the first await.

**H6. Slow work runs before the interaction response, so the response fails after the action already happened (retry = duplicate).** LIKELY
- `asbot/cogs/strikes.py:43-65` `give_strike`: timeout + DM (2-3 HTTP calls) before `response.send_message`. Staff see
  "did not respond", warn again, and the member gets strike 2, a 1 h timeout instead of a warning.
- `asbot/cogs/tickets.py:1221-1232` `/ticket reply`: `relay_staff` (DM + refresh, about 4-6 calls) runs before the
  response, so a retry sends the member the saved reply twice.
- `asbot/cogs/news.py:47-57` `ConfirmPost.post`: `send` + `publish` run before the response. Publish is rate-limited
  (10/h per channel) and discord.py sleeps on the 429. The button is still live, so a second click posts a duplicate
  announcement with the role ping.
- Fix: `await interaction.response.defer(...)` first, disable the view or set a done flag, then use `followup`.

---

## Medium

**M1. `core.py:33-40` -> `layout/sync.py:24-42`: every `channel()`/`role()` call reads and parses `ids_<guild>.json` from disk.** CONFIRMED
About 6 reads per guild message: `modlog.skip_channel` 4 (`modlog.py:47-49`), `tickets.on_message` 1
(`tickets.py:1082`), `guard.exempt -> is_staff -> staff_roles` 1. More on every delete, edit and reaction. On the
$1/mo Railway credit this is CPU spent on every message. Fix: keep the ids in memory and reload when `save_state`
runs or the file's mtime changes.

**M2. SQLite: one fsync per statement, and the "batch" writes are not transactions.** CONFIRMED (measured)
`db.py:51-53` uses autocommit + WAL with the default `synchronous=FULL`. `with c:` in `names.py:41` (`remember_all`)
and `transcripts.py:87-89` does not open a transaction in autocommit mode, so each row commits on its own (1,000x
slower in the test). `remember_all(g.members)` blocks the event loop on every `on_ready`, and `msglog` writes one
fsync per guild message (`modlog.py:159`). Fix: `PRAGMA synchronous=NORMAL` (safe with WAL) and a
`db.tx()` helper that does `BEGIN ... COMMIT`.

**M3. `on_ready` work runs again on every non-resumed reconnect.** CONFIRMED
`tickets.py:695-709` (app-emoji fetch, inbox card, `refresh()` of every open ticket at about 3-4 calls each),
`verify.py:236-243`, `news.py:131-133`, `community.py:98-101` and `modlog.py:140-146` (all members upserted, invites,
audit log) have no run-once guard. `guard.py:234` does. On a flaky night this is many unneeded API calls and slow DB
writes per reconnect. Fix: `if self._booted: return` per cog.

**M4. `staffcmds.py:90-91`: `?` commands run in channels every member can read.** CONFIRMED
The "public channel" test only checks @everyone. The `members` preset (`layout/server.yaml:140-142`) denies @everyone
and allows `verified`, so #general passes as a "staff place". `?t 142` posts a ticket summary (member, handler,
rating) where all verified members can read it, and `?help`/`?tickets`/`?answer` also work there. Fix: also require
the `verified` role to lack `view_channel`, or allow only the staff category and inbox threads.

**M5. `staffapps.py:82-103`: any staff member can accept an application, and the bot grants the role.** CONFIRMED
`is_staff` is the only check, so Support Team can accept a "Moderator" application. The bot then gives Trial
Moderator (`config/bot.yaml` `accept_roles`), whatever the clicker's own rank, and Trial Moderator counts as staff
(strikes, game unban...). Related: `tickets.py:355-368`, where any staff member can game-unban with one click and no
confirm. Fix: require `manage_roles` and `actor.top_role > granted role`, and add a confirm step to unban.

**M6. `radar.py:149-155`: old incidents swallow new ones.** CONFIRMED
`live` is every `new`/`real` incident with no age limit, and `note_ticket` returns on the first match. An ignored
`topic:bug` incident from last month absorbs every later bug ticket (its list grows and its card is re-edited each
time), and no new bug alert can ever fire. Fix: expire `new` incidents after the window/cooldown, and only attach
tickets to recent or `real` ones.

**M7. `radar.py:201-216`: a double-click on "Fixed" runs `close_out` twice.** CONFIRMED
The status check is followed by `defer()` (await) and then the UPDATE, so every member gets DM'd twice. Also
`radar.py:175-186`: `ConfirmModal` adds the issue to the support card even when its guarded UPDATE matched 0 rows
(a second confirmer), which duplicates the known issue. Fix: do a conditional UPDATE first and act only if
`rowcount == 1` (`db.run` returns `lastrowid`, so add a variant that returns the rowcount).

**M8. `guard.py:553-568`: anti-nuke false positives.** LIKELY
Only the owner and this bot are exempt. A moderator who bans 5 raiders in 60 s loses their dangerous roles and gets
a 24 h timeout. Another bot (e.g. Bloxlink or a mod bot) that kicks in bulk gets kicked. `webhook_create` counts as
"destroy". Fix: raise the threshold or exempt staff during a lockdown, add an allow-list of bots and roles in
`guard:`, and drop `webhook_create` from DESTROY.

**M9. Bot-owned threads are counted as the bot's.** CONFIRMED (ownership). LIKELY (archive behaviour).
`forums.py:136` (`/rp create`) and `:63` (`/suggest`, `/bug`) create posts as the bot. So `forums.py:248` "is this
still active?" pings the bot, not the airline owner, and `profile.py:28` (`th.owner_id == uid`) leaves every
/suggest and /bug post out of "N ideas". `forums.daily` also only scans `forum.threads`, the active ones. Discord
auto-archives idle forum posts after at most 7 days, so a post idle for 30 days is never seen and the stale flow
probably never runs. Fix: read ownership from `rp_posts` and `feedback_author:*`, and scan archived threads or drop
the feature.

**M10. `forums.py:263` vs `community.py:226-228`: the live board and the final winner count different threads.** LIKELY
The hourly board counts only active threads. `end_contest` also counts archived ones. Once entries auto-archive, the
live top 5 no longer matches the real winner. Fix: use the same thread list for both.

**M11. `community.py:300-317`: party voice has no limit per member.** CONFIRMED
Hopping in and out of the hub creates a channel every time (channel-create rate limit, 500-channel cap). If
`move_to` fails (`:310-313`), the empty channel stays until restart. Also `community.py:75-82`: the PartyModal rename
skips `renamer` (2 renames per 10 min) and has no defer, so a second rename hangs on the 429 and the modal fails.
Fix: one party per owner (move them back to it), remove the channel after a failed move, and use `rename_soon` with
an immediate reply.

**M12. `renamer.py:21-38`: a rename can get lost.** LIKELY
While `await ch.edit` runs (it can sleep for minutes on a 429, since `_last` is empty after a restart), new
`rename_soon` calls only update `_wanted`, because the task still exists. `finally` then pops the task, and the newest
title is not applied until something changes again. Example: a ticket title stays "claimed" after it moves to
"waiting". Fix: in `finally`, if `ch.id in _wanted`, schedule the task again.

**M13. `tickets.py:1086-1107`: the member -> staff relay can drop messages silently.** LIKELY
Up to 10 attachments of up to 10 MB each are read into RAM and sent in one message. Over the upload limit, the
`HTTPException` is not caught in `on_message`: the member gets no ✅ and no notice, and staff never see the message.
`thread.edit(archived=False)` (`:1101-1102`) is not guarded either. Fix: cap the total at `guild.filesize_limit`,
catch the error and tell the member, and send big files as links.

**M14. Other places that do network work before responding.** LIKELY (lower risk than H6)
`community.py:190-204` `template_add` (24 MB download + forum card edit); `forums.py:91-108`, `:75-78`, `:122-140`
(archived_threads + create_thread + logo download; a retry makes a duplicate post); `hub.py:151-152` "Today's numbers"
(3 Roblox calls); `tickets.py:355-362` unban (Open Cloud call, and its aiohttp errors aren't caught);
`verify.py:202-227` roles select (2 role edits; only `Forbidden` is caught). Fix: defer first.

**M15. Roblox calls outside try in interaction paths leave "thinking…" forever.** CONFIRMED
`profile.py:42-44`; `verify.py:58`, `:73` and `:115` (`roblox_user_by_name` calls `r.json()` on any status, so HTML
5xx raises `ContentTypeError`, `core.py:159-163`); `opencloud.py:29-38` (only JSON errors are caught;
`ClientError`/`TimeoutError` reach `tickets.open` and the unban button). Fix: one `core.safe(coro)` wrapper that
returns None on `aiohttp.ClientError`, `TimeoutError` and `ValueError`, used by all Roblox helpers.

**M16. The support and stats cards are edited on a timer even when nothing changed.** CONFIRMED
`news.py:135-150` edits the support card every 3 min and `stats.py:207-238` edits the live card every 5 min, each with
`fetch_message` + `edit`. `cards.py:76-82` re-reads and md5-hashes the banner PNG on every call. Both loops fetch
`game_stats`. That is about 1,500 Discord calls a day plus file I/O for little change, on a credit already at
$0.5-0.85 of $1. Fix: compare a hash of the rendered text (as `guard.update_status` already does), use
`PartialMessage.edit`, cache banner hashes by mtime and share one Roblox snapshot.

**M17. `guard.py:216, 301-303`: guard history keeps full Message objects for 24 h.** LIKELY
Up to 30 full `discord.Message` objects are kept per member for 24 h (`tick` only drops a member after 24 h idle),
but the checks only look back 120 s. RAM, which Railway bills, grows with the number of people chatting each day.
Fix: store `(time, channel_id, message_id, sig)`, drop entries older than 120 s when adding, and delete via
`PartialMessage`.

**M18. `modlog.py:191-221`: every single delete costs a sleep, an audit-log call and a card, the bot's own included.** CONFIRMED
Each delete, including guard's own clean-ups, waits 1.5 s, makes an audit-log call and posts a card. A hacked account
cleaned across 10 channels gives 10 audit calls and 10 #activity cards on top of the alert. Fix: skip when the
deleter is this bot, or when guard just removed the message (a shared "recently removed" set).

**M19. `paths.py:20-30`: seed state overwrites live state on every boot.** LIKELY
Every boot copies the seed `applied_*.json` over the volume copy and re-forces `force_*.json` (currently
`ch:devlogs`). HANDOFF says `/layout apply` on Railway is the source of truth, but a restart reverts it. If dev-logs
is ever recreated, every restart points `devlogs` back at the dead id. Fix: apply `force_` once (write a marker), and
copy `applied_` only when the volume has none.

**M20. `staffapps.py:43-61`: an application row can block the member.** CONFIRMED
The row is inserted before `create_thread`. If the post fails, the member is stuck with "You already have an
application for this waiting". Fix: insert after the post exists, or delete the row on failure.

**M21. `health.py:24, 39-50` + `core.py:61-67`: a failed staff check sends two replies.** CONFIRMED
`staff_only` replies "Staff only." and returns False. The resulting `CheckFailure` reaches `tree.on_error`, which logs
an ERROR traceback and sends a second "Something went wrong. Staff have been told." Fix: handle
`app_commands.CheckFailure` quietly.

**M22. `admin.py:46-61`: `/layout` only relies on `default_permissions`.** LIKELY
`/layout preview|apply` has no runtime admin check. An Integrations override can open it up, and those are already
used for staff commands. `apply` with `archive_extras` archives channels. Fix: add an `interaction_check` that
requires `administrator`.

**M23. `community.py:129` and `modlog.py:162`: unreferenced fire-and-forget tasks.** CONFIRMED
These `create_task` calls keep no reference and catch nothing. A `fetch_user` NotFound (`community.py:144`) kills
`notify_shipped` before "Told N supporters". Fix: keep the tasks in a set and wrap their bodies.

**M24. `transcripts.py:184-189`: the Screenshots button can fail on size.** LIKELY
It sends up to 10 stored files (budget 25 MB per ticket) in one ephemeral reply. Over the upload limit, Discord
refuses and the button fails. Fix: cap the batch at `guild.filesize_limit` and split it.

**M25 (low). `transcripts.py:140`: `prune` deletes the newest ticket files first from ticket 10000.** CONFIRMED
It sorts folders by name (`0001`…`9999`, then `10000`), so from ticket 10000 on the newest folders sort first and get
deleted first. Fix: sort by `int(p.name)`.

---

## Cleanup (dead code, duplication, structure)

**Dead or stale**
- `core.py:207` `ensure_list`: never used.
- `panels.py:14-35` `ensure_panel`: never used (only `drop_panel` is).
- `tickets.py:155-166, 512-522, 977-980`: `control_card`, `staff_view` and the old embed-card path in `refresh`.
  Delete once no pre-V2 tickets are open.
- `verify.py:28, 32-33`: looks up the "Unverified" role, which was deleted on 2026-10-26 (HANDOFF).
- `tickets.py:699-704`: the one-off seed ticket import runs on every `on_ready`.
- `hub.py:83`: `timeout=None` "members click long after", but discord.py forces 15 min on ephemeral views, so the
  buttons die after 15 min anyway. Either make the /help buttons persistent DynamicItems or drop the comment.
- `health.py` docstring says systemd. `.env.example` still has Oracle `DEPLOY_*` vars.

**Duplicated logic (drifting)**
- Ticket actions exist twice: `tickets.TicketButton`/`PrioritySelect` and `staffcmds.c_claim/c_urgent/c_dev/c_takeover/c_close`.
  They already differ: the button claim logs `ticket_claimed`, `?claim` doesn't, and the urgent text is copied.
  Put one `Tickets.claim/set_priority/send_to_dev/take_over` service in the cog and call it from both.
- Two `AppealButton` classes (`tickets.py:606`, `hub.py:86`). Two `DANGER` tuples (`auditlog.py:29`, `guard.py:38`).
  Reaction-vote counting in 3 places (`community.py:237`, `forums.py:57`, `forums.py:267`). Pin + delete the
  "pinned a message" notice twice (`stats.py:239-246`, `guard.py:271-275`).
- Roblox profile lookup twice (`tickets.gather_profile`, `profile.profile_card`). `game_stats` polled by both
  `news.poll_game` and `stats.tick`. A `guild` property copied into 6 cogs.
- Feedback/liveries `on_thread_create` is split between `community.py:104` and `forums.py:181`. Move the feedback
  votes and "shipped" DMs into forums.py next to the vote titles.

**Breaks the "Rules for new code" in ARCHITECTURE.md**
- Lookups by role name instead of layout key: "Support Team" (`tickets.py:390`, `staffcmds.py:136`),
  "Ticket Blacklisted" (`tickets.py:757`), `dev_role_name` (`tickets.py:343`, `staffcmds.py:154`), `accept_roles`
  (`staffapps.py:98`), guard `alert_roles`/`protect_pings` (`guard.py:133, 405`), `profile.py:15-16`
  BADGES/PLATFORMS, and `core.staff_roles` `staff_role_names`.
- Raw colours: `profile.py:79` (`0xF1C40F`), `transcripts.py:166` (`Colour(0x2B2D31)`).

**Structure**
- `tickets.py` (1291 lines) should become a `tickets/` package: `model.py` (`ticket()`, `topic_label`, `state_of`,
  `summary_card`, card kv), `views.py` (DynamicItems + modals), `helper.py` (Support Agent), `relay.py` (DM<->post,
  edit/delete mirror) and `cog.py` (commands, timers). `model.py` also removes the function-level import loops in
  `transcripts.py:195` and `ticketlist.py:54, 132`.
- Table DDL runs at import time in 7 modules (names, timeline, transcripts, faq, strikes, modlog, radar). Move it into
  `db.SCHEMA` / `MIGRATIONS`.

**Unbounded (small) growth, worth a line in `modlog.prune`**
- `relay_map` rows are only deleted when the source message is deleted, never on ticket close (`tickets.py:275-278`).
- Per-ticket kv keys (`tcard`, `tlast`, `ticket_roblox`, `tdeleted`, `feedback_author`) and per-day keys (`joins`,
  `leaves`, `snap`, `reported`, `peak`) are never removed. `incidents.tickets` JSON grows (see M6).
- In memory: `tickets._dm_nag` (`:655`), `guard.nudged`/`contained`, `modlog.del_counts` (`:118`), `renamer._last`.

**Small things**
- `bot.py:46-47`: `tree.sync` on every boot. Sync only when a hash of the command set changes.
- `paths.py:12`: `DATA` is computed at import, before `load_dotenv` (`bot.py:57`), so an `ASBOT_DATA` in `.env` would
  be ignored.
- `core.http()`: the aiohttp session is never closed.
- `guard.py:241`: reads the private `g._safety_alerts_channel_id`.
- ARCHITECTURE.md is out of date. The feature table lacks stats, strikes, radar, answers, staffcmds and profile. The
  folder table lacks cards, names, timeline, faq, ai and ticketlist. The command list lacks `/answer`, `/profile`,
  `/lockdown`, `/rp` and the context menus.
