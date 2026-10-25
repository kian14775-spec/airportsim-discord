"""Instant answers ("fix it first") that grow from staff replies.

Built-in answers live in config/bot.yaml; staff add more by right-clicking a reply in a
ticket -> Apps -> "Save as instant answer". Each answer has keywords; the first answer
with the most keyword hits wins. Hits are counted so staff see which ones work.
"""
from __future__ import annotations

from . import db
from .core import cfg

db.conn().executescript("""
CREATE TABLE IF NOT EXISTS faq (id INTEGER PRIMARY KEY AUTOINCREMENT, keywords TEXT NOT NULL, answer TEXT NOT NULL,
    added_by INTEGER, created REAL NOT NULL, hits INTEGER NOT NULL DEFAULT 0);
""")


def entries() -> list[dict]:
    built_in = [{"id": None, "keywords": [k.lower() for k in a["keywords"]], "answer": a["answer"]}
                for a in cfg()["support"]["answers"]]
    saved = [{"id": r["id"], "keywords": [k.strip().lower() for k in r["keywords"].split(",") if k.strip()],
              "answer": r["answer"], "hits": r["hits"]}
             for r in db.q("SELECT * FROM faq ORDER BY id")]
    return saved + built_in   # staff answers first: they're the most specific


def match(text: str) -> dict | None:
    low = text.lower()
    best, score = None, 0
    for e in entries():
        n = sum(1 for k in e["keywords"] if k and k in low)
        if n > score:
            best, score = e, n
    return best


def counted(entry: dict | None) -> None:
    if entry and entry.get("id"):
        db.run("UPDATE faq SET hits = hits + 1 WHERE id=?", entry["id"])


def add(keywords: str, answer: str, by: int) -> int:
    return db.run("INSERT INTO faq(keywords, answer, added_by, created) VALUES(?, ?, ?, ?)",
                  keywords, answer, by, db.now())


def remove(fid: int) -> None:
    db.run("DELETE FROM faq WHERE id=?", fid)
