# Discord: logs + security revamp, server check, new ideas (2026-10-31)

## 1. What's built (LIVE since 2026-10-31, selftest 54/54)

**Logs** - the #logs channel with threads is replaced by a **LOGS** category (staff can read, only the bot posts):

| Channel | What lands there |
|---|---|
| security-logs | raids + lockdowns, hacked accounts, scams, anti-nuke, bots/webhooks/integrations added, anyone handed admin-type perms, Discord's own raid alerts |
| mod-logs | bans, kicks, timeouts (who + reason), strikes, auto-timeouts, AutoMod hits |
| member-logs | joins (account age, **which invite**, spammer flag), leaves (roles they had), verifies, roles, nicknames, name/avatar changes, boosts |
| message-logs | deleted messages (**who deleted it**, text even after a bot restart, **images re-posted**), edits, purges as a .txt, ghost pings, pins |
| server-logs | channels, permissions (exact allowed/denied diff), roles, invites, emojis, threads, events, server settings, all with who did it |
| voice-logs | joins/leaves/moves, server mutes and disconnects by staff |
| ticket-logs | tickets, transcripts, ratings, applications |

Every card: member name + avatar on top, what happened, "by @staff · reason", ids + time at the bottom.

**Security (guard)** - automatic, staff are never touched:
- **Spam**: 6 msgs in 6 s, same message 3x, fake links (dlscord-gift, rob1ox.com.ge...) -> delete + timeout 10 min -> 1 h -> 24 h on repeats
- **Hacked accounts**: same link/image in 3 channels, scam text, @everyone + link -> delete all of it, 24 h timeout, DM on how to secure the account, card with Remove timeout / Ban, staff pinged
- **Raids**: 10 joins/min or 6 brand-new accounts in 5 min -> invites + member DMs paused 30 min (Discord's own switch, lifts by itself), staff pinged, End lockdown / Kick unverified joins buttons. `/lockdown start|end` by hand
- **Anti-nuke**: anyone deleting 3+ channels/roles/webhooks or banning 5+ in a minute loses their dangerous roles at once (bots get kicked), owner DM'd
- **Pings**: members pinging senior staff (not in the convo) get a 20 s "use support" nudge; 3 in 30 min = timeout
- **AutoMod**: 3 blocked messages in 10 min = timeout

## 2. Server check: security gaps (your call)

1. **Trial Moderator can ban, Server Moderator can't** - backwards. Move Ban Members to Server Moderator.
2. **Role `*` (Administrator, 3 people) is above the bot** - if one of those accounts gets hacked, nothing can stop it. Drag the bot's role to the very top.
3. **37 invites**, most made by random members, never expiring; the main one (3,216 uses) points at #staff-chat. Turn off Create Invite for members, keep 1-2 official invites pointing at rules-and-verify.
4. **Webhook "Airport Firefighting Simulator" in #announcements** (made by minwww) - anyone with its URL can post @everyone there. Delete it if it's not used.
5. ~~AutoMod~~ **DONE**: alerts now go to #mod-logs, name filter cleaned + on, @mention typo fixed, new Server invites (allowed in rp-servers) + Scam links rules. Only "Block Mention Spam" (made by an old app in 2022) refuses edits through the API: the bot now times out 8+ pings itself, or set its limit to 8 in Server Settings > AutoMod.
6. **Deleted roles came back**: ARFF Commissioner (1 member), Leg (1), Unverified (0).
7. **ARCHIVE** still has 15 old ticket-#### channels from the old Ticket Tool bot (NOT in the bot's transcripts). Backed up to the bot repo `backups/cleanup_2026-10-31/`; delete on your OK.
8. **Layout drift**: someone set #general/#media perms by hand (booster perks; it also blocked ALL slash commands for verified members there, which is why /help "didn't work" - FIXED, commands allowed again), a user is blocked from dev-logs, and the Livery of the Month colour changed. A full layout apply would undo these: keep them (add to the layout) or revert?
9. ~~Old #logs~~ **DELETED** (23,626 messages backed up in `backups/cleanup_2026-10-31/`).

## 3. New systems (automated, minimal, nothing to click)

Top picks first. All free, no AI, tiny on the Railway credit.

1. **Live player count** - a locked channel at the top: `Playing now: 1,234`, updates every 10 min from Roblox. Server looks alive at a glance.
2. **Update radar** - the bot notices when the game is updated on Roblox and posts an "Update is live" card in dev-logs (devs fill the notes with one button), pings Development. No update ever goes unannounced.
3. **Frequent Flyer tiers** - airport-themed trust levels earned by being around (days active, verified, helpful): Economy -> Premium -> Business -> First. Quiet role, no level-up spam. Doubles as security: Economy can't post links/images, so raid and scam accounts are harmless.
4. **Verify nudge** - 651 members (31%) never verified. One DM 24 h after joining with a Verify button.
5. **Shot of the week** - the bot picks the most-liked screenshot in media each week, posts it in socials crediting the member, gives a 7-day role. Free content + reason to post.
6. **Discord <-> game** (needs the Open Cloud key): verified/booster perks in game, and ban sync (exploiter banned in Discord = banned in game, one button).
7. **Events in one command** - `/event` -> Discord event with banner, RSVP, 1 h reminder and a recap post; player airlines can request flights.
8. **Auto status** - when Roblox itself is down, the support card turns amber with "Roblox is having issues" so members stop opening tickets.
