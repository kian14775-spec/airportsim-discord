"""Small SQLite store for links, tickets, panels and votes (data/bot.db)."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_FILE = ROOT / "data" / "bot.db"

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
"""

_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_FILE.parent.mkdir(exist_ok=True)
        _conn = sqlite3.connect(DB_FILE, isolation_level=None)   # autocommit
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
    return _conn


def q(sql: str, *args) -> list[sqlite3.Row]:
    return conn().execute(sql, args).fetchall()


def one(sql: str, *args) -> sqlite3.Row | None:
    return conn().execute(sql, args).fetchone()


def run(sql: str, *args) -> int:
    return conn().execute(sql, args).lastrowid


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
