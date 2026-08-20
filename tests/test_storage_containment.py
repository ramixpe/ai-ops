"""EER-001: no storage path may escape its configured root.

The vulnerability these tests pin was real and reproduced before the fix:
``FileEvidenceStore(root).prune(device_name="../outside", keep_count=0)``
reported one removal and deleted ``outside/victim.json``. ``prune`` unlinks
files, so this was destructive, not merely a read escape.

Every test here uses a **canary outside the root** and asserts it survives,
rather than only asserting that a ``ValueError`` was raised -- an exception
raised for some unrelated reason would satisfy the weaker assertion while the
file was already gone. The canary is the thing that actually matters.

Positive controls (OBS-181) sit beside each refusal: a real device name and a
real fixture label must still work, or these tests would pass just as well
against a function that refuses everything.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_nettools.evidence_store import FileEvidenceStore, SQLiteEvidenceStore
from agent_nettools.fixtures import fixture_path

#: Shapes a traversal can take. `a/../../outside` matters specifically: it is
#: benign-looking until resolved, which is why containment is asserted after
#: resolution and not by scanning the raw string for "..".
TRAVERSALS = [
    "../outside",
    "../../outside",
    "..",
    ".",
    "/etc",
    "a/../../outside",
    "x\x00y",
    "sub/dir",
]


@pytest.fixture
def rooted(tmp_path: Path) -> tuple[Path, Path]:
    """An evidence root, and a canary JSON file just outside it."""

    root = tmp_path / "evidence"
    root.mkdir()
    victim = tmp_path / "outside" / "victim.json"
    victim.parent.mkdir()
    victim.write_text(json.dumps({"canary": "MUST_SURVIVE"}), encoding="utf-8")
    return root, victim


@pytest.mark.parametrize("device_name", TRAVERSALS)
def test_file_prune_cannot_delete_outside_the_evidence_root(rooted, device_name):
    root, victim = rooted
    store = FileEvidenceStore(root)

    with pytest.raises(ValueError):
        store.prune(device_name=device_name, keep_count=0)

    assert victim.exists(), f"{device_name!r} deleted a file outside the root"
    assert json.loads(victim.read_text())["canary"] == "MUST_SURVIVE"


@pytest.mark.parametrize("device_name", TRAVERSALS)
def test_file_reads_cannot_reach_outside_the_evidence_root(rooted, device_name):
    root, victim = rooted
    store = FileEvidenceStore(root)

    for read in (store.load_latest_snapshot, store.load_golden_snapshot):
        with pytest.raises(ValueError):
            read(device_name)
    assert victim.exists()


def test_a_real_device_name_still_prunes_and_reads(rooted):
    """Positive control: the guard rejects traversal, not everything."""

    root, _ = rooted
    store = FileEvidenceStore(root)

    assert store.prune(device_name="PE1", keep_count=0) == {"removed": 0, "devices": {}}
    assert store.load_latest_snapshot("PE1") is None  # absent, not refused
    assert store.load_golden_snapshot("PE1") is None


def test_a_saved_snapshot_round_trips_through_the_guarded_path(rooted):
    """The guard sits on the read path too, so a real save must still load."""

    root, _ = rooted
    store = FileEvidenceStore(root)
    store.save_snapshot({"device": "PE1", "timestamp": "2026-08-20T00:00:00+00:00"})

    loaded = store.load_latest_snapshot("PE1")
    assert loaded is not None and loaded["device"] == "PE1"


@pytest.mark.parametrize("device_name", TRAVERSALS)
def test_sqlite_prune_refuses_the_same_names_the_file_backend_does(tmp_path, device_name):
    """Backend parity. SQLite has no path to traverse -- every query is
    parameterised -- so this is about the two backends agreeing on what a
    device IS, not about a second escape route."""

    store = SQLiteEvidenceStore(str(tmp_path / "snapshots.db"))
    with pytest.raises(ValueError):
        store.prune(device_name=device_name, keep_count=0)


def test_sqlite_prune_accepts_a_real_device_name(tmp_path):
    store = SQLiteEvidenceStore(str(tmp_path / "snapshots.db"))
    assert store.prune(device_name="PE1", keep_count=0)["removed"] == 0


@pytest.mark.parametrize("label", ["../..", "..", "/etc", "a/b", "."])
def test_fixture_label_cannot_escape_the_fixture_root(label):
    with pytest.raises(ValueError):
        fixture_path({"platform": "cisco_xr", "name": "PE1"}, "show version", label=label)


@pytest.mark.parametrize("device_name", ["../../etc", "..", "a/b"])
def test_fixture_device_name_cannot_escape_the_fixture_root(device_name):
    with pytest.raises(ValueError):
        fixture_path({"platform": "cisco_xr", "name": device_name}, "show version", label="t0")


def test_every_committed_fixture_label_is_still_accepted():
    """Positive control against the REAL corpus, not an invented name: if the
    charset were too strict, the offline replay this project's flagship demo
    depends on would break, and a refusal-only test would not notice."""

    for label in ("t0", "t1", "broken", "healthy", "isis-broken"):
        path = fixture_path({"platform": "cisco_xr", "name": "PE1"}, "show version", label=label)
        assert path.name == "show-version.txt"
        assert "cisco_xr/PE1" in str(path)
