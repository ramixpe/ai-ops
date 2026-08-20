"""Tests for the Phase 7 evidence storage backends (JSON files and SQLite).

Both backends implement the same ``EvidenceStore`` shape, so most tests are
parametrized over both classes to pin that they agree; a couple of backend-
specific quirks (file mtime vs. a stored timestamp column for age-based
pruning) get their own test.
"""

from __future__ import annotations

import os
import time

import pytest

from agent_nettools.evidence_store import (
    DEFAULT_EVIDENCE_BACKEND,
    EVIDENCE_BACKEND_ENV,
    FileEvidenceStore,
    SQLiteEvidenceStore,
    get_store,
)

STORE_CLASSES = (FileEvidenceStore, SQLiteEvidenceStore)


def _evidence(device: str, timestamp: str) -> dict:
    return {"device": device, "timestamp": timestamp, "facts": {"status": "success"}}


@pytest.mark.parametrize("store_cls", STORE_CLASSES)
def test_save_and_load_latest_round_trips(tmp_path, store_cls):
    store = store_cls(str(tmp_path))

    store.save_snapshot(_evidence("PE1", "2026-01-01T00-00-00"))
    store.save_snapshot(_evidence("PE1", "2026-01-02T00-00-00"))

    latest = store.load_latest_snapshot("PE1")
    assert latest is not None
    assert latest["timestamp"] == "2026-01-02T00-00-00"


@pytest.mark.parametrize("store_cls", STORE_CLASSES)
def test_load_latest_returns_none_when_never_saved(tmp_path, store_cls):
    store = store_cls(str(tmp_path))

    assert store.load_latest_snapshot("NEVER_SEEN") is None


@pytest.mark.parametrize("store_cls", STORE_CLASSES)
def test_golden_is_resolved_separately_from_latest(tmp_path, store_cls):
    store = store_cls(str(tmp_path))

    store.save_snapshot(_evidence("PE1", "2026-01-01T00-00-00"))
    store.save_golden_snapshot(_evidence("PE1", "golden-capture"))
    store.save_snapshot(_evidence("PE1", "2026-01-02T00-00-00"))

    assert store.load_latest_snapshot("PE1")["timestamp"] == "2026-01-02T00-00-00"
    assert store.load_golden_snapshot("PE1")["timestamp"] == "golden-capture"


@pytest.mark.parametrize("store_cls", STORE_CLASSES)
def test_golden_returns_none_when_never_pinned(tmp_path, store_cls):
    store = store_cls(str(tmp_path))

    assert store.load_golden_snapshot("PE1") is None


@pytest.mark.parametrize("store_cls", STORE_CLASSES)
def test_pinning_golden_twice_overwrites_the_previous_pin(tmp_path, store_cls):
    store = store_cls(str(tmp_path))

    store.save_golden_snapshot(_evidence("PE1", "first"))
    store.save_golden_snapshot(_evidence("PE1", "second"))

    assert store.load_golden_snapshot("PE1")["timestamp"] == "second"


@pytest.mark.parametrize("store_cls", STORE_CLASSES)
def test_history_is_ordered_oldest_first_and_excludes_golden(tmp_path, store_cls):
    """Saved in ascending order deliberately: the file backend orders by actual
    save time (wall-clock filenames), not by whatever timestamp happens to be
    embedded in the evidence dict, so this only pins ordering for saves that
    happened in that order -- which is the only case either backend promises."""

    store = store_cls(str(tmp_path))

    store.save_golden_snapshot(_evidence("PE1", "golden-capture"))
    for stamp in ("2026-01-01T00-00-00", "2026-01-02T00-00-00", "2026-01-03T00-00-00"):
        store.save_snapshot(_evidence("PE1", stamp))

    history = store.list_history("PE1")

    assert [snapshot["timestamp"] for snapshot in history] == [
        "2026-01-01T00-00-00",
        "2026-01-02T00-00-00",
        "2026-01-03T00-00-00",
    ]


@pytest.mark.parametrize("store_cls", STORE_CLASSES)
def test_list_devices_reports_only_devices_with_timestamped_history(tmp_path, store_cls):
    store = store_cls(str(tmp_path))

    store.save_snapshot(_evidence("PE1", "2026-01-01T00-00-00"))
    # A device with only a golden pin (no timestamped history) must not appear.
    store.save_golden_snapshot(_evidence("PE2", "golden-capture"))

    assert store.list_devices() == ["PE1"]


@pytest.mark.parametrize("store_cls", STORE_CLASSES)
def test_prune_by_count_keeps_only_the_most_recent(tmp_path, store_cls):
    store = store_cls(str(tmp_path))

    for index in range(5):
        store.save_snapshot(_evidence("PE1", f"2026-01-0{index + 1}T00-00-00"))

    result = store.prune(device_name="PE1", keep_count=2)

    assert result == {"removed": 3, "devices": {"PE1": 3}}
    remaining = [snapshot["timestamp"] for snapshot in store.list_history("PE1")]
    assert remaining == ["2026-01-04T00-00-00", "2026-01-05T00-00-00"]


@pytest.mark.parametrize("store_cls", STORE_CLASSES)
def test_prune_never_touches_golden(tmp_path, store_cls):
    store = store_cls(str(tmp_path))

    store.save_golden_snapshot(_evidence("PE1", "golden-capture"))
    store.save_snapshot(_evidence("PE1", "2026-01-01T00-00-00"))

    store.prune(device_name="PE1", keep_count=0)

    assert store.load_golden_snapshot("PE1")["timestamp"] == "golden-capture"
    assert store.list_history("PE1") == []


@pytest.mark.parametrize("store_cls", STORE_CLASSES)
def test_prune_with_no_rules_configured_is_a_no_op(tmp_path, store_cls):
    store = store_cls(str(tmp_path))
    store.save_snapshot(_evidence("PE1", "2026-01-01T00-00-00"))

    result = store.prune(device_name="PE1")

    assert result == {"removed": 0, "devices": {}}
    assert len(store.list_history("PE1")) == 1


@pytest.mark.parametrize("store_cls", STORE_CLASSES)
def test_prune_without_device_name_covers_every_device(tmp_path, store_cls):
    store = store_cls(str(tmp_path))
    store.save_snapshot(_evidence("PE1", "2026-01-01T00-00-00"))
    store.save_snapshot(_evidence("PE1", "2026-01-02T00-00-00"))
    store.save_snapshot(_evidence("PE2", "2026-01-01T00-00-00"))

    result = store.prune(keep_count=1)

    # PE1 has 2 snapshots and loses 1 to keep_count=1; PE2 already has only 1
    # and loses none -- pruning "every device" must not over-delete a device
    # that was already within the retention window.
    assert result["removed"] == 1
    assert len(store.list_history("PE1")) == 1
    assert len(store.list_history("PE2")) == 1


def test_sqlite_prune_by_age_uses_the_stored_timestamp(tmp_path):
    store = SQLiteEvidenceStore(str(tmp_path))
    old = time.time() - 10 * 86400
    store._insert(  # noqa: SLF001 - test needs to control the stored timestamp directly.
        {"device": "PE1", "timestamp": _iso(old)}, kind="snapshot"
    )
    store.save_snapshot(_evidence("PE1", _iso(time.time())))

    result = store.prune(device_name="PE1", keep_days=1)

    assert result == {"removed": 1, "devices": {"PE1": 1}}
    assert len(store.list_history("PE1")) == 1


def test_file_prune_by_age_uses_mtime(tmp_path):
    store = FileEvidenceStore(str(tmp_path))
    store.save_snapshot(_evidence("PE1", "old"))
    [old_path] = store._timestamped_paths("PE1")  # noqa: SLF001 - test needs the path to backdate.
    ten_days_ago = time.time() - 10 * 86400
    os.utime(old_path, (ten_days_ago, ten_days_ago))
    store.save_snapshot(_evidence("PE1", "recent"))

    result = store.prune(device_name="PE1", keep_days=1)

    assert result == {"removed": 1, "devices": {"PE1": 1}}
    [remaining] = store.list_history("PE1")
    assert remaining["timestamp"] == "recent"


def _iso(epoch_seconds: float) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(epoch_seconds, tz=datetime.timezone.utc).isoformat()


def test_get_store_defaults_to_files(monkeypatch, tmp_path):
    monkeypatch.delenv(EVIDENCE_BACKEND_ENV, raising=False)

    assert DEFAULT_EVIDENCE_BACKEND == "files"
    assert isinstance(get_store(str(tmp_path)), FileEvidenceStore)


def test_get_store_selects_sqlite_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv(EVIDENCE_BACKEND_ENV, "sqlite")

    assert isinstance(get_store(str(tmp_path)), SQLiteEvidenceStore)


def test_get_store_rejects_unknown_backend_loudly(monkeypatch, tmp_path):
    """EER-008c: an unrecognized backend value must fail closed, not silently
    downgrade to the file store. A typo like 'sqlit' used to return
    FileEvidenceStore with no signal at all -- history split across two
    backends depending on which process had the typo, and nothing ever said
    so. Now it raises, naming both valid choices."""

    monkeypatch.setenv(EVIDENCE_BACKEND_ENV, "sqlit")

    with pytest.raises(ValueError, match="files.*sqlite|sqlite.*files"):
        get_store(str(tmp_path))


def test_get_store_rejects_arbitrary_unknown_backend(monkeypatch, tmp_path):
    """Same as above with an unrelated typo, not just a near-miss of 'sqlite'."""

    monkeypatch.setenv(EVIDENCE_BACKEND_ENV, "not-a-real-backend")

    with pytest.raises(ValueError):
        get_store(str(tmp_path))


def test_get_store_positive_controls_still_resolve(monkeypatch, tmp_path):
    """Positive control for the two tests above (OBS-181): both recognized
    values -- including mixed case and incidental whitespace -- still resolve
    to the right class, so the raise above is about *unrecognized* values
    only, not evidence that get_store() now rejects everything."""

    monkeypatch.setenv(EVIDENCE_BACKEND_ENV, "files")
    assert isinstance(get_store(str(tmp_path)), FileEvidenceStore)

    monkeypatch.setenv(EVIDENCE_BACKEND_ENV, "SQLite")
    assert isinstance(get_store(str(tmp_path)), SQLiteEvidenceStore)

    monkeypatch.setenv(EVIDENCE_BACKEND_ENV, "  sqlite  ")
    assert isinstance(get_store(str(tmp_path)), SQLiteEvidenceStore)


def test_get_store_blank_env_means_unset_default(monkeypatch, tmp_path):
    """A set-but-blank value is treated the same as unset (the documented
    default, 'files'), not as an unrecognized typo -- an empty/whitespace-only
    override is not a deliberate choice of a backend."""

    monkeypatch.setenv(EVIDENCE_BACKEND_ENV, "   ")
    assert isinstance(get_store(str(tmp_path)), FileEvidenceStore)


def test_network_tools_snapshot_functions_use_the_selected_backend(monkeypatch, tmp_path):
    from agent_nettools.network_tools import (
        list_snapshot_history,
        load_golden_snapshot,
        load_latest_snapshot,
        prune_snapshots,
        save_golden_snapshot,
        save_snapshot,
    )

    monkeypatch.setenv(EVIDENCE_BACKEND_ENV, "sqlite")

    save_snapshot(_evidence("PE1", "2026-01-01T00-00-00"), base_dir=str(tmp_path))
    save_snapshot(_evidence("PE1", "2026-01-02T00-00-00"), base_dir=str(tmp_path))
    save_golden_snapshot(_evidence("PE1", "golden-capture"), base_dir=str(tmp_path))

    assert (tmp_path / "evidence.db").is_file()
    assert load_latest_snapshot("PE1", base_dir=str(tmp_path))["timestamp"] == "2026-01-02T00-00-00"
    assert load_golden_snapshot("PE1", base_dir=str(tmp_path))["timestamp"] == "golden-capture"
    assert len(list_snapshot_history("PE1", base_dir=str(tmp_path))) == 2

    result = prune_snapshots(device_name="PE1", keep_count=1, base_dir=str(tmp_path))
    assert result["removed"] == 1
    assert len(list_snapshot_history("PE1", base_dir=str(tmp_path))) == 1
    # The golden pin survives pruning regardless of backend.
    assert load_golden_snapshot("PE1", base_dir=str(tmp_path))["timestamp"] == "golden-capture"
