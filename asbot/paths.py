"""Where the bot keeps changing data (database, layout ids, templates, logs).

Locally that's the repo folder. On Railway, ASBOT_DATA points at the mounted volume,
so data survives redeploys. On a fresh volume the repo's seed/ copy is used once.
"""
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or os.environ.get("ASBOT_DATA") or ROOT)
SEED = ROOT / "seed"


def seed_once() -> None:
    """Fill a fresh volume from seed/: only when it has no layout ids yet, so live data
    is never overwritten."""
    if DATA == ROOT or not SEED.exists() or any((DATA / "state").glob("ids_*.json")):
        return
    for src in SEED.rglob("*"):
        if src.is_file():
            dst = DATA / src.relative_to(SEED)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    for stale in ("bot.db-wal", "bot.db-shm"):
        (DATA / "data" / stale).unlink(missing_ok=True)
