"""Role order and gradients (2026-11-05). Groups read top to bottom: leadership (ember), management
(violet), moderation (blue), team (teal to lime), community (each its own). Platform and ping
roles stay grey. Roles above the bot can't be touched. Run: railway run python tools/roles_style.py
Undo: backups/roles_2026-11-05/roles.json has the old colours, order and hoists."""
import json
import os
import time
import urllib.error
import urllib.request

G = os.environ["GUILD_ID"]
API = "https://discord.com/api/v10"

# name, top colour, bottom colour, shown apart in the member list (None = leave as it is)
STYLE = [
    ("Founder", 0xFF6B6B, 0xFF9E7A, True),
    ("Owner", 0xFF8A65, 0xFFB86B, None),
    ("Co Owner", 0xFFA95C, 0xFFD27A, None),
    ("Admin", 0xFFC857, 0xFFE6A0, True),
    ("Airport Board", 0xB08CFF, 0xD6BCFF, None),
    ("Developer", 0x7F9CFF, 0xB5C4FF, None),
    ("Server Moderator", 0x4FA8FF, 0x8FD0FF, None),
    ("Trial Moderator", 0x7CB8E8, 0xB6DBF5, None),
    ("Support Team", 0x3FD0C9, 0x8DEBD9, True),
    ("Public Relations Team", 0x5FD39A, 0xA8EDC0, None),
    ("Quality Assurance", 0xA5D86A, 0xD5EFA0, None),
    ("ARFF Commissioner", 0xE8735A, 0xF2A48C, None),
    ("Star Program", 0xFFD86B, 0xFFF2B0, None),
    ("Server Booster", 0xF47FFF, 0xB98CFF, None),
    ("Airport VIP", 0xE3C58E, 0xF5E6C4, None),
    ("Livery of the Month", 0xFF7EB6, 0x7ED6FF, None),
    ("Traveler", 0x8FB8D8, 0xC9DDED, None),
]


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": "Bot " + os.environ["DISCORD_TOKEN"], "User-Agent": "DiscordBot (asbot, 1)",
        "Content-Type": "application/json", "X-Audit-Log-Reason": "Role order and gradients"})
    for _ in range(5):
        try:
            return json.load(urllib.request.urlopen(req))
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise SystemExit(f"{method} {path}: {e.code} {e.read()[:300]}")
            time.sleep(json.load(e).get("retry_after", 2) + 0.5)
    raise SystemExit("rate limited")


roles = {r["name"]: r for r in api("GET", f"/guilds/{G}/roles")}
missing = [s[0] for s in STYLE if s[0] not in roles]
if missing:
    raise SystemExit(f"roles not found: {missing}")
top = len(STYLE) + roles["Traveler"]["position"] - 1
api("PATCH", f"/guilds/{G}/roles", [{"id": roles[s[0]]["id"], "position": top - i} for i, s in enumerate(STYLE)])
for name, a, b, hoist in STYLE:
    body = {"colors": {"primary_color": a, "secondary_color": b, "tertiary_color": None}}
    if hoist is not None:
        body["hoist"] = hoist
    api("PATCH", f"/guilds/{G}/roles/{roles[name]['id']}", body)
    time.sleep(0.6)
for r in sorted(api("GET", f"/guilds/{G}/roles"), key=lambda r: -r["position"]):
    c = r.get("colors") or {}
    print(r["position"], r["name"].encode("ascii", "replace").decode(), f"{c.get('primary_color') or 0:06X}", f"{c.get('secondary_color') or 0:06X}", "hoist" if r["hoist"] else "")
