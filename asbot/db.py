"""Small SQLite store for links, tickets, panels and votes (data/bot.db)."""
from __future__ import annotations

import json
import sqlite3
import time

from .paths import DATA

DB_FILE = DATA / "data" / "bot.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS links (discord_id INTEGER PRIMARY KEY, roblox_id INTEGER NOT NULL,
    roblox_name TEXT NOT NULL, linked_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS pending_verify (discord_id INTEGER PRIMARY KEY, roblox_id INTEGER NOT NULL,
    roblox_name TEXT NOT NULL, code TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    thread_id INTEGER, topic TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
    claimed_by INTEGER, created REAL NOT NULL, first_reply REAL, closed REAL, rating INTEGER);
CREATE TABLE IF NOT EXISTS panels (name TEXT PRIMARY KEY, channel_id INTEGER NOT NULL, message_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS applications (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    position TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', message_id INTEGER, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS parties (channel_id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS shipped (thread_id INTEGER PRIMARY KEY, notified REAL NOT NULL);
CREATE TABLE IF NOT EXISTS rp_posts (owner_id INTEGER PRIMARY KEY, thread_id INTEGER NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS rp_stale (thread_id INTEGER PRIMARY KEY, warned REAL NOT NULL);
CREATE TABLE IF NOT EXISTS relay_map (src INTEGER PRIMARY KEY, dst_channel INTEGER NOT NULL, dst INTEGER NOT NULL,
    ticket_id INTEGER NOT NULL, side TEXT NOT NULL);
"""

# Columns added after the first release (ALTER is skipped when they already exist)
MIGRATIONS = [
    "ALTER TABLE tickets ADD COLUMN last_activity REAL",
    "ALTER TABLE tickets ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'",
    "ALTER TABLE tickets ADD COLUMN nudged INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE tickets ADD COLUMN warned INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE tickets ADD COLUMN transcript_channel INTEGER",
    "ALTER TABLE tickets ADD COLUMN transcript_message INTEGER",
    "ALTER TABLE applications ADD COLUMN thread_id INTEGER",
    "ALTER TABLE tickets ADD COLUMN handed REAL",   # when a person took over from the support helper
]

_conn: sqlite3.Connection | None = None


class _Conn(sqlite3.Connection):
    """Autocommit, except `with db.conn():` which is one real transaction (one write to disk,
    all or nothing). Plain autocommit never opens one, so each row would commit on its own.
    Keep the block free of awaits: other coroutines' writes would land in it."""

    def __enter__(self):
        if not self.in_transaction:
            self.execute("BEGIN")
        return super().__enter__()


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_FILE, isolation_level=None, factory=_Conn)   # autocommit
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")   # safe with WAL; no fsync on every single write
        _conn.executescript(SCHEMA)
        for sql in MIGRATIONS:
            try:
                _conn.execute(sql)
            except sqlite3.OperationalError:   # column already there
                pass
    return _conn


def q(sql: str, *args) -> list[sqlite3.Row]:
    return conn().execute(sql, args).fetchall()


def one(sql: str, *args) -> sqlite3.Row | None:
    return conn().execute(sql, args).fetchone()


def run(sql: str, *args) -> int:
    return conn().execute(sql, args).lastrowid


def changes(sql: str, *args) -> int:
    """Like run(), but returns how many rows changed: 0 = someone else got there first."""
    return conn().execute(sql, args).rowcount


def now() -> float:
    return time.time()


def kv_get(key: str, default=None):
    row = one("SELECT value FROM kv WHERE key=?", key)
    return json.loads(row["value"]) if row else default


def kv_set(key: str, value) -> None:
    run("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        key, json.dumps(value))


def panel(name: str) -> sqlite3.Row | None:
    return one("SELECT * FROM panels WHERE name=?", name)


def set_panel(name: str, channel_id: int, message_id: int) -> None:
    run("INSERT INTO panels VALUES(?, ?, ?) ON CONFLICT(name) DO UPDATE SET channel_id=excluded.channel_id, "
        "message_id=excluded.message_id", name, channel_id, message_id)
