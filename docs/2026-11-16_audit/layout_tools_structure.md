# Audit: layout engine, tools, tests, repo structure (2026-11-16)

Read-only audit. Scope: `layout.py`, `asbot/layout/*`, `asbot/paths.py` (seed), `layout/server.yaml`,
`config/bot.yaml` (schema only), `seed/`, `state/`, `tools/`, `tests/`, `deploy/`, ignore files,
`railway.json`, `requirements.txt`, `SETUP.md`, `ARCHITECTURE.md`. Bot features (`bot.py`, `asbot/*.py`,
`asbot/cogs/`) are covered by the other audit; only cross-overs are noted here.

Method: read every file in scope; ran the offline tests; ran offline probes of the planner/executor/
keep_manual_edits against the tests' `FakeBackend` (scratch script outside the repo, nothing connects to
Discord). Live overwrites could not be seen (no Discord access by rule), so claims about the live server are
from code + HANDOFF + the 2026-10-25 backups.

Side effect noted and undone: opening `seed/data/bot.db` / `data/bot.db` read-only created empty
`bot.db-wal` / `bot.db-shm` next to them (the DBs are in WAL mode). I deleted those 4 files again;
`git status` is clean. (This is itself finding X8 below.)

---------------------------------------------------------------------------------------------------

## 0. Results at a glance

**Tests:** `.venv\Scripts\python.exe -m pytest tests -q` -> `No module named pytest` (pytest is not in the
venv or any requirements file). Run as scripts (`python -B tests/<file>.py`, which every file supports):
**30/30 pass** - test_layout 8/8, test_guard 7/7, test_logs 15/15.

**Counts**

| Area | Critical | High | Medium | Low |
|---|---|---|---|---|
| Layout engine + seed/state | 1 | 3 | 7 | 11 |
| Tools | - | - | 1 | 5 |
| Tests | - | - | 1 | 1 |
| Hygiene + ignore files | - | - | - | 8 |
| Docs out of date | - | - | - | 7 |
| **Total** | **1** | **3** | **9** | **32** |

Plus 2 dead tools to delete (`cleanup_inbox.py`, `logs_merge.py`), not counted as defects.

**Top 5**
1. **C1** A `/layout apply` replaces the WHOLE overwrite list of every channel whose perms differ, so every
   overwrite for a role not in the yaml (Server Booster, Admin, Star Program, PR, QA, VIP, Ticket
   Blacklisted, other bots) and every member overwrite is deleted. HANDOFF's #general/#media/#dev-logs risk
   is real (proven offline). Livery colour part is no longer true.
2. **H1** "Keep manual edits" only survives one apply: the adopted manual value is written into
   `applied_*.json`, so the *next* apply reverts it (proven offline).
3. **H2** Every boot copies `seed/state/applied_*.json` (2026-10-26) over the volume's copy, so after any
   Railway restart the "last applied" record is stale; the 3 log channels and the LOGS section have no
   protection at all.
4. **H3** Apply is all-or-nothing, the preview says only `perms (id:123456)`, and nothing is backed up before
   an on-Railway apply - the admin cannot see or undo what C1 does.
5. **M3/M1/M2** State drift: PC `state/` = `seed/state/` = 2026-10-31 copy; the volume is the truth but
   nothing can pull it down; `layout.py apply` still runs from the PC; `force_*.json` re-forces
   `ch:devlogs` every boot; seed re-adds keys removed on the volume and still carries 4 dead log keys.

---------------------------------------------------------------------------------------------------

## 1. HANDOFF known risk: "a full `layout apply` would revert ..."

| Claim (HANDOFF.md:32-33) | Still true? | Why |
|---|---|---|
| Hand-made booster perks on #general / #media reverted | **TRUE** | Server Booster is a managed role: `planner.py:133` excludes managed roles from matching, so its overwrite shows up as `id:<n>` (`planner.py:156-157`), counts as drift (`planner.py:282-284`) and the executor writes only the layout's targets (`executor.py:59-61`, `discord_backend.py:116-117` -> `ch.edit(overwrites=...)` replaces the full list). Also, if the perk scheme changed Traveler's own overwrite (e.g. denies attach/embed for non-boosters, or the s21b `use_application_commands` allow), Traveler is reset to preset `members`, which ALLOWS attach_files + embed_links -> every verified member gets the perk. |
| User block on #dev-logs reverted | **TRUE** | Member overwrites are never in the layout -> `id:<user>` -> deleted. Probe (a): plan `~ #dev-logs: perms (id:6000)`; after apply the block is gone. |
| Livery role colour reverted | **NO (obsolete since b256ab8, 2026-11-05)** | `planner.py:143` now diffs only name / mentionable / permissions. Colours + hoist + order belong to `tools/roles_style.py`. `server.yaml:25 color: "#F1C40F"` is only used if the role is missing and gets created. |
| (not in HANDOFF) other things a full apply reverts | - | hand role renames (M6), hand channel moves / order / slowmode (M6), hand-added forum tags are DELETED (M5), hand name/topic edits on the 2nd apply (H1), any rename/topic of the 3 log channels or the LOGS section (H2), any overwrite on a category for a non-layout role (C1, categories too: `planner.py:177-178`). |

Bot role "Airport Simulator" has Administrator (backup 2026-10-25), so C1 can not lock the bot out.

How to encode the hand-made perms safely: section 8.

---------------------------------------------------------------------------------------------------

## 2. Layout engine defects (`asbot/layout/`, `layout.py`, `asbot/cogs/admin.py` as its UI)

### C1 (Critical) - non-layout overwrites are wiped
- `asbot/layout/planner.py:156-157` (`have_overwrites` maps unknown targets to `id:<n>`), `:177-178`
  (categories), `:282-284` (channels); `asbot/layout/executor.py:51,61`; `asbot/layout/discord_backend.py:116-117`.
- Defect: the diff compares the full live overwrite map with the yaml map, and the fix writes the yaml map as
  the complete list. Any target the yaml does not name is treated as drift and removed.
- Failure: Kian adds one channel to the yaml and runs `/layout apply`. The same plan also carries
  `~ #general: perms (id:...)`, `~ #media: perms (id:...)`, `~ #dev-logs: perms (id:...)`; confirming wipes
  booster perks and the user block. Same for any future per-member mute/block, Ticket Blacklisted denies,
  other bots' overwrites.
- Fix: make the layout own only the targets it names. In `_channel_diff` / the category diff compare only
  keys that are `@everyone` or layout role keys; pass the live unknown overwrites along (`diff["_keep"] =
  {id: (allow, deny, type)}`) and in the executor send `{**keep, **resolve(want)}`. The snapshot must record
  the overwrite type (role/member) so the backend can build `discord.Object(id, type=discord.Role|Member)`
  (discord.py honours `Object.type` on edit: `abc.py:546-547`). Add opt-in `perms_exact: true` per
  channel/section for places where strays should be stripped (STAFF, LOGS). Keep the `archive` op exact.
  Test: `test_unlisted_overwrites_survive`.

### H1 (High) - keep_manual_edits protects for one apply only
- `asbot/layout/sync.py:85-93` (`adopt` does `setattr(spec, f, have)` on the loaded layout) +
  `sync.py:123` (`save_applied(guild.id, layout)` saves that mutated layout).
- Defect: after an apply that contains any other change, `applied_*.json` holds the manual value; next time
  `have == was`, so nothing is adopted and the planner reverts to the yaml value.
- Failure (probe b): staff set #media topic by hand -> apply #1 keeps it and records it -> apply #2 plans
  `~ #media: topic` and puts the yaml topic back.
- Fix: `save_applied(guild.id, model.load(LAYOUT_FILE))` (record the yaml's values, not the adopted ones).
  Then a manual value stays adopted until the yaml itself changes. Test: two applies in a row keep it.

### H2 (High) - seed overwrites the volume's applied record on every boot
- `asbot/paths.py:20-21` (`shutil.copy2` of `seed/state/applied_*.json`, unconditional, inside
  `merge_seed_state`, which `seed_once` runs on every start once ids exist: `paths.py:40-42`).
- Defect: docstring says "the PC applies the layout", but HANDOFF.md:41-45 moved applies to Railway. The seed
  copy is 2026-10-26: it has `ch:logs` (deleted) and no `cat:logs_cat`, `ch:log_security`, `ch:log_mod`,
  `ch:log_messages`.
- Failure: `/layout apply` on Railway -> any restart (deploy, crash, watchdog exit, Railway restart policy)
  -> applied record reset to 10-26 -> manual renames/topics of #alerts/#actions/#activity/LOGS are reverted
  on the next apply, and any yaml change made after 10-26 makes `want != was`, so later manual edits on that
  channel are reverted too.
- Fix: copy only if missing: `if not dst.exists(): shutil.copy2(src, dst)`. Better: stop shipping
  `applied_*` in seed (a fresh volume can live without it).

### H3 (High) - all-or-nothing apply, unreadable perms preview, no backup
- `asbot/cogs/admin.py:55-61` + `sync.py:116-126` (one plan, applied whole); `planner.py:116-118` (`_ow_text`
  lists only target keys, e.g. `perms (id:1234, verified)`, never the bits or the target's name); no snapshot
  is written before an on-Railway apply (the PC era had `backups/server_before_*.json`, made by hand).
- Failure: an admin cannot tell "adds one channel" from "removes booster perks"; after a bad apply there is
  nothing to restore from.
- Fix: (1) preview shows `+attach_files -embed_links` per target and names it (`@Server Booster`,
  `member 1234`); (2) `/layout apply only:<keys>` filter on `Plan.actions`; (3) before `execute`, dump
  `snapshot()` (channels, overwrites with types, roles) to `DATA/backups/layout_<utc>.json` on the volume
  (a few KB; volume is 54/500 MB) and a `/layout undo` or a tool that re-applies overwrites from it.

### M1 (Medium) - `force_*.json` re-forces `ch:devlogs` on every boot
- `asbot/paths.py:28-30`; `seed/state/force_1347984080842788914.json` (one-off from s9, 2026-10-25).
- Defect: forced ids win over the volume forever. Today the value equals the seed id, so it is a no-op.
- Failure: if dev-logs is ever re-created by an on-Railway apply, every restart points features back at the
  dead id until the next apply re-matches by name; news posts to dev-logs fail silently in between.
- Fix: `git rm seed/state/force_1347984080842788914.json`; if forcing is ever needed again, make it one-shot
  (record the applied force hash on the volume).

### M2 (Medium) - seed resurrects removed keys and carries dead ones
- `asbot/paths.py:27` (`added = {k: v for k, v in seed.items() if k not in live}`).
- Defect: a key removed on the volume (e.g. `remove_category` pops `cat:x`, `executor.py:74`) is re-added
  from seed at the next boot. Seed ids also still hold `ch:log_members`, `ch:log_server`, `ch:log_tickets`,
  `ch:log_voice` (channels deleted 2026-11-01) and `cat:_archive`.
- Failure: stale ids in `bot_made` (`planner.py:228`) and lookups that return None for keys that look valid.
- Fix: support tombstones (`"key": null` in seed = delete on the volume) and refresh seed/state from the
  volume (M3); drop the 4 dead keys.

### M3 (Medium) - no single source of truth for state; PC apply still allowed
- `asbot/layout/sync.py:17` (`STATE_DIR = DATA / "state"`), `layout.py:40-45`, `tools/selftest.py:55`,
  `tools/automod_setup.py:49`, `tools/logs_merge.py:50`, `tools/cleanup_inbox.py:31`.
- Defect: PC `state/` == `seed/state/` == 2026-10-31 snapshot (byte-identical, checked). The volume is the
  truth (HANDOFF.md:43-45) but there is no way to read it (no `railway ssh` key). PC tools read PC `state/`.
  `layout.py apply` has no guard although HANDOFF says never apply from the PC.
- Failure: an apply from the PC writes PC state that the volume never sees (seed only adds missing keys);
  a channel created on Railway is unknown to selftest/automod_setup; on a fresh volume the stale seed is used.
  Also: `railway run python <tool>` injects `RAILWAY_VOLUME_MOUNT_PATH=/data`, so any tool importing
  `asbot.paths` reads `C:\data\state` (empty) on Windows.
- Fix (zero cost): admin-only `/layout state` that sends the volume's `ids_*.json` + `applied_*.json` as an
  ephemeral attachment -> save into `seed/state/`; `layout.py apply` refuses unless `--from-pc` is passed;
  PC tools read `seed/state/` when `DATA == ROOT`. Then delete PC `state/`.

### M4 (Medium) - name fallback can pull a channel out of ARCHIVE and re-permission it
- `asbot/layout/planner.py:194-196` (state -> same parent by name -> ANY channel by name/`was`) with `was`
  lists that contain names of archived channels (`server.yaml:46,55,63,66,78-79,96,107`: information, verify,
  news, support-center, feedback, suggestions, chat, screenshots, airlines, moderator-only).
- Failure (probe c): `ch:staff_chat` key stale/missing -> planner adopts archived `#moderator-only`, renames it,
  moves it to STAFF and replaces its perms. For `chat`/`news` the archived channel (old history) would be
  made visible to every verified member.
- Fix: skip channels whose parent is the ARCHIVE category in the name fallback, and when a state file exists
  only fall back to names inside the target section; print `! adopting #x from ARCHIVE` in the preview.

### M5 (Medium) - forum tags not in the yaml are deleted
- `asbot/layout/planner.py:273-275`, `asbot/layout/discord_backend.py:130-140`.
- Defect: the tag list is replaced with exactly the yaml list. Renaming a tag in the yaml = delete + create
  (no `was` for tags).
- Failure: staff add a "Fixed" tag to ideas-and-bugs by hand -> next apply removes the tag from the forum and
  from every post that had it.
- Fix: default to add/update only; remove only with `tags_exact: true`; support `"New name<-Old name"`
  renames so tag ids (and posts' tags) survive.

### M6 (Medium) - keep_manual_edits gaps: role names, moves, order, slowmode, require_tag/layout
- `planner.py:143-151` (role name), `:255-261` (parent), `:269-271` (slowmode), `:276-281`, `:288-306` (order);
  `sync.py:96-100` only adopts category names and channel name/topic.
- Failure (probes e, f): staff rename @Traveler -> apply renames it back; staff move #media into CREATE or
  drag sections -> apply moves them back; a raid-time slowmode change is undone. A rename of a role above the
  bot (only `*` today) puts an error in the plan that blocks the whole apply (`planner.py:147-149`).
- Fix: extend `applied_*` to role names, parent, slowmode and order and adopt them like names; at minimum mark
  these lines `REVERTS a hand change` in the preview.

### M7 (Medium) - discord.py cache race right after creating a role or section
- `asbot/layout/discord_backend.py:58-59` (`_target` falls back to `discord.Object(tid)`), `:90`
  (`get_channel(parent_id)`), with discord.py 2.7.1 `guild.py:3800-3802` (`create_role` returns the Role but
  does not cache it), `guild.py` `create_category` (same), `guild.py:1391-1394` (create path types any
  non-Role target as MEMBER), `abc.py:546-547` (edit path types an untyped Object as MEMBER).
- Failure: a yaml change that adds a role and uses it in a new channel's perms in one apply: if the gateway
  event has not landed yet, the overwrite goes out as a *member* overwrite (Discord rejects it or it matches
  nobody) and the new channel is made without its section (fixed later by `reorder`, but its overwrite is not).
- Fix: keep what the backend created (`self.made[id] = obj`) and look there first; build role targets as
  `discord.Object(id, type=discord.Role)` for edits; pass the returned category object to `create_channel`.

### Low - layout engine
- **L1** `planner.py:228` - every `cat:` key counts as "bot-made", including human sections the layout
  adopted by name; the comment "a section people made is never touched" is wrong for those. Only empty ones are
  deleted (`discord_backend.py:143-149` re-checks), so low.
- **L2** `planner.py:215-224`, `executor.py:65-71` - `archive_extras` moves every unmatched channel (staff-made,
  other bots', Discord's rules / community-updates / safety-alerts channels if not in the yaml) and replaces
  all their overwrites. Fix: skip `guild.rules_channel`, `public_updates_channel`, `safety_alerts_channel`
  (record them in the snapshot) and require confirmation per channel.
- **L3** `sync.py:79` (`save_applied`) and `paths.py:31-32` (seed merge) write JSON in place; a crash
  mid-write corrupts the file and every `make_plan` / `core.channel()` raises. Use tmp + replace like
  `save_state` (`sync.py:29-33`).
- **L4** `sync.py:36-42` - `channel_id()` / `role_id()` re-read and parse the JSON on every lookup (every log
  line). Cache with an mtime check.
- **L5** `admin.py:31` -> `sync.py:117` - Confirm re-plans instead of applying what was previewed. Compare the
  action texts and stop if they differ.
- **L6** `sync.apply` / `admin.py` - no lock: two admins (or CLI + Railway) applying at once both create the
  same channels; last writer wins in `ids_*.json`. Module-level `asyncio.Lock` around plan+execute.
- **L7** `planner.py:152-154` - "created bottom-up so they end up in layout order": Discord puts new roles
  just above @everyone, i.e. below Traveler; the tool never reorders roles. Fix the comment or call
  `roles_style`-style ordering.
- **L8** `model.py:186` - `slowmode` is accepted on `announcement` channels, which Discord does not support ->
  API error mid-apply. Validate in `parse`.
- **L9** `model.py:69-71` - `norm()` keeps only `[a-z0-9-]`; names made only of emoji/other scripts all become
  `""` and match each other.
- **L10** `sync.py:49-53` - from the PC, party ids come from the stale PC DB; and every voice channel next to
  the lounge hub is ignored forever (a permanent staff voice room there is never reported).
- **L11** `server.yaml:2-5,14-15,25` - header says to use `python layout.py apply` (HANDOFF forbids it) and
  "The bot never deletes anything" (it deletes empty bot sections and unlisted forum tags); `color:` on
  Livery of the Month is create-only now; many `was:` lists repeat the current name (harmless clutter).

---------------------------------------------------------------------------------------------------

## 3. Seed vs state (structure)

| Path | Tracked | Ships to Railway | Content / role | Verdict |
|---|---|---|---|---|
| `seed/state/ids_<guild>.json` | yes | yes | 47 keys, 2026-10-31; 4 dead `ch:log_*` keys | keep, refresh from volume (M3), drop dead keys (M2) |
| `seed/state/applied_<guild>.json` | yes | yes | 2026-10-26; copied over the volume EVERY boot | H2: copy-if-missing or stop shipping |
| `seed/state/force_<guild>.json` | yes | yes | `ch:devlogs` = same id as ids file | delete (M1) |
| `seed/data/bot.db` | yes | yes | 2026-10-25 DB: 1 ticket, 4 panel message ids, 1 kv; WAL mode | Low: only used for a fresh volume; panel ids are stale. Consider dropping (db.py creates tables) |
| `seed/import/ticket-0001.json` | yes | yes | read by `tickets.py:699-704` on every on_ready (idempotent) | keep or retire once confirmed on the volume |
| `state/` (PC) | no (ignored) | no | byte-identical to seed/state | delete once tools read seed/state (M3) |
| `data/bot.db` (PC) | no (ignored) | no | 2026-11-05 PC copy; selftest writes transcripts into it | PC scratch; move to `_local/` |

---------------------------------------------------------------------------------------------------

## 4. Tools (`tools/`, `deploy/`)

| Tool | Status | Notes |
|---|---|---|
| `cleanup_inbox.py` | **DEAD - delete** | `tools/cleanup_inbox.py:23` imports `_html_row` from `asbot.cogs.tickets`, which no longer exists (ImportError); `:32` needs `ch:transcripts` (channel removed s7). One-off from s6. |
| `logs_merge.py` | **DONE one-off - delete** | s22 rename + delete finished; today it only prints "already renamed / already gone". Git history keeps it. Its idea (targeted change without a full apply) lives on in C1's fix. |
| `automod_setup.py` | keep (ops, idempotent) | Low: reads PC `state/` for `ch:log_mod` / `ch:rp_servers` (stale-state dependency, M3); hard-coded invite codes `:31`. |
| `roles_style.py` | keep (now the only owner of role colours/hoist/order) | Low: needs `railway run` for env; `:27` hard-codes "ARFF Commissioner" (exits if that role is deleted); `:55-56` rewrites a contiguous block above Traveler, shifting any unlisted role inside it. |
| `make_banners.py` | keep (PC only) | Low: Windows fonts; Pillow is not in any requirements file. |
| `selftest.py` | keep | **Medium (T1):** reads PC `state/` (stale) - HANDOFF s23 had to use a temp ASBOT_DATA + seed/state; writes into the PC DB (`:111-114`). Low: docstring `:4-5` says it leaves a "[self-test]" post, code deletes it (`:115`). |
| `deploy/railway_vars.ps1` | keep | Low: syncs only DISCORD_TOKEN, GUILD_ID, ROBLOX_OPENCLOUD_KEY; code also reads `BLOXLINK_KEY` (`core.py:223`) and `GEMINI_API_KEY` (`ai.py:20`). |

---------------------------------------------------------------------------------------------------

## 5. Tests

- **Medium (T2):** no tests for the riskiest code: `sync.keep_manual_edits` / `save_applied` (H1),
  `paths.merge_seed_state` / `seed_once` (H2, M1, M2), unknown-overwrite handling (C1), executor partial
  failure + resume (backend raising mid-plan, `sync.py:121-125`), archive exclusions (M4), forum tags (M5).
- **Low:** pytest is not installed and there is no `requirements-dev.txt`; `tests/test_layout.py:1` tells you
  to run pytest. All files run fine as scripts.
- Stale tests: none fail and none test removed behaviour. `test_three_channels_in_the_layout` matches today's
  yaml.
- **Cogs with zero offline tests:** admin, answers, community, forums, health, hub, news, profile, staffapps,
  staffcmds, stats, strikes, tickets (1,291 lines), verify. Partly tested: guard, auditlog, radar, modlog
  (`small_edit`, `span` only). Untested modules: faq, renamer, panels, transcripts, ticketlist, cards,
  opencloud, ai.
- Cheapest high-value pure targets: `strikes.active/step_text` (1 warn / 1 h / 24 h / 7 d, 30-day expiry),
  `tickets.tag_names/title_of/state_of/queue_position/eta_minutes/known_issue`, `stats.table/short/day_bounds`,
  `faq.match/ranked`, `forums.similar/base_title`, `answers.guess_keywords`, `ticketlist.find`.

---------------------------------------------------------------------------------------------------

## 6. Hygiene and docs

### Hygiene (all Low)
- **X1** root `__pycache__/bot.cpython-312.pyc` (ignored stray) - delete.
- **X2** `logs/bot.log` - from the 2026-10-25 PC run; the bot logs to `DATA/logs` on the volume - delete.
- **X3** `backups/` - 28 MB of message archives (member content) inside the repo folder; ignored by git and
  Railway, but easy to ship by mistake if an ignore file changes. Move to `_local/backups/` or outside the repo.
- **X4** `.env` still has the Oracle-era `DEPLOY_HOST` / `DEPLOY_KEY` lines (values not read); `.env.example`
  lists them too and lacks `ROBLOX_OPENCLOUD_KEY`, `BLOXLINK_KEY`, `GEMINI_API_KEY` (optional), `ASBOT_DATA`.
- **X5** `.railwayignore` ships things Railway never needs: `docs/` (including this audit), `*.md`, `deploy/`,
  `.env.example`, `layout.py`, `assets/banners/_sheet.jpg` (170 KB contact sheet, not referenced by
  `cards.py`). Harmless, but add them.
- **X6** `.railwayignore` / `.gitignore` miss `*.db-wal`, `*.db-shm`, `.pytest_cache/`.
- **X7** `seed/` vs `state/` vs volume: three copies of the same thing, two of them stale (section 3).
- **X8** Opening `seed/data/bot.db` (WAL mode) creates `-wal` / `-shm` in `seed/data/` that show as untracked
  and would ship (seed_once deletes them on the volume after copying, so not harmful, just noise).

### Docs out of date (all Low)
- **D1** `ARCHITECTURE.md:25-39` feature table misses `answers.py`, `profile.py`, `radar.py`, `staffcmds.py`,
  `stats.py`, `strikes.py`; folders table (`:5-23`) misses `cards.py`, `names.py`, `timeline.py`, `faq.py`,
  `ticketlist.py`, `ai.py`, `docs/`, `deploy/`, `assets/`, the other tools; `:23` says tests are layout-only.
- **D2** `ARCHITECTURE.md:41-44` commands miss `/answer add|list|edit|remove|test`, `/profile`,
  `/lockdown start|end`, `/ticket list`, `?` staff commands, context menus (Warn, Warn for this message,
  Save as instant answer, Profile).
- **D3** `ARCHITECTURE.md:51-52` says layout changes = `python layout.py apply` + copy state to seed;
  HANDOFF.md:41-45 says the opposite (Railway `/layout apply`, never from the PC).
- **D4** `HANDOFF.md:47-50` Map: `systemd`, `server_setup.sh`, `deploy.ps1` no longer exist (Oracle era);
  restarts come from Railway's restart policy. `HANDOFF.md:32-33` colour part obsolete (section 1).
- **D5** `SETUP.md:18` selftest command silently uses the stale PC state (M3/T1).
- **D6** `layout/server.yaml:2-5` header (L11).
- **D7** `config/bot.yaml` schema/content: `:92` "besides the layout's Staff role" - there is no `staff` role
  key (it is a group), so `core.py:48 role(guild, "staff")` is always None (other audit); Airport Board is
  staff in the layout group (`server.yaml:38`) but not in `staff_role_names` (`bot.yaml:93`), Admin the other
  way round - two staff definitions; `:115` "# Logs" comment sits above `radar:`; answers text says
  "listed in airlines" (`:58`, channel is rp-servers) and "/template in any channel" (`:56`, only
  `/template_add` exists); application question says "#chat" (`:86`, channel is #general).

---------------------------------------------------------------------------------------------------

## 7. Proposed folder layout (does not touch the start command or imports)

Fixed points (do not move): `bot.py` at the root (`railway.json` startCommand `python bot.py`); package
`asbot/` at the root (`asbot.*` imports; `paths.py:11` and `core.py:16` use `parents[1]`, `sync.py:15` uses
`parents[2]`); `config/bot.yaml`, `layout/server.yaml`, `assets/banners/*.png`, `assets/support_agent.png`,
`seed/` (all read root-relative at runtime); `tools/*.py` and `tests/*.py` stay one level deep
(`parents[1]`).

```
airportsim-discord/
├── bot.py  layout.py  railway.json  requirements.txt  requirements-dev.txt (new: pytest, Pillow)
├── .python-version .gitattributes .gitignore .railwayignore .env.example
├── ARCHITECTURE.md  HANDOFF.md  SETUP.md
├── asbot/            (unchanged)  layout/ cogs/ ...
├── config/bot.yaml
├── layout/server.yaml
├── assets/
│   ├── banners/*.png           shipped
│   ├── support_agent.png       shipped
│   └── shots/  (+ _sheet.jpg)  PC only, railway-ignored
├── seed/                       shipped, fresh-volume data only
│   ├── state/ids_<guild>.json  (refreshed from the volume)
│   ├── state/applied_<guild>.json  (copy-if-missing after H2 fix, or removed)
│   ├── data/bot.db
│   └── import/ticket-0001.json
├── tools/   selftest.py automod_setup.py roles_style.py make_banners.py
├── tests/   test_layout.py test_guard.py test_logs.py (+ test_sync.py, test_seed.py)
├── deploy/  railway_vars.ps1
├── docs/    2026-10-31_logs_security_ideas.md, 2026-11-16_audit/
└── _local/  (git + railway ignored: PC scratch)  backups/  [data/ state/ logs/ with option B]
```

Exact moves - option A (no code change):
1. `git rm tools/cleanup_inbox.py tools/logs_merge.py`
2. `git rm seed/state/force_1347984080842788914.json`
3. `git mv assets/banners/_sheet.jpg assets/shots/_sheet.jpg`
4. Delete local strays: `__pycache__/` (root), `logs/`.
5. `mkdir _local` and move `backups/` -> `_local/backups/` (update the path in `tools/roles_style.py:4`
   docstring only).
6. `.gitignore` += `/_local/`, `*.db-wal`, `*.db-shm`, `.pytest_cache/`.
7. `.railwayignore` += `/_local/`, `/docs/`, `/deploy/`, `*.md`, `.env.example`, `*.db-wal`, `*.db-shm`
   (`/assets/shots/` already covers the moved sheet).
8. Add `requirements-dev.txt` (pytest, Pillow) - PC only, never installed on Railway.
9. Keep `data/` and `state/` at the root as ignored PC scratch until M3 lands, then delete `state/`.

Option B (tidier, one small code change in `asbot/paths.py`): default PC data folder `ROOT / "_local"`
instead of `ROOT` (`paths.py:12`), and change the `seed_once` guard (`paths.py:38`) from `DATA == ROOT` to
"no `RAILWAY_VOLUME_MOUNT_PATH` and no `ASBOT_DATA`". Then move `data/ state/ logs/` into `_local/`.
Railway is unaffected (the volume env var wins); tests already set `ASBOT_DATA`.

---------------------------------------------------------------------------------------------------

## 8. Encoding the hand-made perms safely (order matters)

1. **Read the live values first (read-only).** `layout.py export` lists names only. Add `--perms` (or an
   admin `/layout export`) that prints each channel's overwrites as `target name (role/member): +allow -deny`.
   Copy the exact bits for #general, #media, #dev-logs. Do not guess them.
2. **Land C1 first** (layout owns only the targets it names). After that, booster and member overwrites
   survive without being written into the yaml, and `/layout preview` should show nothing for those 3 channels
   except real differences on @everyone / Traveler / staff.
3. **Encode what the layout should own.** Two small model additions:
   - `external: true` roles: matched by name among ALL roles including managed ones, never created or edited;
     missing = error, not create. (Without this, adding `Server Booster` to `roles:` makes the planner create
     a duplicate role - probe d.)
   - `perms_extra:` per channel, merged on top of the section/channel preset (today a channel `perms:` replaces
     the preset completely, `model.py:179`).

   Shape (bits are placeholders until step 1 gives the real ones):
   ```yaml
   roles:
     - {key: booster, name: Server Booster, external: true}   # Discord's managed role: reference only
   ...
     - key: community
       name: ENGAGE
       perms: members
       channels:
         - key: chat
           name: general
           slowmode: 3
           perms_extra:
             verified: {allow: [use_application_commands]}     # the s21b /help fix, if it is an explicit allow
             booster:  {allow: [attach_files, embed_links]}    # copy the real booster bits
         - key: media
           ...same pattern...
   ```
   If the perk scheme denies attach/embed for Traveler, it needs its own preset (e.g. `members_perks`) instead,
   because preset `members` allows them.
   Leave the #dev-logs user block to step 2 (do not put member ids in the yaml); if it must be explicit, a
   `"user:<id>"` target in `perms_extra` with `discord.Object(id, type=discord.Member)` in the backend.
4. **Tests:** `test_unlisted_overwrites_survive`, `test_external_role_never_created`,
   `test_perms_extra_merges`, `test_manual_topic_survives_two_applies`, `test_seed_keeps_volume_applied`.
5. **Gate:** `/layout preview` must say "Server already matches the layout." (or only intended lines) before
   any `/layout apply`; take the pre-apply backup from H3.

---------------------------------------------------------------------------------------------------

## Appendix - offline probe output (FakeBackend, no Discord)

```
(a) plan: ['~ #dev-logs: perms (id:6000)', '~ #general: perms (id:5000)']
(a) after apply: booster ow on general: False | user block on dev-logs: False
(a2) plan: ['~ #general: perms (verified)']            # one extra bit on Traveler -> reset to preset
(b) apply #1: kept=["kept manual change: media topic = 'Hand-written topic' ..."] changes=[]
     media topic now: 'Hand-written topic'
(b) apply #2: kept=[] changes=['~ #media: topic']
     media topic now: 'Screenshots and clips from the game'
(c) staff_chat now matched to archived id 7001 ["~ #staff-chat: rename 'moderator-only', move to STAFF, perms (...)"]
(d) ['+ role @Server Booster']                         # managed role in roles: -> duplicate created
(e) changes: ['~ role @Passenger: name'] errors: ["Role @Founders is above the bot's role ..."]
(f) ['~ #media: move to ENGAGE', '> put categories and channels in layout order']
```
(b) note: in real use apply #1 runs only when the plan has some other change too; the revert then happens on
the next apply.
