"""Optional wording polish for instant answers. OFF unless GEMINI_API_KEY is set.

Use a FREE-tier Gemini key (AI Studio project with no billing): Google rate-limits it but never
charges. It's one short web request when staff save an answer, so it doesn't touch the Railway
credit. Without a key (or if the call fails) faq.write_answer's template is used instead.
"""
from __future__ import annotations

import os

from .core import cfg, http, log

PROMPT = ("You write instant answers for the Airport Simulator (Roblox game) support bot. Rewrite the staff "
          "notes below into a short, friendly answer a player reads in Discord: plain English, at most 3 short "
          "sentences or a numbered list of steps, no greeting, no sign-off, no emojis, don't add facts that "
          "aren't in the notes.\nTrigger words: {kw}\nStaff notes: {notes}")


def available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


async def polish(keywords: str, notes: str) -> str | None:
    if not available():
        return None
    model = cfg().get("ai", {}).get("model", "gemini-flash-lite-latest")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"contents": [{"parts": [{"text": PROMPT.format(kw=keywords, notes=notes)}]}],
            "generationConfig": {"maxOutputTokens": 300, "temperature": 0.3}}
    try:
        async with http().post(url, json=body, headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]}) as r:
            data = await r.json()
        if r.status != 200:
            log.warning("ai polish: %s", (data.get("error") or {}).get("message", r.status))
            return None
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return text[:1500] or None
    except Exception as e:  # noqa: BLE001 - polish is optional, the template still works
        log.warning("ai polish: %s", e)
        return None
