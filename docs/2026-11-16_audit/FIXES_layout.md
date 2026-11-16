# Fixes: layout engine, seed/state, tools, repo tidy (2026-11-16)

Follows `layout_tools_structure.md` (same folder) + `core_cogs.md` M1/M19. Nothing was run against Discord
or Railway; nothing committed (git rm / git mv are staged for review).

## Tests
| | test_layout | test_guard | test_logs | test_sync (new) | test_seed (new) | total |
|---|---|---|---|---|---|---|
| before | 8/8 | 7/7 | 15/15 | - | - | **30/30** |
| after | 15/15 | 7/7 | 15/15 | 4/4 | 5/5 | **46/46** |

Run: `.venv\Scripts\python.exe -B tests\<file>.py`. `compileall -q layout.py asbot tools` clean.
(`tests/test_fixes.py` belongs to the other agent; it also passes 10/10 with these changes.)
pytest was not installed (no package download in this run); every file is pytest-shaped (`test_*` functions,
no fixtures), `requirements-dev.txt` lists it.

## Fixes
| Finding | Where (file:line) | Change | Test |
|---|---|---|---|
| **C1** apply wiped overwrites the yaml doesn't name | `asbot/layout/planner.py:216-243` (`split_overwrites`, `ow_details`), `:261-270`, `:291-296`; `asbot/layout/executor.py:36-42` (`edit_fields`) | The layout owns only `@everyone`, its own non-external roles and targets the channel names (e.g. an external role in `perms_extra`). Everything else (Server Booster, Admin, other bots, members, unknown roles) is never diffed; it rides along in `_keep` and the executor sends `{**keep, **layout}`, because Discord replaces the full list. A stray overwrite for a layout role is still drift (removed). Categories too. `archive` stays exact (by design). | `test_layout.py:186` not drift; `:192` general/media/dev-logs + ENGAGE category survive an apply that rewrites Traveler/@everyone on those channels; `:218` layout-role stray still removed |
| C1 (real backend) member vs role typing | `asbot/layout/discord_backend.py:29-31` (`_is_role`), `:39-55` (`member_targets` in snapshot), `:67-80` (`_get`, `_target` reuses the channel's own target object), `:93,106,128` (`made`) | Kept overwrites go back with the channel's own Role/Member/typed Object, so a user block stays a MEMBER overwrite. Roles/sections made in the same apply are used from `self.made` (also fixes audit M7's cache race for parents + role overwrites). | `test_layout.py:230` `test_discord_backend_keeps_overwrite_types` (stub guild, no network) |
| **H1** manual edit kept for one apply only | `asbot/layout/sync.py:79-86` (`build_plan`), `:102-115` (`applied_record`, `save_applied`), `:229` | `applied_<guild>.json` now records the yaml's values, taken BEFORE `keep_manual_edits` adopts hand edits. A hand-made name/topic stays on every apply until the yaml changes that field. Write is tmp+replace (L3). | `test_sync.py:46` (3 applies in a row keep the hand topic; then a yaml change wins) |
| **H2 / M19** boot overwrote volume `applied_*`, resurrected removed keys | `asbot/paths.py:35-68` (`merge_seed_state`), `:71-84` (`seed_once`) | `applied_*` copied only if the volume has none. Ids: a key is added only if new in the seed (`DATA/state/seeded_<guild>.json` remembers what was offered) and missing on the volume; existing ids never change; a key the volume dropped is not brought back. All writes tmp+replace. | `test_seed.py:47`, `:58`, `:83`, `:95` (real seed/ merges cleanly) |
| **M19 / M1(audit)** `force_*.json` re-forced `ch:devlogs` every boot | `seed/state/force_1347984080842788914.json` (**git rm**); `asbot/paths.py:58-68` | Force file deleted (its value equalled the seed id: no-op). Mechanism kept but one-shot: applied once per file content (`force_<guild>.done` on the volume); fresh-volume copy skips it. Code tolerates its absence. | `test_seed.py:70` (applied once; re-made id not reverted; new content applies once) |
| **H3** no backup, unreadable preview | `asbot/layout/sync.py:141-194` (`backup_of`, `write_backup`), `:197-213` (`describe`), `:220-233` (`run_plan`); `planner.py:123-162` (`flag_names`, `_ow_changes`, `_ow_text`), `:64` (`Action.details`) | Before execute: `DATA/backups/layout_<utc>.json` with the live names/topics/slowmode/tags, every overwrite of each touched channel (id, role/member, name, flag names, raw bits), touched roles, and all positions if the plan reorders; newest 10 `layout_*.json` kept (other backups untouched); a failed write stops the apply. "Done" reply names the file. Preview: `~ #general: perms (@Traveler)` + `    @Traveler: allow -attach_files; deny +mention_everyone` + `    left as they are: @Server Booster, member 123`; flags use the yaml names (`view_channel`, not `read_messages`). | `test_sync.py:63` (preview text, backup content/types/old values, pruning), `:106` (errors: no backup, nothing applied) |
| **M1** (core_cogs) ids JSON parsed on every lookup | `asbot/layout/sync.py:26-63` | `_ids()` caches the parsed file keyed by `(st_mtime_ns, st_size)`; `save_state` drops the entry; `channel_id`/`role_id` use the cache, `load_state` returns a copy (apply mutates it). `core.py` unchanged. | `test_sync.py:120` (50 lookups = 1 parse; own + foreign writes picked up; copy isolation; missing file) |
| **6** `external: true` roles | `asbot/layout/model.py:32,95-98,108`; `planner.py:181-198` | Matched by name/`was` among ALL roles (managed too), never created/edited; missing = preview error. Setting color/hoist/mentionable/permissions on one is a parse error. A Discord-managed role listed WITHOUT `external` is now an error instead of a duplicate create (audit probe d). | `test_layout.py:281`, `:296` |
| **6** per-channel `perms_extra:` | `asbot/layout/model.py:124-162` (`_rules`, `merge_extra`), `:202-203` | Same target syntax as presets (@everyone / group / role key / name). Per target: extra allow/deny win, the preset's other bits stay; a new target gets exactly the extra bits. A flag in both allow and deny is a parse error (presets too; today's yaml passes). | `test_layout.py:264` |
| **M3** PC apply unguarded | `layout.py:1-17`, `:91-103` | `apply` refuses without `--from-pc` (exits before logging in) and points to ARCHITECTURE.md. Added read-only `export --perms` (`:79-88`) to copy exact live flags into `perms_extra`. | not run (hard rule: no layout.py apply/preview) |
| **T1 / M3** selftest read stale PC `state/` | `asbot/paths.py:14-21` (`STATE`); `sync.py:18`; `tools/selftest.py:7-8,24,60-63` | `paths.STATE` = `DATA/state` on Railway / with `ASBOT_DATA`; on the PC (`DATA == ROOT`) = `seed/state` (the copy that ships). sync + selftest + automod_setup all use it. `railway run` on Windows no longer points DATA at `C:\data`. Selftest prints which ids file it used; docstring fixed (it deletes its test post). | covered by `test_seed.py` + `test_sync.py` paths; selftest not run (rule) |
| **7** tidy | git | `git rm tools/cleanup_inbox.py tools/logs_merge.py`; `git mv assets/banners/_sheet.jpg assets/shots/_sheet.jpg` (not referenced by code); deleted untracked root `__pycache__/`, `logs/` (`git ls-files` = 0 for both). `backups/` left in place. | - |
| X5/X6 ignore files | `.gitignore:10-14`, `.railwayignore:13-21` | git: `*.db-wal *.db-shm *.tmp .pytest_cache/ /_local/`. railway: `/_local/ /docs/ /deploy/ *.md *.db-wal *.db-shm *.tmp .pytest_cache/ requirements-dev.txt`. Checked with `git check-ignore`: bot.py, railway.json, requirements.txt, .python-version, config/, layout/, seed/ (ids, applied, bot.db, import), assets/banners/, assets/support_agent.png, asbot/ still ship. No runtime code reads `.md`. | - |
| T2 | `requirements-dev.txt` (new) | `pytest>=8` only, PC only. | - |
| D1/D3 | `ARCHITECTURE.md`, `SETUP.md` | Folders table (paths, cards, ticketlist, faq/ai, names/timeline, tools, tests, deploy/docs); feature table + answers, profile, radar, staffcmds, stats, strikes; commands; new "Layout changes" section (Railway `/layout apply` is the rule, PC `--from-pc` + seed/force steps, what apply owns, external roles, `perms_extra`, backups, seed rules). SETUP: layout + tests + selftest notes. | - |

## Not done (out of scope or not asked)
- `layout/server.yaml` untouched (Kian decides); its header still says `python layout.py apply` and "never deletes"
  (audit L11) - update together with HANDOFF.
- No restore tool / `/layout undo` for the backup, no `/layout apply only:` filter, no `perms_exact:` option,
  no apply lock (L6), no Confirm-vs-preview check (L5): these need `asbot/cogs/admin.py` (other agent's file) or
  were not asked.
- M4 (ARCHIVE name fallback), M5 (forum tags deleted), M6 (role names/moves/order/slowmode not kept),
  L2, L7-L10: unchanged.
- Seed ids content: the 4 dead `ch:log_*` keys + `cat:_archive` are still in `seed/state/ids_*.json`; harmless
  (lookups return a dead id -> None). No tombstone support.
- PC `state/` and `data/` folders left as ignored scratch (`state/` is no longer read by anything).
- HANDOFF.md (lead): line 62 mentions `force_<guild>.json pins ch:devlogs` (now deleted); lines 32-33
  "a full layout apply would REVERT ... booster perks / user block" is no longer true after C1 (Livery colour
  part was already obsolete); "Deploying"/"How layout changes work" can point to ARCHITECTURE.md "Layout changes".

## Deploy-time notes (Railway)
1. No new env vars, no new packages, no start-command change. Volume use: +1 small `seeded_<guild>.json`,
   + up to 10 `backups/layout_*.json` (a few KB each).
2. First boot after deploy: the volume's `applied_<guild>.json` is the stale 2026-10-26 seed copy (every
   earlier boot replaced it) and is now kept as is until the next `/layout apply` rewrites it. Seed ids missing
   on the volume are added one last time (same as every boot before), then `seeded_` stops resurrection.
3. Gate before the first apply: `/layout preview`. It should show no perms lines for #general / #media /
   #dev-logs unless @everyone / Traveler / staff really differ, and each perms line now says exactly what moves
   and what is "left as they are". Read any `kept manual change` lines.
