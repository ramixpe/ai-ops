"""Tests for B-474 (atomic persistence): DEEP-REVIEW-2026-08-17 SS2.4 /
EXPERT-PEER-REVIEW-2026-08-17 P1-07.

Four defects, four groups of tests here:

1. Atomic writes -- a crash between "tempfile written" and "renamed into
   place" must leave the final path exactly as it was (unchanged, or still
   absent), never truncated.
2. SQLite golden replacement -- DELETE+INSERT in one transaction, plus a
   partial unique index (with a migration for a DB that predates it).
3. Guarded reads -- a corrupt/truncated snapshot must not crash a reader; it
   is skipped with a loud stderr warning, and `detect_flaps` reports the
   count in its own payload.
4. Device-name validation at the storage boundary.

Plus SS0.12 companions: an ordinary save/load round trip still works in both
backends, and `check_fabric`'s enriched error message still names the device
and the check.
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest
from helpers import install_fake_netmiko, set_device_environment

from agent_nettools import metrics
from agent_nettools.evidence_store import (
    FileEvidenceStore,
    SQLiteEvidenceStore,
    _atomic_write_text,
)
from agent_nettools.network_tools import check_fabric, detect_flaps

STORE_CLASSES = (FileEvidenceStore, SQLiteEvidenceStore)


# --------------------------------------------------------------------------- #
# 1. Atomic writes
# --------------------------------------------------------------------------- #


def test_atomic_write_leaves_an_existing_final_path_unchanged_if_replace_fails(
    tmp_path, monkeypatch
):
    path = tmp_path / "out.json"
    path.write_text("original content", encoding="utf-8")

    def raise_on_replace(_src, _dst):
        raise OSError("simulated crash between tempfile write and rename")

    monkeypatch.setattr(os, "replace", raise_on_replace)

    with pytest.raises(OSError):
        _atomic_write_text(path, "new content that must never land")

    # The final path is untouched -- no truncation, no partial write.
    assert path.read_text(encoding="utf-8") == "original content"
    # And no stray tempfile was left behind in the directory either.
    assert [entry.name for entry in tmp_path.iterdir()] == ["out.json"]


def test_atomic_write_leaves_a_never_written_final_path_absent_if_replace_fails(
    tmp_path, monkeypatch
):
    path = tmp_path / "new.json"

    def raise_on_replace(_src, _dst):
        raise OSError("simulated crash between tempfile write and rename")

    monkeypatch.setattr(os, "replace", raise_on_replace)

    with pytest.raises(OSError):
        _atomic_write_text(path, "content that must never land")

    assert not path.exists()
    assert list(tmp_path.iterdir()) == []  # tempfile cleaned up, nothing left behind


def test_atomic_write_succeeds_and_produces_exactly_the_given_text(tmp_path):
    path = tmp_path / "sub" / "out.json"  # directory does not exist yet

    _atomic_write_text(path, '{"a": 1}')

    assert path.read_text(encoding="utf-8") == '{"a": 1}'


def test_metrics_persist_swallows_a_replace_failure_and_leaves_the_file_unchanged(
    tmp_path, monkeypatch
):
    """`_persist`'s existing ``except OSError: pass`` (best-effort, same as the
    audit log) must still apply now that the write goes through
    `_atomic_write_text` -- a failed replace must not raise out of a metrics
    call, and must not leave the on-disk file half-written."""

    path = tmp_path / "metrics.json"
    collector = metrics.MetricsCollector(path=str(path))
    collector.record_collection("PE1", success=True, duration_s=1.0)
    original = path.read_text(encoding="utf-8")

    def raise_on_replace(_src, _dst):
        raise OSError("simulated crash mid-write")

    monkeypatch.setattr(os, "replace", raise_on_replace)

    collector.record_collection("PE1", success=True, duration_s=2.0)  # must not raise

    assert path.read_text(encoding="utf-8") == original


# --------------------------------------------------------------------------- #
# 2. SQLite golden replacement: one transaction + partial unique index
# --------------------------------------------------------------------------- #


def _raw_golden_rows(db_path) -> list[sqlite3.Row]:
    with sqlite3.connect(str(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            "SELECT * FROM snapshots WHERE kind = 'golden' ORDER BY id"
        ).fetchall()


def test_sqlite_migration_deduplicates_pre_existing_duplicate_golden_rows(tmp_path):
    """Hand-build a pre-B-474 database with two golden rows for one device --
    the exact shape the old two-connection DELETE+INSERT could leave behind
    on a crash between the two. Instantiating the store must collapse this
    down to the highest id (the one `ORDER BY id DESC` was already treating
    as "the" golden) before the unique index is created."""

    db_path = tmp_path / "evidence.db"
    raw = sqlite3.connect(str(db_path))
    raw.execute(
        """
        CREATE TABLE snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            kind TEXT NOT NULL,
            evidence TEXT NOT NULL
        )
        """
    )
    raw.execute(
        "INSERT INTO snapshots (device, timestamp, kind, evidence) VALUES (?, ?, ?, ?)",
        ("PE1", "first", "golden", json.dumps({"device": "PE1", "timestamp": "first"})),
    )
    raw.execute(
        "INSERT INTO snapshots (device, timestamp, kind, evidence) VALUES (?, ?, ?, ?)",
        ("PE1", "second", "golden", json.dumps({"device": "PE1", "timestamp": "second"})),
    )
    raw.commit()
    raw.close()

    store = SQLiteEvidenceStore(str(tmp_path))

    rows = _raw_golden_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["timestamp"] == "second"
    assert store.load_golden_snapshot("PE1")["timestamp"] == "second"


def test_sqlite_migration_leaves_a_single_golden_row_per_device_alone(tmp_path):
    """The migration must not be destructive for the common (non-buggy) case:
    a device that already has exactly one golden row keeps it."""

    db_path = tmp_path / "evidence.db"
    raw = sqlite3.connect(str(db_path))
    raw.execute(
        """
        CREATE TABLE snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            kind TEXT NOT NULL,
            evidence TEXT NOT NULL
        )
        """
    )
    raw.execute(
        "INSERT INTO snapshots (device, timestamp, kind, evidence) VALUES (?, ?, ?, ?)",
        ("PE1", "only", "golden", json.dumps({"device": "PE1", "timestamp": "only"})),
    )
    raw.commit()
    raw.close()

    store = SQLiteEvidenceStore(str(tmp_path))

    assert store.load_golden_snapshot("PE1")["timestamp"] == "only"


def test_sqlite_save_golden_twice_leaves_exactly_one_row(tmp_path):
    store = SQLiteEvidenceStore(str(tmp_path))

    store.save_golden_snapshot({"device": "PE1", "timestamp": "first"})
    store.save_golden_snapshot({"device": "PE1", "timestamp": "second"})

    rows = _raw_golden_rows(tmp_path / "evidence.db")
    assert len(rows) == 1
    assert rows[0]["timestamp"] == "second"


def test_sqlite_unique_index_rejects_a_second_golden_row_inserted_directly(tmp_path):
    """Pins that the partial unique index actually exists and is enforced by
    the database itself, not merely by save_golden_snapshot's own DELETE."""

    store = SQLiteEvidenceStore(str(tmp_path))
    store.save_golden_snapshot({"device": "PE1", "timestamp": "first"})

    with sqlite3.connect(str(tmp_path / "evidence.db")) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO snapshots (device, timestamp, kind, evidence) VALUES (?, ?, ?, ?)",
                ("PE1", "second", "golden", json.dumps({"device": "PE1"})),
            )


# --------------------------------------------------------------------------- #
# 3. Guarded reads
# --------------------------------------------------------------------------- #


def test_file_backend_load_latest_returns_none_and_warns_on_corrupt_json(tmp_path, capsys):
    store = FileEvidenceStore(str(tmp_path))
    store.save_snapshot({"device": "PE1", "timestamp": "t0"})
    [path] = store._timestamped_paths("PE1")  # noqa: SLF001 - test needs the path to corrupt it.
    path.write_text("{not valid json at all", encoding="utf-8")

    result = store.load_latest_snapshot("PE1")

    assert result is None
    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert str(path) in captured.err


def test_file_backend_load_golden_returns_none_and_warns_on_corrupt_json(tmp_path, capsys):
    store = FileEvidenceStore(str(tmp_path))
    store.save_golden_snapshot({"device": "PE1", "timestamp": "golden"})
    path = store._device_dir("PE1") / "golden.json"  # noqa: SLF001
    path.write_text("garbage, not json", encoding="utf-8")

    result = store.load_golden_snapshot("PE1")

    assert result is None
    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert str(path) in captured.err


def test_file_backend_list_history_skips_a_corrupt_snapshot_and_keeps_the_rest(tmp_path, capsys):
    store = FileEvidenceStore(str(tmp_path))
    store.save_snapshot({"device": "PE1", "timestamp": "t0"})
    store.save_snapshot({"device": "PE1", "timestamp": "t1"})
    paths = store._timestamped_paths("PE1")  # noqa: SLF001 - test needs to corrupt one file.
    assert len(paths) == 2
    paths[0].write_text("not json at all", encoding="utf-8")

    history = store.list_history("PE1")

    # One bad snapshot must not erase the rest of the device's history.
    assert len(history) == 1
    assert history[0]["timestamp"] == "t1"
    assert "WARNING" in capsys.readouterr().err


def test_sqlite_backend_guards_a_corrupt_evidence_column_the_same_way(tmp_path, capsys):
    """Same guard, applied to the sqlite backend's `evidence` column instead
    of a file -- hand-corrupt the row's JSON directly."""

    store = SQLiteEvidenceStore(str(tmp_path))
    store.save_snapshot({"device": "PE1", "timestamp": "t0"})

    db_path = tmp_path / "evidence.db"
    with sqlite3.connect(str(db_path)) as connection:
        connection.execute(
            "UPDATE snapshots SET evidence = ? WHERE device = ?", ("not json", "PE1")
        )

    assert store.load_latest_snapshot("PE1") is None
    assert "WARNING" in capsys.readouterr().err

    assert store.list_history("PE1") == []
    assert "WARNING" in capsys.readouterr().err


def _write_raw_snapshot(directory, name: str, content: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(content, encoding="utf-8")


def test_detect_flaps_skips_a_corrupt_snapshot_and_reports_the_count(tmp_path, capsys):
    device_dir = tmp_path / "PE9"
    # Two parseable (if minimal) snapshots, one corrupt, interleaved by
    # filename order so the corrupt one is not first or last.
    _write_raw_snapshot(device_dir, "2026-01-01T00-00-00.json", json.dumps({"device": "PE9"}))
    _write_raw_snapshot(device_dir, "2026-01-02T00-00-00.json", "{ this is not json")
    _write_raw_snapshot(device_dir, "2026-01-03T00-00-00.json", json.dumps({"device": "PE9"}))

    result = detect_flaps("PE9", base_dir=str(tmp_path))

    assert result["snapshots_examined"] == 2
    assert result["snapshots_skipped"] == 1
    assert result["flapping"] == []  # trivial snapshots, nothing to correlate
    assert "WARNING" in capsys.readouterr().err


def test_detect_flaps_with_no_corruption_reports_zero_skipped(tmp_path):
    device_dir = tmp_path / "PE9"
    _write_raw_snapshot(device_dir, "2026-01-01T00-00-00.json", json.dumps({"device": "PE9"}))

    result = detect_flaps("PE9", base_dir=str(tmp_path))

    assert result["snapshots_skipped"] == 0
    assert result["snapshots_examined"] == 1


# --------------------------------------------------------------------------- #
# 4. Device-name validation at the storage boundary
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("store_cls", STORE_CLASSES)
def test_save_snapshot_rejects_a_path_traversal_device_name(tmp_path, store_cls):
    store = store_cls(str(tmp_path))

    with pytest.raises(ValueError):
        store.save_snapshot({"device": "../evil"})


@pytest.mark.parametrize("store_cls", STORE_CLASSES)
def test_save_golden_snapshot_rejects_a_path_traversal_device_name(tmp_path, store_cls):
    store = store_cls(str(tmp_path))

    with pytest.raises(ValueError):
        store.save_golden_snapshot({"device": "../evil"})


@pytest.mark.parametrize("store_cls", STORE_CLASSES)
@pytest.mark.parametrize("bad_name", [".", "..", "a/b", "a\\b", "", "x" * 65])
def test_save_snapshot_rejects_every_shape_of_bad_device_name(tmp_path, store_cls, bad_name):
    store = store_cls(str(tmp_path))

    with pytest.raises(ValueError):
        store.save_snapshot({"device": bad_name})


@pytest.mark.parametrize("store_cls", STORE_CLASSES)
def test_save_snapshot_accepts_ordinary_inventory_shaped_names(tmp_path, store_cls):
    store = store_cls(str(tmp_path))

    for name in ("PE1", "rtr-01.lab", "P_4"):
        store.save_snapshot({"device": name, "timestamp": name})
        assert store.load_latest_snapshot(name)["device"] == name


# --------------------------------------------------------------------------- #
# SS0.12 companions
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("store_cls", STORE_CLASSES)
def test_ordinary_save_and_load_round_trip_still_works(tmp_path, store_cls):
    store = store_cls(str(tmp_path))

    store.save_snapshot({"device": "PE1", "timestamp": "t0", "facts": {"status": "success"}})
    store.save_golden_snapshot({"device": "PE1", "timestamp": "golden"})

    latest = store.load_latest_snapshot("PE1")
    golden = store.load_golden_snapshot("PE1")

    assert latest is not None and latest["timestamp"] == "t0"
    assert golden is not None and golden["timestamp"] == "golden"
    assert store.list_history("PE1") == [latest]


def test_check_fabric_error_message_still_names_the_device_and_the_check(monkeypatch):
    set_device_environment(monkeypatch)
    install_fake_netmiko(monkeypatch, fail_commands={"show bgp summary"})

    result = check_fabric("bgp")

    assert result["status"] == "error"
    assert result["errors"], "at least one device should have failed"
    message = result["errors"][0]
    assert "bgp check failed" in message
    assert "show bgp summary" in message  # the enriched reason, not just the verdict
    # And the device name that failed is identifiable from the message itself.
    assert message.split(":", 1)[0] in result["data"]["devices"]


def test_save_paths_actually_route_through_the_atomic_writer(tmp_path, monkeypatch):
    """The wiring, not the helper. Found by mutation, not by review.

    The tests above prove `_atomic_write_text` behaves; none of them proved
    `save_snapshot`/`save_golden_snapshot` *call* it -- reverting either to a
    bare `write_text` passed all 35, which is §0.13's tests face wearing a
    green suite (the mutation harness reported the guard VACUOUS, and it was
    right). A recorder pins the route itself, so the revert now fails here.
    """

    import agent_nettools.evidence_store as es

    calls: list[str] = []
    real = es._atomic_write_text

    def recording(path, text):
        calls.append(path.name)
        real(path, text)

    monkeypatch.setattr(es, "_atomic_write_text", recording)

    store = es.FileEvidenceStore(str(tmp_path))
    store.save_snapshot({"device": "PE1", "timestamp": "2026-08-17T00:00:00+00:00"})
    store.save_golden_snapshot({"device": "PE1"})

    assert len(calls) == 2, "both save paths must route through _atomic_write_text"
    assert es.GOLDEN_SNAPSHOT_FILENAME in calls

    # metrics' persist too -- same helper, same wiring risk.
    import agent_nettools.metrics as m

    calls.clear()
    monkeypatch.setattr(m, "_atomic_write_text", recording)
    monkeypatch.setenv("NETTOOLS_METRICS_FILE", str(tmp_path / "metrics.json"))
    m.MetricsCollector().record_collection("PE1", success=True, duration_s=0.1)
    assert calls, "metrics._persist must route through _atomic_write_text"


# --------------------------------------------------------------------------- #
# 5. Durable-data permissions (EER-019). Evidence snapshots, metrics, and
# (elsewhere: ticket.py, ledger.py, session_memory.py) hold device evidence
# and, for tickets, full model prompts/responses -- restrictive-by-default
# permissions, not left to whatever the caller's umask happens to be.
#
# A note on umask and why most assertions below are exact, not "no group/
# other bits": every mode here is the result of an explicit `os.chmod` (for
# directories, and for the sqlite db file) or of `tempfile.mkstemp`'s
# hardcoded 0600 request (for atomically-written files). `os.chmod` sets the
# exact bits requested -- it is not filtered through umask the way
# `os.mkdir`/`os.open`'s own `mode=` argument is -- so these assertions are
# deterministic regardless of the umask the test process happens to run
# under. The one test below that exercises a *fresh* mkstemp file under an
# adversarial umask (`test_atomic_write_text_result_is_0600_even_overwriting_
# a_permissive_file`) sets the umask explicitly anyway, belt-and-suspenders,
# since mkstemp's request is 0600 -- no group/other bits to begin with -- and
# a sane umask can only ever clear bits, never add them.
# --------------------------------------------------------------------------- #


def test_atomic_write_text_result_is_0600_even_overwriting_a_permissive_file(tmp_path):
    """Referenced by name in `evidence_store._atomic_write_text`'s docstring:
    pins the claim that `os.replace` does not inherit the destination's
    pre-existing permissions -- the tempfile's own 0600 mode survives the
    rename onto a target that used to be 0644."""

    path = tmp_path / "snapshot.json"
    path.write_text("old", encoding="utf-8")
    os.chmod(path, 0o644)

    old_umask = os.umask(0o022)  # exact assertion below: pin this regardless of the ambient umask.
    try:
        from agent_nettools.evidence_store import _atomic_write_text

        _atomic_write_text(path, "new")
    finally:
        os.umask(old_umask)

    assert path.read_text(encoding="utf-8") == "new"
    assert path.stat().st_mode & 0o777 == 0o600


def test_atomic_write_text_creates_a_0700_directory(tmp_path):
    from agent_nettools.evidence_store import _atomic_write_text

    directory = tmp_path / "fresh"
    _atomic_write_text(directory / "out.json", "{}")

    assert directory.stat().st_mode & 0o777 == 0o700


def test_atomic_write_text_self_heals_a_permissive_existing_directory(tmp_path):
    """`Path.mkdir(exist_ok=True)` silently ignores `mode=` once the
    directory already exists -- the whole reason `_secure_mkdir` chmods on
    every call rather than only at creation. A directory left 0755 by, say,
    a version of this code that predates EER-019 must come back to 0700 the
    next time anything is written into it."""

    directory = tmp_path / "evidence-dir"
    directory.mkdir()
    os.chmod(directory, 0o755)

    from agent_nettools.evidence_store import _atomic_write_text

    _atomic_write_text(directory / "out.json", "{}")

    assert directory.stat().st_mode & 0o777 == 0o700


def test_file_evidence_store_device_directory_is_0700(tmp_path):
    store = FileEvidenceStore(str(tmp_path))
    store.save_snapshot({"device": "PE1", "timestamp": "2026-08-17T00:00:00+00:00"})

    assert (tmp_path / "PE1").stat().st_mode & 0o777 == 0o700


def test_file_evidence_store_snapshot_and_golden_files_are_0600(tmp_path):
    store = FileEvidenceStore(str(tmp_path))
    store.save_snapshot({"device": "PE1", "timestamp": "2026-08-17T00:00:00+00:00"})
    store.save_golden_snapshot({"device": "PE1"})

    device_dir = tmp_path / "PE1"
    snapshot_files = [p for p in device_dir.glob("*.json") if p.name != "golden.json"]
    assert snapshot_files, "expected exactly one timestamped snapshot"
    for path in snapshot_files + [device_dir / "golden.json"]:
        assert path.stat().st_mode & 0o777 == 0o600, path


def test_sqlite_evidence_store_db_file_and_directory_are_owner_only(tmp_path):
    # A subdirectory that does not exist yet, not `tmp_path` itself -- pytest's
    # own `tmp_path` fixture already creates its directory at 0700, which
    # would make the directory assertion below pass whether or not
    # `_secure_mkdir` ever ran (mkdir's `exist_ok=True` is a no-op on an
    # already-existing directory).
    base_dir = tmp_path / "store"
    store = SQLiteEvidenceStore(str(base_dir))
    store.save_snapshot({"device": "PE1", "timestamp": "2026-08-17T00:00:00+00:00"})

    db_path = base_dir / "evidence.db"
    assert db_path.is_file()
    assert db_path.stat().st_mode & 0o777 == 0o600
    assert base_dir.stat().st_mode & 0o777 == 0o700


def test_sqlite_evidence_store_self_heals_a_pre_existing_permissive_db_file(tmp_path):
    """Constructing the store re-chmods on every open (`_init_schema` runs
    unconditionally in `__init__`), not just the first time the file is
    created -- so a database left permissive by an older version of this
    code, or opened by hand, self-heals the next time anything opens it."""

    SQLiteEvidenceStore(str(tmp_path))  # creates evidence.db
    db_path = tmp_path / "evidence.db"
    os.chmod(db_path, 0o644)

    SQLiteEvidenceStore(str(tmp_path))

    assert db_path.stat().st_mode & 0o777 == 0o600


def test_metrics_persist_file_is_0600(tmp_path):
    path = tmp_path / "metrics.json"
    collector = metrics.MetricsCollector(path=str(path))
    collector.record_collection("PE1", success=True, duration_s=0.1)

    assert path.stat().st_mode & 0o777 == 0o600
