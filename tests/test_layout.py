"""Offline tests for the layout engine: python -m pytest tests  (or python tests/test_layout.py)"""
import asyncio
import copy
import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from discord import Permissions  # noqa: E402

from asbot.layout import model  # noqa: E402
from asbot.layout.executor import execute  # noqa: E402
from asbot.layout.planner import ChannelSnap, RoleSnap, Snapshot, plan  # noqa: E402

LAYOUT = ROOT / "layout" / "server.yaml"
EVERYONE = 1
BOOSTER, ADMIN, USER = 5000, 5001, 6000          # hand-made overwrite targets the yaml doesn't name


def bits(*flags: str) -> int:
    return Permissions(**{f: True for f in flags}).value


class FakeBackend:
    """Applies changes to a Snapshot the way Discord would."""

    def __init__(self, snap: Snapshot):
        self.snap, self.everyone_id = snap, snap.everyone_id
        self.ids = itertools.count(1000)

    async def create_role(self, name, color, hoist, mentionable, permissions):
        i = next(self.ids)
        self.snap.roles[i] = RoleSnap(i, name, color or 0, bool(hoist), bool(mentionable), permissions or 0)
        return i

    async def edit_role(self, role_id, fields):
        for k, v in fields.items():
            setattr(self.snap.roles[role_id], k, v)

    async def create_category(self, name, overwrites):
        i = next(self.ids)
        self.snap.channels[i] = ChannelSnap(i, name, "category", position=99, overwrites=dict(overwrites))
        return i

    async def create_channel(self, spec, parent_id, overwrites):
        i = next(self.ids)
        self.snap.channels[i] = ChannelSnap(
            i, spec.name, spec.type, parent=parent_id, position=99, overwrites=dict(overwrites),
            topic=spec.topic if spec.type != "voice" else None, slowmode=spec.slowmode,
            tags=list(spec.tags or []), require_tag=bool(spec.require_tag), layout=spec.layout)
        return i

    async def edit_channel(self, channel_id, ctype, fields):
        ch = self.snap.channels[channel_id]
        for k, v in fields.items():
            setattr(ch, k, v)

    async def delete_category(self, category_id):
        assert not any(c.parent == category_id for c in self.snap.channels.values())
        del self.snap.channels[category_id]

    async def reorder(self, order):
        for i, pos, parent in order:
            self.snap.channels[i].position = pos
            self.snap.channels[i].parent = parent


def run(coro):
    return asyncio.run(coro)


def sync(layout, snap, state, **kw):
    p = plan(layout, snap, state, **kw)
    assert not p.errors, p.errors
    return run(execute(p, layout, FakeBackend(snap), state, log=lambda _: None))


EXTERNAL = {ADMIN: "Admin", 5002: "Public Relations Team", 5003: "Quality Assurance", 5004: "Star Program"}   # external roles


def empty():
    """A server with no layout yet, but with the roles the yaml marks external (those are never made)."""
    roles = {EVERYONE: RoleSnap(EVERYONE, "@everyone")} | {i: RoleSnap(i, n) for i, n in EXTERNAL.items()}
    return Snapshot(everyone_id=EVERYONE, roles=roles, channels={})


def test_layout_parses():
    lay = model.load(LAYOUT)
    assert lay.categories[0].top and lay.categories[0].channels[0].key == "welcome"
    assert sum(len(c.channels) for c in lay.categories) >= 10


def test_build_from_empty_then_nothing_left():
    lay, snap = model.load(LAYOUT), empty()
    state = sync(lay, snap, {})
    again = plan(lay, snap, state)
    assert again.changes == [], [a.text for a in again.changes]
    assert again.extras == []


def test_second_run_without_state_matches_by_name():
    lay, snap = model.load(LAYOUT), empty()
    sync(lay, snap, {})
    again = plan(lay, snap, {})            # lost state file: still no duplicates
    assert not [a for a in again.changes if a.op.startswith("create")]


def test_rename_keeps_same_channel():
    lay, snap = model.load(LAYOUT), empty()
    state = sync(lay, snap, {})
    chat_id = state["ch:chat"]
    lay2 = copy.deepcopy(lay)
    next(c for cat in lay2.categories for c in cat.channels if c.key == "chat").name = "hangout"
    p = plan(lay2, snap, state)
    assert [a.op for a in p.changes] == ["edit_channel"]
    state = run(execute(p, lay2, FakeBackend(snap), state, log=lambda _: None))
    assert state["ch:chat"] == chat_id and snap.channels[chat_id].name == "hangout"


def test_old_server_reuses_and_archives():
    lay = model.load(LAYOUT)
    snap = empty()
    snap.channels = {
        10: ChannelSnap(10, "BULLETIN", "category", position=0),
        11: ChannelSnap(11, "│information", "text", parent=10, position=0),
        12: ChannelSnap(12, "│livery-templates", "text", parent=10, position=1),
        20: ChannelSnap(20, "COMMUNITY", "category", position=1),
        21: ChannelSnap(21, "chat", "text", parent=20, position=0),
        22: ChannelSnap(22, "scheduled-flights", "announcement", parent=20, position=1),
        30: ChannelSnap(30, "ticket-0012", "text", parent=None, position=5),
    }
    p = plan(lay, snap, {}, archive_extras=True)
    assert p.matches["ch:welcome"] == 11 and p.matches["ch:chat"] == 21
    assert {c.id for c in p.extras if c.type != "category"} == {12, 22, 30}
    state = run(execute(p, lay, FakeBackend(snap), {}, log=lambda _: None))
    archive = state["cat:_archive"]
    assert snap.channels[12].parent == archive and snap.channels[22].parent == archive
    assert plan(lay, snap, state).changes == []


def test_empty_bot_section_is_removed():
    lay, snap = model.load(LAYOUT), empty()
    state = sync(lay, snap, {})
    lay2 = copy.deepcopy(lay)
    moved = lay2.categories.pop(-2)             # drop a section; its channels go to the first one
    lay2.categories[0].channels += moved.channels
    p = plan(lay2, snap, state)
    assert [a.op for a in p.changes][-1] == "remove_category"
    state = run(execute(p, lay2, FakeBackend(snap), state, log=lambda _: None))
    assert f"cat:{moved.key}" not in state
    assert plan(lay2, snap, state).changes == []


def test_community_required():
    lay, snap = model.load(LAYOUT), empty()
    snap.community = False
    assert plan(lay, snap, {}).errors


def test_bad_permission_is_caught():
    data = {"roles": [], "presets": {"x": {"@everyone": {"allow": ["fly_planes"]}}}, "categories": []}
    try:
        model.parse(data)
    except model.LayoutError as e:
        assert "fly_planes" in str(e)
    else:
        raise AssertionError("expected LayoutError")


# ---- C1: the layout owns only the overwrites it names ----
def hand_made_server():
    """The built layout plus the live server's hand-made perms: booster perks on #general and #media
    (Discord's managed Server Booster role), an Admin role overwrite on #media, a user block on #dev-logs."""
    lay, snap = model.load(LAYOUT), empty()
    state = sync(lay, snap, {})
    snap.roles[BOOSTER] = RoleSnap(BOOSTER, "Server Booster", managed=True)
    snap.roles[ADMIN] = RoleSnap(ADMIN, "Admin")
    general, media, devlogs = (snap.channels[state[f"ch:{k}"]] for k in ("chat", "media", "devlogs"))
    perk = (bits("attach_files", "embed_links", "use_external_emojis"), 0)
    general.overwrites[BOOSTER] = perk
    media.overwrites[BOOSTER] = perk
    media.overwrites[ADMIN] = (bits("manage_messages"), 0)
    devlogs.overwrites[USER] = (0, bits("view_channel"))
    devlogs.member_targets.add(USER)
    snap.channels[state["cat:community"]].overwrites[BOOSTER] = perk
    return lay, snap, state, general, media, devlogs


def test_unlisted_overwrites_are_not_drift():
    lay, snap, state, *_ = hand_made_server()
    p = plan(lay, snap, state)
    assert p.changes == [], [a.text for a in p.changes]


def test_unlisted_overwrites_survive_an_apply():
    lay, snap, state, general, media, devlogs = hand_made_server()
    traveler = state["role:verified"]
    allow, deny = general.overwrites[traveler]
    general.overwrites[traveler] = (allow | bits("manage_events"), deny)                         # drift
    media.overwrites[traveler] = (0, 0)                                                            # drift
    devlogs.overwrites[EVERYONE] = (0, 0)                                                          # drift
    lay2 = copy.deepcopy(lay)
    next(c for cat in lay2.categories for c in cat.channels if c.key == "stats").topic = "New topic"
    p = plan(lay2, snap, state)
    texts = {a.key: a for a in p.changes}
    assert set(texts) == {"chat", "media", "devlogs", "stats"}, [a.text for a in p.changes]
    assert texts["chat"].text == "~ #general: perms (@Traveler)"
    assert texts["chat"].details[0] == "@Traveler: allow -manage_events"
    assert texts["chat"].details[-1] == "left as they are: @Server Booster"
    assert texts["media"].details[-1] == "left as they are: @Server Booster, @Admin"
    assert texts["devlogs"].details[-1] == f"left as they are: member {USER}"
    run(execute(p, lay2, FakeBackend(snap), state, log=lambda _: None))
    assert general.overwrites[BOOSTER][0] & bits("attach_files")
    assert BOOSTER in media.overwrites and ADMIN in media.overwrites
    assert devlogs.overwrites[USER] == (0, bits("view_channel"))
    assert general.overwrites[traveler] == next(c for cat in lay.categories for c in cat.channels
                                                if c.key == "chat").overwrites["verified"]
    assert snap.channels[state["cat:community"]].overwrites[BOOSTER]
    assert plan(lay2, snap, state).changes == []


def test_layout_role_overwrite_is_still_owned():
    """A stray overwrite for a role the yaml DOES list is drift and is removed (as before)."""
    lay, snap, state, general, *_ = hand_made_server()
    pc = state["role:plat_pc"]
    general.overwrites[pc] = (bits("send_messages"), 0)
    p = plan(lay, snap, state)
    [a] = p.changes
    assert a.details[0] == "@PC: remove (allow send_messages)", a.details
    run(execute(p, lay, FakeBackend(snap), state, log=lambda _: None))
    assert pc not in general.overwrites and BOOSTER in general.overwrites


def test_discord_backend_keeps_overwrite_types():
    """The real backend writes a kept overwrite back with the channel's own target object, so an
    uncached member's block stays a MEMBER overwrite (discord.py types an untyped Object as member)."""
    import discord
    from types import SimpleNamespace as NS
    from asbot.layout.discord_backend import DiscordBackend, _is_role
    guild = NS(default_role=NS(id=EVERYONE), get_role=lambda i: None, get_member=lambda i: None,
               get_channel=lambda i: None)
    backend = DiscordBackend(guild)
    user = discord.Object(USER, type=discord.User)          # how discord.py lists an uncached member
    booster = discord.Object(BOOSTER, type=discord.Role)
    new_role = discord.Object(777, type=discord.Role)       # made earlier in this apply, not cached yet
    backend.made[777] = new_role
    ch = NS(overwrites={user: None, booster: None})
    targets = {t.id: t for t in backend._overwrites({USER: (0, 1), BOOSTER: (1, 0), 777: (1, 0)}, ch)}
    assert targets[USER] is user and not _is_role(user)
    assert targets[BOOSTER] is booster and _is_role(booster)
    assert targets[777] is new_role


# ---- external roles + perms_extra ----
def small_layout(**booster) -> dict:
    return {
        "roles": [{"key": "verified", "name": "Traveler"}, {"key": "booster", "name": "Server Booster", **booster}],
        "presets": {"members": {"@everyone": {"deny": ["view_channel"]},
                                "verified": {"allow": ["view_channel", "send_messages", "attach_files"]}}},
        "categories": [{"key": "community", "name": "ENGAGE", "perms": "members", "channels": [
            {"key": "chat", "name": "general",
             "perms_extra": {"verified": {"deny": ["attach_files"]}, "booster": {"allow": ["attach_files"]}}},
            {"key": "media", "name": "media"},
        ]}],
    }


def test_perms_extra_merges_on_top_of_the_preset():
    lay = model.parse(small_layout(external=True))
    chat, media = lay.categories[0].channels
    assert chat.overwrites["verified"] == (bits("view_channel", "send_messages"), bits("attach_files"))
    assert chat.overwrites["booster"] == (bits("attach_files"), 0)
    assert media.overwrites == lay.categories[0].overwrites           # other channels: preset only
    bad = small_layout(external=True)
    bad["categories"][0]["channels"][0]["perms_extra"] = {"verified": {"allow": ["embed_links"],
                                                                       "deny": ["embed_links"]}}
    try:
        model.parse(bad)
    except model.LayoutError as e:
        assert "both allow and deny" in str(e)
    else:
        raise AssertionError("expected LayoutError")


def test_external_role_is_referenced_never_made():
    lay, snap = model.parse(small_layout(external=True)), empty()
    snap.roles[BOOSTER] = RoleSnap(BOOSTER, "Server Booster", color=0xF47FFF, managed=True)
    p = plan(lay, snap, {})
    assert not p.errors and [a.text for a in p.actions if "role" in a.op] == ["+ role @Traveler"]
    state = run(execute(p, lay, FakeBackend(snap), {}, log=lambda _: None))
    assert state["role:booster"] == BOOSTER
    chat = snap.channels[state["ch:chat"]]
    assert chat.overwrites[BOOSTER] == (bits("attach_files"), 0)
    assert BOOSTER not in snap.channels[state["ch:media"]].overwrites
    snap.channels[state["ch:media"]].overwrites[BOOSTER] = (bits("embed_links"), 0)   # hand-made: not named there
    assert plan(lay, snap, state).changes == []
    assert snap.roles[BOOSTER].color == 0xF47FFF and snap.roles[BOOSTER].name == "Server Booster"


def test_external_role_missing_or_unmarked():
    lay, snap = model.parse(small_layout(external=True)), empty()
    p = plan(lay, snap, {})
    assert p.errors and "external" in p.errors[0]
    assert not [a for a in p.actions if a.op == "create_role" and a.key == "booster"]
    lay = model.parse(small_layout())                       # managed role listed without external: true
    snap.roles[BOOSTER] = RoleSnap(BOOSTER, "Server Booster", managed=True)
    p = plan(lay, snap, {})
    assert p.errors and "external: true" in p.errors[0]
    assert not [a for a in p.actions if a.op == "create_role" and a.key == "booster"]
    try:
        model.parse(small_layout(external=True, color="#FF00FF"))
    except model.LayoutError as e:
        assert "external" in str(e)
    else:
        raise AssertionError("expected LayoutError")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
