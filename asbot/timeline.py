"""One member's history, shown in the /staff member lookup. Quiet things (name changes, voice)
are only kept here; they don't post anywhere. Settings: `logs:` in config/bot.yaml."""
from __future__ import annotations

import time

from . import db
from .core import cfg

db.conn().executescript("""
CREATE TABLE IF NOT EXISTS memberlog (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    kind TEXT NOT NULL, text TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS memberlog_user ON memberlog(user_id, created);
CREATE INDEX IF NOT EXISTS memberlog_created ON memberlog(created);
""")


def settings() -> dict:
    return {"keep_voice_days": 7, "keep_history_days": 90, **(cfg().get("logs") or {})}


def add(user_id: int, kind: str, text: str) -> None:
    db.run("INSERT INTO memberlog(user_id, kind, text, created) VALUES(?, ?, ?, ?)", user_id, kind, text[:200], time.time())


def recent(user_id: int, limit: int = 15) -> list:
    return db.q("SELECT * FROM memberlog WHERE user_id=? ORDER BY created DESC, id DESC LIMIT ?", user_id, limit)


def lines(user_id: int, limit: int = 15) -> str:
    return "\n".join(f"<t:{int(r['created'])}:R> {r['text']}" for r in recent(user_id, limit))


def prune() -> None:
    s, now = settings(), time.time()
    db.run("DELETE FROM memberlog WHERE kind='voice' AND created<?", now - s["keep_voice_days"] * 86400)
    db.run("DELETE FROM memberlog WHERE created<?", now - s["keep_history_days"] * 86400)
