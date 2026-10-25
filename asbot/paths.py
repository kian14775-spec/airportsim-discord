"""Where the bot keeps changing data (database, layout ids, templates, logs).

Locally that's the repo folder. On Railway, ASBOT_DATA points at the mounted volume,
so data survives redeploys. On a fresh volume the repo's seed/ copy is used once.
"""
import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or os.environ.get("ASBOT_DATA") or ROOT)
SEED = ROOT / "seed"


def merge_seed_state() -> None:
    """Add layout ids that exist in seed/state but not on the volume (e.g. a section
    created by a layout apply from the PC). Existing ids are never changed."""
    for src in (SEED / "state").glob("ids_*.json"):
        dst = DATA / "state" / src.name
        if not dst.exists():
            continue
        live, seed = json.loads(dst.read_text()), json.loads(src.read_text())
        added = {k: v for k, v in seed.items() if k not in live}
        force = SEED / "state" / src.name.replace("ids_", "force_")   # ids that must change (rare)
        forced = json.loads(force.read_text()) if force.exists() else {}
        merged = {**live, **added, **forced}
        if merged != live:
            dst.write_text(json.dumps(merged, indent=2, sort_keys=True))


def seed_once() -> None:
    """Fill a fresh volume from seed/: only when it has no layout ids yet, so live data
    is never overwritten."""
    if DATA == ROOT or not SEED.exists():
        return
    if any((DATA / "state").glob("ids_*.json")):
        merge_seed_state()
        return
    for src in SEED.rglob("*"):
        if src.is_file():
            dst = DATA / src.relative_to(SEED)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    for stale in ("bot.db-wal", "bot.db-shm"):
        (DATA / "data" / stale).unlink(missing_ok=True)
