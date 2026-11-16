# Airport Simulator Discord: restructure plan (2026-11-16)

BUILD STATUS (2026-11-16, Kian: "add everything"): BUILT + offline-tested, NOT deployed.
- Built: 1 staff baseline report (posted once in #leads, and /staff > Staff Duty), 2 Staff Duty v1, 3 departments
  (STAFF rooms: #leads, #mod-room, #creators-room; Support Team folded into Moderation by config, no roles deleted),
  4 verify nudge, 5 live player count + question of the day + poll reminder + shot of the week + boost card,
  6 on-duty ticket routing, 7 bug-to-fix loop (main server), 8 event engine, 9 creator pipeline.
- Not built: QA hub bridge (needs the bot invited to the QA hub), 10 in-game perks (Open Cloud key),
  11 Frequent Flyer tiers, 12 Trello push, native onboarding (a Discord setting, Kian's call), staff website (not recommended).
- Department ladders use the roles that exist (Trial Moderator -> Server Moderator; QA + PR have no Trial role yet).

The original proposal follows.
Hard rule for everything below: $0. It all runs inside the bot that already lives on Railway (no new service, no paid API, no AI).

---

## 1. Departments: where staff live

**Pick: a hybrid.** Mod and PR/creators move into a smart STAFF section in the main server. QA keeps its own QA hub. The dead staff hub gets archived.

Why:
- The staff hub already died. Separate staff servers die because nobody opens them: mods have to be in the main server anyway.
- The QA hub is still used because QA needs its own space: test builds, leaks and many testers who aren't staff. Don't break the one hub that works.
- One bot, both servers: the same bot joins the QA hub for free and only counts activity and bridges bugs (section 5). No layout changes there.

Main server STAFF section (each department sees only its own room):

```
STAFF
  staff-chat        all staff
  staff-board       live roster + weekly report (bot, read-only)   <- merges into #stats
  inbox             tickets (as now)
  applications      as now
  mod-room          Moderation only
  creators-room     PR / creators only
LOGS (as now: alerts / actions / activity)
```

Roles become 3 departments with the same ladder (Trial -> Member -> Lead):

| Department | Trial | Member | Lead |
|---|---|---|---|
| Moderation (tickets + chat) | Trial Moderator | Server Moderator (+ Support Team folded in) | Head of Moderation |
| QA | QA Trial | QA | QA Lead |
| PR / creators | PR Trial | PR Team (+ Star Program) | PR Lead |

- Fold Support Team into Moderation: one department handles tickets and chat. That's fewer roles and one quota.
- Leads run their own department's weekly review (section 2). Kian and the board only see escalations.

## 2. Staff accountability: the Staff Duty system (the big one)

Problem: people join staff, do nothing, and nobody notices. Fix: the bot counts real work by itself, shows it every week, and flags people early. No forms and no self-reporting.

**Points are counted from data the bot already sees:**

| Department | Counts as work | Points |
|---|---|---|
| Moderation | ticket closed (member replied, not deleted) | 2 |
| | first reply under 15 min | +1 |
| | mod action with a reason (warn, timeout, ban: from the audit log) | 1 (max 5 a day) |
| | active day in public chat (5+ messages) | 1 (max 5 a week) |
| QA | bug filed in the QA hub | 2 |
| | bug confirmed by another tester or a dev | +3 |
| | attended a test session (voice/event attendance) | 4 |
| PR / creators | video or short posted (YouTube auto-detected, section 5) | 6 |
| | event hosted (section 5) | 6 |
| | social post shared in #socials | 2 |

The weekly target per department is set in `config/bot.yaml` (start: Mod 12, QA 10, PR 10). The numbers are starting points and get tuned after 2 weeks of real data.

**Weekly cycle (all automatic):**
- **Monday 00:00 UTC:** the staff-board card updates. One line per person: points, a bar, and green (target hit), amber (50-99 %) or red (under 50 %). Each staff member gets a short DM with their week ("8/12: 3 tickets, 2 actions. 4 to go.").
- **Thursday:** a nudge DM only to people under 40 % that week. Nobody else gets a ping.
- **2 red weeks in a row:** a review card goes to the department lead with buttons [Give LOA] [Final warning] [Demote] [Keep]. **The bot never demotes on its own.** A human clicks, and the click is logged in #actions.
- **4 green weeks in a row as Trial:** a "ready for promotion" card [Promote] [Not yet].
- **LOA:** `/loa 14d exams` pauses tracking, shows "on leave" on the board and ends by itself.

**Anti-gaming:**
- Daily caps on actions and chat.
- Tickets closed with no member reply score 0.
- A timeout removed within 1 h doesn't count.

**First step (one session, no risk):** a read-only **baseline report** of the last 30 days: tickets per staff member, audit-log actions per staff member, last message date. It shows on day one who never contributes, with data instead of feelings.

## 3. Tooling: Trello, a website, or Discord-native?

**Pick: Discord-native (a Staff Board built into the bot).** Not Trello, not a website.

| | Discord-native board | Trello | Own website/dashboard |
|---|---|---|---|
| Cost | $0 (same process) | $0, but the free plan is **max 10 collaborators per workspace** and boards go view-only above that | Needs an always-on web server + Discord login. Railway bills RAM at **~$10/GB-month** and the bot already uses $0.5-0.85 of the $1 credit |
| Will staff open it? | It's where they already are | Another app = the dead-hub problem again | Another tab nobody opens |
| Data | The bot already has tickets, actions, chat | Needs syncing both ways + webhooks (public endpoint) | Needs an API + login + security |
| Build effort | Medium | Medium | High, plus upkeep |

- Trello fits in one place only: a public **dev roadmap**, if the devs already use Trello. Then it's a one-way push of Planned/Done ideas from the forum. Optional and later.
- If a website is ever needed: a read-only static page on a free static host, generated from the board. Not now.

## 4. Getting the server active

Diagnosis:
- **31 % of members never verified** (651 of ~2,100 on 2026-10-31), so they can't even see chat.
- There's nothing new to talk about most days, and no reason to come back.

**Chat**
- **Verify nudge:** a DM 24 h after joining with a Verify button. If Kian adds the Open Cloud key, verified members get a small in-game reward. That's the cheapest way to win back ~650 people.
- **Live player count** as a locked channel name at the top ("Playing now: 1,234"). The server looks alive at a glance.
- **Daily topic:** at peak hour the bot posts one question in #general from a list of ~100 in the config ("Best livery in the game?", "Which airport next?"). It skips the post if chat was busy in the last 30 min, so it never interrupts.
- **Weekly native poll** in #dev-logs (next airport, next plane, next feature). The bot reminds the devs and posts the result. Polls get replies.
- **Discord-only sneak peeks** in #dev-logs (and codes, if the game ever has them). This is the main reason people join a game's Discord.
- **Shot of the week** (most-liked screenshot in #media, credited in #socials, 7-day role). Livery of the Month already runs.

**Boosts**
- Discord levels: 2 boosts = level 1, **7 boosts = level 2 (needed for a server banner and role icons)**, 14 = level 3.
- **Biggest lever: an in-game booster perk** (Open Cloud key): exclusive livery or tag. Roblox players boost for in-game stuff, not for Discord perks.
- **Boost goal card:** "3 more boosts unlock the server banner". There's also a thank-you card in #general when someone boosts.

**Events + automations that keep it alive**
- A weekly in-game event (group flight, rush hour) run by PR. The bot handles the Discord event, reminders, the ping role and a recap thread for screenshots. The host earns Duty points.
- A monthly "Ask the devs" thread: the bot collects the top-voted questions for the devs.

## 5. New bot systems (my direction)

Theme: **the server runs itself.** Every system feeds another one: work becomes points, reports become fixes, fixes become news.

1. **Staff Duty** (section 2): accountability with zero paperwork.
2. **Bug-to-fix loop:**
   - QA gets [Repro] [Can't repro] [Duplicate] buttons on bug posts, and those clicks earn QA points.
   - Devs tag a bug "Fixed next update".
   - When the existing **update radar** sees the new game version, the bot closes those posts and DMs each reporter "your bug was fixed in today's update".
   - It also bridges the QA hub: "send to devs" carries a QA report into the main forum.
   - Players see their reports matter, so they report more and chat more.
3. **On-duty routing:**
   - `/duty on` makes a staff member available for tickets, and new tickets ping only on-duty staff (not everyone).
   - The support card shows "2 staff on duty · usual reply 12 min".
   - Duty hours feed the Staff Duty points.
4. **Event engine:**
   - `/event` creates the Discord event: banner from the new set, RSVP, 1 h reminder, attendance from voice.
   - It also posts the recap.
   - The host earns points.
5. **Creator pipeline:**
   - `/creator link` takes a YouTube channel.
   - The bot checks its free RSS feed every 30 min (no API key). A new Airport Simulator video is auto-posted in #socials with credit, plus PR points.
   - It ends with a monthly "top creator" post.
6. **Frequent Flyer tiers:**
   - Quiet trust roles earned from *days active*, not message count (no spam).
   - The lowest tier can't post links or images, so raid and scam accounts can't do damage.
7. **Native onboarding:** move the role menus into Discord's own Onboarding questions. That's free, there are fewer bot parts, and Discord shows it to every new member.

Cost check: all of this is a few DB rows and a few cheap timed checks inside the existing bot. The biggest one is the RSS poll: one small request per linked creator every 30 min.

---

## PICK LIST (ranked by impact vs effort)

| # | Pick | Impact | Effort | Needs from Kian |
|---|---|---|---|---|
| 1 | **Staff baseline report** (last 30 days, who does nothing) | High | S (1 session) | nothing |
| 2 | **Staff Duty v1**: points from tickets + audit log, weekly board, DMs, LOA, review/promotion cards | Very high | M (1-2 sessions) | weekly targets OK |
| 3 | **Departments in the main server**: STAFF rooms + dept roles, fold Support Team into Mod, archive the dead staff hub | High | S | role names OK, dead hub archive OK |
| 4 | **Verify nudge** (+ in-game reward later) | High | S | – (reward: Open Cloud key) |
| 5 | **Live player count + daily topic + weekly poll** | Med-High | S | topic list OK |
| 6 | **On-duty ticket routing** | Medium | S-M | – |
| 7 | **Bug-to-fix loop + QA hub bridge** (QA points) | High | M | invite the bot to the QA hub |
| 8 | **Event engine** | Med-High | M | who hosts |
| 9 | **Creator pipeline** (YouTube RSS) | Medium | S-M | – |
| 10 | **In-game booster + verify perks** | High (boosts) | M | **Open Cloud key** + a small game change |
| 11 | Frequent Flyer tiers | Medium | M | – |
| 12 | Trello roadmap push | Low | S | only if devs use Trello |
| – | Own staff website | Low | L | **not recommended** (cost + nobody opens it) |

Suggested order: 1 -> 3 -> 2 -> 4 + 5 -> 6 -> 7. The first three fix the staff problem within about a week of real data.

Sources: [Trello collaborator limit](https://support.atlassian.com/trello/docs/workspace-user-limit), [Railway pricing](https://docs.railway.com/pricing/plans).
