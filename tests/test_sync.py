"""Offline tests for layout sync: manual edits kept on every apply, the pre-apply backup + readable
preview, and the cached ids lookup: python tests/test_sync.py"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("ASBOT_DATA", tempfile.mkdtemp())   # keep test files out of the real data folder

from asbot.layout import model, sync  # noqa: E402
from test_layout import BOOSTER, EVERYONE, LAYOUT, USER, FakeBackend, bits, empty, hand_made_server  # noqa: E402

G = 42   # guild id
_REAL = sync.STATE_DIR, sync.BACKUP_DIR


def teardown_module():
    sync.STATE_DIR, sync.BACKUP_DIR = _REAL


def fresh_dirs():
    tmp = Path(tempfile.mkdtemp())
    sync.STATE_DIR, sync.BACKUP_DIR = tmp / "state", tmp / "backups"
    return tmp


def apply(layout, snap):
    """One /layout apply: plan against the fake server with the saved state, then run it."""
    p = sync.build_plan(layout, snap, sync.load_state(G), sync.load_applied(G))
    asyncio.run(sync.run_plan(G, layout, p, FakeBackend(snap), log=lambda _: None))
    return p


def with_topic(key: str, topic: str):
    lay = model.load(LAYOUT)
    next(c for cat in lay.categories for c in cat.channels if c.key == key).topic = topic
    return lay


def test_manual_topic_survives_every_apply():
    fresh_dirs()
    snap = empty()
    apply(model.load(LAYOUT), snap)
    media = snap.channels[sync.load_state(G)["ch:media"]]
    yaml_topic = media.topic
    media.topic = "Hand-written topic"                            # staff edit it in Discord
    for n in range(3):                                           # each apply has some other yaml change
        p = apply(with_topic("stats", f"Stats topic {n}"), snap)
        assert [a.key for a in p.changes] == ["stats"], [a.text for a in p.changes]
        assert p.kept and "media topic" in p.kept[0]
        assert media.topic == "Hand-written topic"
        assert sync.load_applied(G)["ch:media"]["topic"] == yaml_topic   # the yaml's value, not the hand edit
    p = apply(with_topic("media", "New yaml topic"), snap)       # the yaml itself changes it: yaml wins
    assert media.topic == "New yaml topic" and not p.kept


def test_backup_and_readable_preview():
    tmp = fresh_dirs()
    lay, snap, state, general, media, devlogs = hand_made_server()
    sync.save_state(G, state)
    traveler = state["role:verified"]
    old_traveler = general.overwrites[traveler]
    general.overwrites[traveler] = (old_traveler[0] | bits("mention_everyone"), 0)
    devlogs.overwrites[EVERYONE] = (0, 0)
    sync.BACKUP_DIR.mkdir(parents=True)
    for n in range(12):                                          # older backups: only the newest 10 stay
        (sync.BACKUP_DIR / f"layout_2020-01-01_0000{n:02d}.json").write_text("{}")
    (sync.BACKUP_DIR / "server_before_2026-10-25.json").write_text("{}")   # other backups are never touched

    p = sync.build_plan(lay, snap, sync.load_state(G), {})
    text = sync.describe(p)
    assert "~ #general: perms (@Traveler)" in text
    assert "    @Traveler: allow -mention_everyone" in text
    assert "    left as they are: @Server Booster" in text
    assert "    @everyone: deny +view_channel" in text and f"left as they are: member {USER}" in text
    assert "backups/layout_<time>.json" in text

    lines = []
    saved = asyncio.run(sync.run_plan(G, lay, p, FakeBackend(snap), log=lines.append))
    assert lines[0] == f"(old values saved to backups/{saved.name})"
    kept = sorted(sync.BACKUP_DIR.glob("layout_*.json"))
    assert len(kept) == 10 and kept[-1] == saved
    assert (sync.BACKUP_DIR / "server_before_2026-10-25.json").exists()
    data = json.loads(saved.read_text(encoding="utf-8"))
    chans = {c["name"]: c for c in data["channels"]}
    assert set(chans) == {"general", "dev-logs"}
    ows = {o["id"]: o for o in chans["general"]["overwrites"]}
    assert ows[traveler]["bits"] == [old_traveler[0] | bits("mention_everyone"), 0]   # the value BEFORE apply
    assert "mention_everyone" in ows[traveler]["allow"] and ows[traveler]["name"] == "Traveler"
    assert ows[BOOSTER] == {"id": BOOSTER, "type": "role", "name": "Server Booster",
                            "allow": ["embed_links", "attach_files", "use_external_emojis"], "deny": [],
                            "bits": [bits("attach_files", "embed_links", "use_external_emojis"), 0]}
    user = next(o for o in chans["dev-logs"]["overwrites"] if o["id"] == USER)
    assert user["type"] == "member" and user["deny"] == ["view_channel"]
    assert data["plan"] == [a.text for a in p.changes]
    assert general.overwrites[traveler] == old_traveler and BOOSTER in general.overwrites   # applied
    assert tmp in saved.parents


def test_no_backup_and_no_apply_when_the_plan_has_errors():
    fresh_dirs()
    lay, snap = model.load(LAYOUT), empty()
    snap.community = False
    p = sync.build_plan(lay, snap, {}, {})
    try:
        asyncio.run(sync.run_plan(G, lay, p, FakeBackend(snap), log=lambda _: None))
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError")
    assert not sync.BACKUP_DIR.exists() and snap.channels == {}


def test_ids_lookup_is_cached_until_the_file_changes():
    fresh_dirs()
    sync.save_state(G, {"ch:inbox": 1, "role:verified": 2})
    with mock.patch.object(sync.json, "loads", wraps=json.loads) as parse:
        for _ in range(50):
            assert sync.channel_id(G, "inbox") == 1 and sync.role_id(G, "verified") == 2
        assert parse.call_count == 1
        sync.save_state(G, {"ch:inbox": 3, "role:verified": 2})          # the bot's own write
        assert sync.channel_id(G, "inbox") == 3
        f = sync.STATE_DIR / f"ids_{G}.json"                              # someone else's write
        f.write_text(json.dumps({"ch:inbox": 40, "ch:media": 5}))
        os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 1_000_000))
        assert sync.channel_id(G, "inbox") == 40 and sync.channel_id(G, "media") == 5
        assert parse.call_count == 3
    mine = sync.load_state(G)
    mine["ch:inbox"] = 999                                               # apply edits its copy in place
    assert sync.channel_id(G, "inbox") == 40
    f.unlink()
    assert sync.channel_id(G, "inbox") is None and sync.load_state(G) == {}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
