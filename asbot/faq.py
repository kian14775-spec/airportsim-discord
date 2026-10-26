"""Instant answers ("fix it first") that grow from staff replies.

Built-in answers live in config/bot.yaml; staff add more with /answer add (a few trigger
words + the fix in rough notes, the bot writes the full sentence), the "Save fix" button on
a ticket, or right-click a reply -> Apps -> "Save as instant answer". Matching is plain
Python (no AI, nothing paid): trigger words count, small typos still match, and the answer
with the most hits wins. Hits are counted so staff see which ones work.
"""
from __future__ import annotations

import difflib
import re

from . import db
from .core import cfg

db.conn().executescript("""
CREATE TABLE IF NOT EXISTS faq (id INTEGER PRIMARY KEY AUTOINCREMENT, keywords TEXT NOT NULL, answer TEXT NOT NULL,
    added_by INTEGER, created REAL NOT NULL, hits INTEGER NOT NULL DEFAULT 0);
""")

WORD = re.compile(r"[a-z0-9']+")


def entries() -> list[dict]:
    built_in = [{"id": f"b{i}", "keywords": [k.lower() for k in a["keywords"]], "answer": a["answer"], "hits": 0}
                for i, a in enumerate(cfg()["support"]["answers"])]
    saved = [{"id": r["id"], "keywords": [k.strip().lower() for k in r["keywords"].split(",") if k.strip()],
              "answer": r["answer"], "hits": r["hits"]}
             for r in db.q("SELECT * FROM faq ORDER BY id")]
    return saved + built_in   # staff answers first: they're the most specific


def _stem(w: str) -> str:
    """liveries -> livery, planes -> plane, servers -> server (good enough for trigger words)."""
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


stem = _stem


def _hit(keyword: str, low: str, words: list[str]) -> bool:
    if keyword in low:
        return True
    if " " in keyword:
        return False
    k = _stem(keyword)
    if any(_stem(w) == k for w in words):
        return True
    if len(k) < 5:
        return False
    # one long keyword word: small typos still match ("livry", "templete")
    return any(abs(len(w) - len(k)) <= 2 and difflib.SequenceMatcher(None, _stem(w), k).ratio() >= 0.84
               for w in words)


def ranked(text: str, skip: set | None = None) -> list[dict]:
    """Answers that match `text`, best first (ids in `skip` are left out)."""
    low = text.lower()
    words = WORD.findall(low)
    out = []
    for e in entries():
        if skip and e["id"] in skip:
            continue
        n = sum(1 for k in e["keywords"] if k and _hit(k, low, words))
        if n:
            out.append((n, e))
    out.sort(key=lambda x: -x[0])   # stable sort: staff answers stay ahead on a tie
    return [e for _, e in out]


def match(text: str, skip: set | None = None) -> dict | None:
    found = ranked(text, skip)
    return found[0] if found else None


def label(entry: dict) -> str:
    """Short name for staff: '#4 livery, template' or 'built-in: livery, template'."""
    kw = ", ".join(entry["keywords"][:3])
    return f"#{entry['id']} {kw}" if isinstance(entry["id"], int) else f"built-in: {kw}"


def counted(entry: dict | None) -> None:
    if entry and isinstance(entry.get("id"), int):
        db.run("UPDATE faq SET hits = hits + 1 WHERE id=?", entry["id"])


def _clean(keywords: str) -> str:
    return ", ".join(k.strip().lower() for k in re.split(r"[,\n]", keywords) if k.strip())


def add(keywords: str, answer: str, by: int) -> int:
    return db.run("INSERT INTO faq(keywords, answer, added_by, created) VALUES(?, ?, ?, ?)",
                  _clean(keywords), answer.strip(), by, db.now())


def update(fid: int, keywords: str, answer: str) -> None:
    db.run("UPDATE faq SET keywords=?, answer=? WHERE id=?", _clean(keywords), answer.strip(), fid)


def get(fid: int):
    return db.one("SELECT * FROM faq WHERE id=?", fid)


def remove(fid: int) -> None:
    db.run("DELETE FROM faq WHERE id=?", fid)


# ---------- rough notes -> a full answer (no AI) ----------
def _sentence(s: str) -> str:
    s = s.strip(" .-•*\t")
    if not s:
        return ""
    s = s[0].upper() + s[1:]
    return s if s[-1] in ".!?" else s + "."


def write_answer(notes: str) -> str:
    """Turn notes like "rejoin, try another server, still broken = send a screenshot" into a
    friendly answer: one step -> one sentence, several -> numbered steps."""
    notes = notes.strip()
    if not notes:
        return ""
    if len(notes) > 160 and notes.count(". ") >= 2:   # already written out properly: keep it
        return notes
    parts = [p.strip() for p in re.split(r"\n|;|,(?![^()]*\))| then | and then ", notes)]
    steps = [s for s in (_sentence(p) for p in parts) if s]
    if len(steps) == 1:
        return f"Try this: {steps[0]}"
    return "Try these steps:\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
