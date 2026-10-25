"""Runs a Plan against a backend (real Discord or the fake one in tests)."""
from __future__ import annotations

from typing import Protocol

from .model import EVERYONE, Layout, Overwrites
from .planner import (ARCHIVE_KEY, ARCHIVE_NAME, Plan, cat_ref, ch_ref, desired_order, role_ref)


class Backend(Protocol):
    everyone_id: int

    async def create_role(self, name: str, color: int | None, hoist: bool | None,
                          mentionable: bool | None, permissions: int | None) -> int: ...
    async def edit_role(self, role_id: int, fields: dict) -> None: ...
    async def create_category(self, name: str, overwrites: dict[int, tuple[int, int]]) -> int: ...
    async def create_channel(self, spec, parent_id: int, overwrites: dict[int, tuple[int, int]]) -> int: ...
    async def edit_channel(self, channel_id: int, ctype: str, fields: dict) -> None: ...
    async def reorder(self, order: list[tuple[int, int, int | None]]) -> None: ...


async def execute(plan: Plan, layout: Layout, backend: Backend, state: dict[str, int],
                  log=print) -> dict[str, int]:
    """Applies the plan and returns the key -> id state. `state` is updated in place as
    each step lands, so after an error the caller can save it and a re-run continues."""
    ids = state
    ids.update(plan.matches)

    def resolve(ow: Overwrites) -> dict[int, tuple[int, int]]:
        out = {}
        for ref, bits in ow.items():
            out[backend.everyone_id if ref == EVERYONE else ids[role_ref(ref)]] = bits
        return out

    for a in plan.actions:
        if a.op == "reorder" and not a.data.get("needed"):
            continue
        log(a.text)
        if a.op == "create_role":
            s = a.data["spec"]
            ids[role_ref(s.key)] = await backend.create_role(s.name, s.color, s.hoist, s.mentionable, s.permissions)
        elif a.op == "edit_role":
            await backend.edit_role(ids[role_ref(a.key)], a.data)
        elif a.op == "create_category":
            s = a.data["spec"]
            ids[cat_ref(s.key)] = await backend.create_category(s.name, resolve(s.overwrites))
        elif a.op == "edit_category":
            fields = dict(a.data)
            if "overwrites" in fields:
                fields["overwrites"] = resolve(fields["overwrites"])
            await backend.edit_channel(ids[cat_ref(a.key)], "category", fields)
        elif a.op == "create_channel":
            s = a.data["spec"]
            ids[ch_ref(s.key)] = await backend.create_channel(s, ids[cat_ref(a.data["parent"])],
                                                              resolve(s.overwrites))
        elif a.op == "edit_channel":
            fields = {k: v for k, v in a.data.items() if k != "_type"}
            if "overwrites" in fields:
                fields["overwrites"] = resolve(fields["overwrites"])
            if "parent" in fields:
                fields["parent"] = ids[cat_ref(fields["parent"])]
            await backend.edit_channel(ids[ch_ref(a.key)], a.data["_type"], fields)
        elif a.op == "archive":
            hidden = resolve({EVERYONE: (0, _view_bit()), **_staff_view(layout)})
            if cat_ref(ARCHIVE_KEY) not in ids:
                ids[cat_ref(ARCHIVE_KEY)] = await backend.create_category(ARCHIVE_NAME, hidden)
            for cid in a.data["ids"]:
                await backend.edit_channel(cid, "archive", {"parent": ids[cat_ref(ARCHIVE_KEY)],
                                                            "overwrites": hidden})
        elif a.op == "reorder":
            order = desired_order(layout, ids)
            if cat_ref(ARCHIVE_KEY) in ids:
                order.append((ids[cat_ref(ARCHIVE_KEY)], len(layout.categories), None))
            await backend.reorder(order)
    return ids


def _view_bit() -> int:
    from discord import Permissions
    return Permissions(view_channel=True).value


def _staff_view(layout: Layout) -> Overwrites:
    staff = next((r.key for r in layout.roles if r.key == "staff"), None)
    return {staff: (_view_bit(), 0)} if staff else {}
