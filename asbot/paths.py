"""Where the bot keeps changing data (database, layout ids, templates, logs).

Locally that's the repo folder. On Railway, RAILWAY_VOLUME_MOUNT_PATH (or ASBOT_DATA) points at
the mounted volume, so data survives redeploys. The volume is the truth: seed/ only fills gaps
(a fresh volume, or ids that are new in the seed), it never overwrites what is there.
"""
import hashlib
import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_VOLUME = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
if _VOLUME and os.name == "nt":   # `railway run` on the PC passes the server's mount path; no volume here
    _VOLUME = None
DATA = Path(_VOLUME or os.environ.get("ASBOT_DATA") or ROOT)
SEED = ROOT / "seed"
# Layout state (key -> Discord id). The bot's copy is DATA/state (the volume). On the PC (no
# volume, no ASBOT_DATA) the newest copy we have is the one that ships to the bot: seed/state.
STATE = DATA / "state" if DATA != ROOT else SEED / "state"


def _read(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write(path: Path, data) -> None:
    """tmp + replace, so a crash mid-write never leaves a broken file."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def merge_seed_state() -> None:
    """Fill gaps on the volume from seed/state; nothing already on the volume is overwritten
    (`/layout apply` runs on Railway, so the volume's state is newer than the seed):
    - applied_<guild>.json: copied only when the volume has none.
    - ids_<guild>.json: a key is added when it is new in the seed (never offered before, see
      seeded_<guild>.json) and missing on the volume. A key the volume dropped (a removed
      section) is not brought back, and existing ids never change.
    - force_<guild>.json (optional, rare): ids that MUST change, e.g. after a PC apply re-made a
      channel. Applied once per file content (force_<guild>.done), not on every boot."""
    state, seed_state = DATA / "state", SEED / "state"
    state.mkdir(parents=True, exist_ok=True)
    for src in seed_state.glob("applied_*.json"):
        if not (state / src.name).exists():
            shutil.copy2(src, state / src.name)
    for src in seed_state.glob("ids_*.json"):
        dst = state / src.name
        if not dst.exists():
            continue
        guild = src.stem.removeprefix("ids_")
        live, seed = _read(dst), _read(src)
        offered_file = state / f"seeded_{guild}.json"
        offered = set(_read(offered_file, []))
        merged = {**live, **{k: v for k, v in seed.items() if k not in live and k not in offered}}
        force, done = seed_state / f"force_{guild}.json", state / f"force_{guild}.done"
        stamp = hashlib.sha256(force.read_bytes()).hexdigest() if force.exists() else None
        force_due = stamp is not None and _read(done) != stamp
        if force_due:
            merged.update(_read(force))
        if merged != live:
            _write(dst, merged)
        if not offered >= set(seed):
            _write(offered_file, sorted(offered | set(seed)))
        if force_due:
            _write(done, stamp)


def seed_once() -> None:
    """Fill a fresh volume from seed/ (only when it has no layout ids yet), then merge any gaps.
    Live data is never overwritten."""
    if DATA == ROOT or not SEED.exists():
        return
    if not any((DATA / "state").glob("ids_*.json")):
        for src in SEED.rglob("*"):
            if src.is_file() and not src.name.startswith("force_"):
                dst = DATA / src.relative_to(SEED)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        for stale in ("bot.db-wal", "bot.db-shm"):
            (DATA / "data" / stale).unlink(missing_ok=True)
    merge_seed_state()
