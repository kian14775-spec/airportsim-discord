"""Staff Duty ledger: who did what work, in points, per ISO week (Monday 00:00 UTC).

Other features only call `award(...)`. Rules (points, daily/weekly caps, departments, targets) live in
config/bot.yaml `duty:`. Everything here is plain logic on SQLite so it is cheap and testable; the cards,
DMs and buttons live in cogs/duty.py.
"""
from __future__ import annotations

import datetime as dt
import time

import discord

from . import db
from .core import cfg, role

SCHEMA = """
CREATE TABLE IF NOT EXISTS duty_points (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    dept TEXT NOT NULL, kind TEXT NOT NULL, pts INTEGER NOT NULL, ref TEXT NOT NULL UNIQUE, at REAL NOT NULL,
    week TEXT NOT NULL, note TEXT, by_id INTEGER);
CREATE INDEX IF NOT EXISTS duty_points_week ON duty_points(week, user_id);
CREATE TABLE IF NOT EXISTS duty_weeks (user_id INTEGER NOT NULL, week TEXT NOT NULL, dept TEXT NOT NULL,
    pts INTEGER NOT NULL, target INTEGER NOT NULL, status TEXT NOT NULL, PRIMARY KEY(user_id, week));
CREATE TABLE IF NOT EXISTS duty_loa (user_id INTEGER PRIMARY KEY, since REAL NOT NULL, until REAL NOT NULL,
    reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS duty_on (user_id INTEGER PRIMARY KEY, since REAL NOT NULL, until REAL NOT NULL);
"""
_ready = False
dirty = True   # set by every change; the board re-renders soon after


def _db():
    global _ready
    if not _ready:
        db.conn().executescript(SCHEMA)
        _ready = True
    return db


def settings() -> dict:
    return cfg().get("duty", {})


# ---------------------------------------------------------------- weeks
def week_of(ts: float | None = None) -> str:
    """ISO week label, e.g. '2026-W47'. Weeks start Monday 00:00 UTC."""
    d = dt.datetime.fromtimestamp(ts if ts is not None else time.time(), dt.timezone.utc)
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_start(week: str) -> float:
    y, w = week.split("-W")
    return dt.datetime.fromisocalendar(int(y), int(w), 1).replace(tzinfo=dt.timezone.utc).timestamp()


def prev_week(week: str, n: int = 1) -> str:
    return week_of(week_start(week) - 7 * 86400 * n + 3600)


def day_of(ts: float | None = None) -> str:
    return dt.datetime.fromtimestamp(ts if ts is not None else time.time(), dt.timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- departments
def departments() -> dict[str, dict]:
    """{'mod': {'name': 'Moderation', 'roles': [...], 'trial': key, 'member': key, 'target': 12, ...}, ...}"""
    return settings().get("departments", {})


def dept_roles(guild: discord.Guild, dept: str) -> list[discord.Role]:
    return [r for k in departments().get(dept, {}).get("roles", []) if (r := role(guild, k))]


def dept_of(member: discord.abc.User) -> str | None:
    """First department whose roles the member has (order in the config = priority), else None."""
    if not isinstance(member, discord.Member):
        return None
    have = {r.id for r in member.roles}
    for key in departments():
        if any(r.id in have for r in dept_roles(member.guild, key)):
            return key
    return None


def members_of(guild: discord.Guild, dept: str) -> list[discord.Member]:
    seen: dict[int, discord.Member] = {}
    for r in dept_roles(guild, dept):
        for m in r.members:
            if not m.bot and dept_of(m) == dept:
                seen[m.id] = m
    return sorted(seen.values(), key=lambda m: m.display_name.lower())


def is_trial(member: discord.Member) -> bool:
    d = dept_of(member)
    t = departments().get(d or "", {}).get("trial")
    r = role(member.guild, t) if t else None
    return bool(r and r in member.roles)


def target(dept: str) -> int:
    return int(departments().get(dept, {}).get("target", 10))


# ---------------------------------------------------------------- awarding
def rule(kind: str) -> dict:
    """{'pts': 2, 'day_cap': None, 'week_cap': None, 'label': 'Ticket closed'}"""
    return settings().get("rules", {}).get(kind, {})


def award(member: discord.abc.User, kind: str, ref: str, *, pts: int | None = None, note: str = "",
          by_id: int | None = None, at: float | None = None, dept: str | None = None) -> int:
    """Give points for one piece of work. `ref` makes it idempotent (same ref = counted once).
    Only department members score. Daily/weekly caps from the rule. Returns the points actually given."""
    d = dept or dept_of(member)
    if not d or not settings().get("enabled", True):
        return 0
    r = rule(kind)
    p = int(pts if pts is not None else r.get("pts", 0))
    if p == 0:
        return 0
    at = at or time.time()
    wk = week_of(at)
    if p > 0:
        if r.get("day_cap") is not None:
            day0 = dt.datetime.fromtimestamp(at, dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            got = _db().one("SELECT COALESCE(SUM(pts), 0) s FROM duty_points WHERE user_id=? AND kind=? AND at>=? AND at<?",
                            member.id, kind, day0.timestamp(), day0.timestamp() + 86400)["s"]
            p = min(p, max(0, int(r["day_cap"]) - got))
        if r.get("week_cap") is not None:
            got = _db().one("SELECT COALESCE(SUM(pts), 0) s FROM duty_points WHERE user_id=? AND kind=? AND week=?",
                            member.id, kind, wk)["s"]
            p = min(p, max(0, int(r["week_cap"]) - got))
        if p <= 0:
            return 0
    if not _db().changes("INSERT OR IGNORE INTO duty_points(user_id, dept, kind, pts, ref, at, week, note, by_id) "
                         "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)", member.id, d, kind, p, ref, at, wk, note, by_id):
        return 0                                    # same ref already counted
    touch()
    return p


def revoke(ref: str) -> bool:
    """Undo one award (e.g. a timeout removed within the hour)."""
    gone = bool(_db().changes("DELETE FROM duty_points WHERE ref=?", ref))
    touch()
    return gone


# ---------------------------------------------------------------- reading
def totals(week: str) -> dict[int, int]:
    return {r["user_id"]: r["s"] for r in _db().q(
        "SELECT user_id, SUM(pts) s FROM duty_points WHERE week=? GROUP BY user_id", week)}


def breakdown(user_id: int, week: str) -> list[tuple[str, int, int]]:
    """[(kind, count, points)] biggest first."""
    return [(r["kind"], r["n"], r["s"]) for r in _db().q(
        "SELECT kind, COUNT(*) n, SUM(pts) s FROM duty_points WHERE user_id=? AND week=? GROUP BY kind ORDER BY s DESC",
        user_id, week)]


def status(pts: int, tgt: int) -> str:
    """green = target hit, amber = at least half, red = under half."""
    if pts >= tgt:
        return "green"
    return "amber" if pts * 2 >= tgt else "red"


def close_week(guild: discord.Guild, week: str) -> list[dict]:
    """Freeze last week's result for every department member (LOA = 'leave'). Safe to run twice."""
    tot = totals(week)
    out = []
    for dept in departments():
        tgt = target(dept)
        for m in members_of(guild, dept):
            st = "leave" if on_leave(m.id, week_start(week) + 3.5 * 86400) else status(tot.get(m.id, 0), tgt)
            _db().run("INSERT OR REPLACE INTO duty_weeks(user_id, week, dept, pts, target, status) VALUES(?, ?, ?, ?, ?, ?)",
                      m.id, week, dept, tot.get(m.id, 0), tgt, st)
            out.append(dict(user_id=m.id, dept=dept, pts=tot.get(m.id, 0), target=tgt, status=st))
    return out


def streak(user_id: int, upto_week: str, want: str) -> int:
    """How many finished weeks in a row (ending at upto_week) had status `want`. 'leave' weeks are skipped."""
    n, wk = 0, upto_week
    for _ in range(26):
        row = _db().one("SELECT status FROM duty_weeks WHERE user_id=? AND week=?", user_id, wk)
        if not row:
            break
        if row["status"] == "leave":
            wk = prev_week(wk)
            continue
        if row["status"] != want:
            break
        n += 1
        wk = prev_week(wk)
    return n


# ---------------------------------------------------------------- leave + on duty
def set_leave(user_id: int, days: int, reason: str) -> float:
    until = time.time() + days * 86400
    _db().run("INSERT OR REPLACE INTO duty_loa(user_id, since, until, reason) VALUES(?, ?, ?, ?)",
              user_id, time.time(), until, reason)
    touch()
    return until


def end_leave(user_id: int) -> bool:
    gone = bool(_db().changes("DELETE FROM duty_loa WHERE user_id=?", user_id))
    touch()
    return gone


def on_leave(user_id: int, at: float | None = None) -> dict | None:
    row = _db().one("SELECT * FROM duty_loa WHERE user_id=? AND since<=? AND until>?", user_id,
                    at or time.time(), at or time.time())
    return dict(row) if row else None


def go_on_duty(user_id: int, hours: float | None = None) -> float:
    until = time.time() + (hours or float(settings().get("duty_hours", 2))) * 3600
    _db().run("INSERT OR REPLACE INTO duty_on(user_id, since, until) VALUES(?, ?, ?)", user_id, time.time(), until)
    touch()
    return until


def go_off_duty(user_id: int) -> bool:
    gone = bool(_db().changes("DELETE FROM duty_on WHERE user_id=?", user_id))
    touch()
    return gone


def on_duty_ids() -> list[int]:
    _db().run("DELETE FROM duty_on WHERE until<=?", time.time())
    return [r["user_id"] for r in _db().q("SELECT user_id FROM duty_on ORDER BY since")]


def touch() -> None:
    global dirty
    dirty = True


# ---------------------------------------------------------------- for cards
def is_lead(member: discord.abc.User) -> bool:
    """Leads run the weekly check-ins (config duty.leads = role keys). Server admins always count."""
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator:
        return True
    want = {r.id for k in settings().get("leads", []) if (r := role(member.guild, k))}
    return any(r.id in want for r in member.roles)


def label(kind: str) -> str:
    return rule(kind).get("label") or kind.replace("_", " ").capitalize()


def bar(pts: int, tgt: int, width: int = 10) -> str:
    filled = width if not tgt else min(width, round(width * pts / tgt))
    return "█" * filled + "░" * (width - filled)


def week_lines(user_id: int, week: str) -> list[str]:
    """['3 x Ticket solved: 6', ...] in plain words, biggest first."""
    return [f"{n} x {label(k)}: {p}" for k, n, p in breakdown(user_id, week)]


def team(guild: discord.Guild) -> list[tuple[str, discord.Member]]:
    """(department, member) for everyone who scores, departments in config order."""
    return [(d, m) for d in departments() for m in members_of(guild, d)]


def to_go(user_id: int, dept: str, week: str) -> int:
    return max(0, target(dept) - totals(week).get(user_id, 0))
