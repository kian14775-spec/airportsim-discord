# Fixes 2026-11-16: core + cogs (from core_cogs.md)

Scope: `bot.py` (unchanged), `asbot/{core,db,transcripts,opencloud}.py`, `asbot/cogs/*`, `tests/test_fixes.py` (new).
No new dependencies, no new loops, no new background tasks. Line numbers are after the change.

| Finding | Where | What changed | Test (`tests/test_fixes.py`) |
|---|---|---|---|
| C1 | `core.py:24` `guarded` | New decorator for loop bodies: any `Exception` is `log.exception`'d and the loop runs again next time (keeps the function name, which `tasks.Loop` needs). | `test_loop_errors_dont_stop_the_loop` |
| C1 | all 11 loops: `tickets.py:1219,1310` (timers, monthly), `news.py:149`, `stats.py:180`, `forums.py:216,236,261` (weekly, daily, contest_board), `community.py:281`, `modlog.py:133`, `guard.py:535`, `health.py:36` | `@guarded` under every `@tasks.loop`. Also per-item guards where one bad item blocked the rest forever: `tickets.timers` (nudge send, each auto-close), `forums.daily` (each rp post). | `test_every_cog_loop_is_guarded` (fails if a new loop has no `@guarded`) |
| C1 | `health.py:46` `revive_loops` | The existing 30 s watchdog tick now checks every cog's loops; a loop whose task ended (not cancelled) is `start()`ed again and one line goes to #alerts (`log_staff(kind="Security", event="loop_restarted")`, at most once an hour per loop). | `test_loop_errors_dont_stop_the_loop` |
| H1 | `tickets.py:65` `drop_ticket`, `:818-872` | Any DM error (not only Forbidden) and any error between the DM and `create_thread` + `UPDATE thread_id` rolls back: row + `tbot/tcard/ticket_roblox` kv deleted, half-made post deleted, member gets a "didn't reach the team, open it again" DM and an ephemeral "try again". `helper_note` failure no longer breaks the reply. | `test_ticket_open_rolls_back` |
| H1/M15 | `opencloud.py:31` `_call` | `aiohttp.ClientError`/`TimeoutError` caught -> `(0, {})`, so `get_ban` = None, `unban`/`set_perks` = False. | `test_roblox_errors_become_none` |
| H2 | `core.py:21,36` `disk_ok` | One shared check: `shutil.disk_usage(DATA)`; refuses (and logs a warning) if saving would leave < 50 MB free. | `test_disk_budget` |
| H2 | `transcripts.py:21,76,137` | Ticket files cap 350 MB -> `FILES_TOTAL` 200 MB; `disk_ok` before each screenshot save; a failed save (`OSError`) skips the file instead of losing the transcript. M25 one-liner: prune sorts folders by number. | `test_disk_budget` |
| H2 | `modlog.py:39,46-47,140,185-187` | Log image cache cap 150 -> `CACHE_MB` 80 (config can lower it, not raise it past 80). Running `cache_bytes` total: when a save would pass the cap, the oldest copies go at once (to 75 %), not only in the 10-min prune; `disk_ok` before each save. `prune_cache` stats each file once. | `test_disk_budget` (`_log_cache`) |
| H3/H4 | `guard.py:46,339` `busy`, `punish`, `hacked` | Per-member in-flight mark `(user, "punish"/"hacked")`, set before any await and kept 60 s; members already timed out are skipped. Later calls only delete their messages (no card, DM, timeout or extra hit). History popped synchronously before the first await. `tick` drops old marks. | `test_guard_handles_a_member_once` |
| H5 | `guard.py:482` `start_lockdown`, `:566` | Returns False if a lockdown is on; the state is written before `g.edit`, so joins during the edit are added to it. `/lockdown start` says "already on" instead of a 2nd card. | `test_lockdown_starts_once` |
| H6 | `strikes.py:46` | `give_strike` defers first, `followup` at the end. | - (Discord interaction) |
| H6 | `tickets.py:1267` | `/ticket reply` defers (public, thinking) before `relay_staff`, then `followup`. | - |
| H6 | `news.py:46-70` `ConfirmPost` | `done` flag + `stop()`; the first click edits the preview to "Posting..." (buttons gone), then `edit_original_response` with the link, or "Couldn't post" on an HTTP error. Second clicks just ack. Same flag on Cancel. | - |
| M2 | `db.py:47,65,87` | `PRAGMA synchronous=NORMAL` at connect (WAL already on). Connection subclass: `with db.conn():` now opens a real `BEGIN ... COMMIT` (rollback on error), which fixes `names.remember_all` and `transcripts.archive` without touching them. New `db.changes()` returns rowcount; `run/q/one` unchanged. | `test_db_wal_and_transactions` |
| M4 | `staffcmds.py:35` `staff_place` | `?` commands only run where neither @everyone nor the `verified` role can view the channel (plus the existing staff check). | - |
| M5 | `staffapps.py:17,24` `can_decide` | Accept/Deny needs `administrator` or a `founder/owner/co_owner/board` role (layout keys); other staff get "vote with 👍 / 👎". | - |
| M6 | `radar.py:40,150,161` | New default `open_hours: 48` (code, not config). Unconfirmed (`new`) incidents older than that stop absorbing tickets and stop blocking new alerts for the same key. Confirmed (`real`) ones keep collecting until Fixed, so those members still get the "fixed" DM. | `test_radar_old_incidents_and_double_fixed` |
| M7 | `radar.py:182,215` | Fixed / False alarm: conditional `UPDATE ... AND status=?` before any await; only the click that changed the row continues. `ConfirmModal`: only the confirmer whose UPDATE matched adds the issue to the support card. | `test_radar_old_incidents_and_double_fixed` |
| M15 | `core.py:179` `quiet` | All Roblox web helpers (`roblox_user_by_name`, `roblox_user`, `roblox_headshot`, `roblox_group_rank`, `group_info`, `game_votes`, `game_stats`) return None on `ClientError`/`TimeoutError`/`ValueError` (HTML 5xx)/`KeyError`. Defer-first where Roblox ran before the reply: unban button (`tickets.py:365`), /staff "Today's numbers" (`hub.py:151`). Verify's "couldn't find" text mentions Roblox being slow (`verify.py:117`). | `test_roblox_errors_become_none` |
| M21 | `health.py:66` | `CheckFailure` is not logged as an error; if the check already replied ("Staff only.") nothing more is sent; otherwise "You can't use that." | `test_failed_staff_check_replies_once` |

## Not done (and why)
- `config/bot.yaml:125` still says `keep_images_mb: 150`. Out of my scope (config/ is the other agent's); the code clamps it to 80, so it's harmless, but the line should read 80.
- Other Medium/Cleanup items (M1, M3, M8-M14 except the two defers above, M16-M20, M22-M24) left as asked. M20 (application row before the post) is the same bug shape as H1 and is a small follow-up.
- `community.template_add` saves up to 24 MB templates without `disk_ok`; not in the H2 list (ticket files + image copies). One-line follow-up if wanted.

## Tests
Before: `test_guard.py` 7 ok, `test_layout.py` all ok, `test_logs.py` 15 ok. After: `test_guard.py` 7, `test_layout.py` 14, `test_logs.py` 15, new `test_fixes.py` 10, all ok; `python -m compileall -q bot.py asbot` clean.
The dedupe and lockdown tests were checked to fail against the old behaviour.
