"""Offline tests for the layout engine: python -m pytest tests  (or python tests/test_layout.py)"""
import asyncio
import copy
import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from asbot.layout import model  # noqa: E402
from asbot.layout.executor import execute  # noqa: E402
from asbot.layout.planner import ChannelSnap, RoleSnap, Snapshot, plan  # noqa: E402

LAYOUT = ROOT / "layout" / "server.yaml"
EVERYONE = 1


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


def empty():
    return Snapshot(everyone_id=EVERYONE, roles={EVERYONE: RoleSnap(EVERYONE, "@everyone")}, channels={})


def test_layout_parses():
    lay = model.load(LAYOUT)
    assert [c.name for c in lay.categories][0] == "START HERE"
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
    next(c for cat in lay2.categories for c in cat.channels if c.key == "chat").name = "general"
    p = plan(lay2, snap, state)
    assert [a.op for a in p.changes] == ["edit_channel"]
    state = run(execute(p, lay2, FakeBackend(snap), state, log=lambda _: None))
    assert state["ch:chat"] == chat_id and snap.channels[chat_id].name == "general"


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
    }
    p = plan(lay, snap, {}, archive_extras=True)
    assert p.matches["ch:welcome"] == 11 and p.matches["ch:chat"] == 21
    assert {c.id for c in p.extras if c.type != "category"} == {12, 22}
    state = run(execute(p, lay, FakeBackend(snap), {}, log=lambda _: None))
    archive = state["cat:_archive"]
    assert snap.channels[12].parent == archive and snap.channels[22].parent == archive
    assert plan(lay, snap, state).changes == []


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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
