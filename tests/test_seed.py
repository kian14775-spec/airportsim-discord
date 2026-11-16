"""Offline tests for the boot-time seed merge: the volume's layout state is never overwritten by the
seed copy in the image: python tests/test_seed.py"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from asbot import paths  # noqa: E402

G = 42   # guild id
_REAL = paths.DATA, paths.SEED


def teardown_module():
    paths.DATA, paths.SEED = _REAL


def setup(volume_ids=None, volume_applied=None):
    """A fake image seed/ + volume; paths points at them. None = the volume has no such file."""
    tmp = Path(tempfile.mkdtemp())
    paths.DATA, paths.SEED = tmp / "volume", tmp / "seed"
    (paths.SEED / "state").mkdir(parents=True)
    (paths.SEED / "data").mkdir()
    write(paths.SEED / "state" / f"ids_{G}.json", {"ch:a": 1, "ch:b": 2})
    write(paths.SEED / "state" / f"applied_{G}.json", {"ch:a": {"name": "a", "topic": "seed topic"}})
    (paths.SEED / "data" / "bot.db").write_bytes(b"db")
    (paths.SEED / "data" / "bot.db-wal").write_bytes(b"wal")
    if volume_ids is not None:
        write(paths.DATA / "state" / f"ids_{G}.json", volume_ids)
    if volume_applied is not None:
        write(paths.DATA / "state" / f"applied_{G}.json", volume_applied)


def write(f: Path, data) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data))


def vol(name: str):
    f = paths.DATA / "state" / name
    return json.loads(f.read_text()) if f.exists() else None


def test_boot_never_overwrites_the_volume():
    applied = {"ch:a": {"name": "a", "topic": "set by /layout apply on Railway"}}
    setup(volume_ids={"ch:a": 10}, volume_applied=applied)
    paths.seed_once()
    assert vol(f"applied_{G}.json") == applied                     # H2: was replaced by the seed copy
    assert vol(f"ids_{G}.json") == {"ch:a": 10, "ch:b": 2}         # live id kept, missing key filled in
    assert not (paths.DATA / "data" / "bot.db").exists()            # not a fresh volume: no full copy
    paths.seed_once()                                              # every later boot: still no change
    assert vol(f"applied_{G}.json") == applied


def test_removed_key_stays_removed_and_new_seed_keys_arrive():
    setup(volume_ids={"ch:a": 10})
    paths.seed_once()
    write(paths.DATA / "state" / f"ids_{G}.json", {"ch:a": 10})   # an apply on Railway removed ch:b
    paths.seed_once()
    assert vol(f"ids_{G}.json") == {"ch:a": 10}
    write(paths.SEED / "state" / f"ids_{G}.json", {"ch:a": 1, "ch:b": 2, "ch:c": 3})   # new key in a deploy
    paths.seed_once()
    assert vol(f"ids_{G}.json") == {"ch:a": 10, "ch:c": 3}
    assert vol(f"applied_{G}.json") == {"ch:a": {"name": "a", "topic": "seed topic"}}   # volume had none


def test_force_file_applies_once():
    setup(volume_ids={"ch:a": 10})
    write(paths.SEED / "state" / f"force_{G}.json", {"ch:a": 99})
    paths.seed_once()
    assert vol(f"ids_{G}.json")["ch:a"] == 99
    write(paths.DATA / "state" / f"ids_{G}.json", {"ch:a": 123, "ch:b": 2})   # re-made later on Railway
    paths.seed_once()
    assert vol(f"ids_{G}.json")["ch:a"] == 123                    # M19: was forced back on every boot
    write(paths.SEED / "state" / f"force_{G}.json", {"ch:a": 7})  # a new force file: applied once again
    paths.seed_once()
    assert vol(f"ids_{G}.json")["ch:a"] == 7


def test_fresh_volume_gets_the_seed():
    setup()
    write(paths.SEED / "state" / f"force_{G}.json", {"ch:b": 5})
    paths.seed_once()
    assert vol(f"ids_{G}.json") == {"ch:a": 1, "ch:b": 5}
    assert vol(f"applied_{G}.json") == {"ch:a": {"name": "a", "topic": "seed topic"}}
    assert (paths.DATA / "data" / "bot.db").read_bytes() == b"db"
    assert not (paths.DATA / "data" / "bot.db-wal").exists()
    assert not (paths.DATA / "state" / f"force_{G}.json").exists()
    assert sorted(vol(f"seeded_{G}.json")) == ["ch:a", "ch:b"]


def test_repo_seed_merges_cleanly():
    """The real seed/ merges into a volume without errors and without writing into the repo."""
    tmp = Path(tempfile.mkdtemp())
    paths.DATA, paths.SEED = tmp, ROOT / "seed"
    ids = next((ROOT / "seed" / "state").glob("ids_*.json"))
    write(tmp / "state" / ids.name, {"ch:inbox": 1})
    paths.merge_seed_state()
    merged = json.loads((tmp / "state" / ids.name).read_text())
    assert merged["ch:inbox"] == 1 and len(merged) > 1
    assert list((ROOT / "seed").rglob("*.tmp")) == []             # nothing written into the repo


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
