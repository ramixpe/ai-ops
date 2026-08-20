"""Evidence storage backends: JSON files (the default) and SQLite (opt-in).

``evidence/<device>/*.json`` (the pre-Phase-7 store, still the default) grows
without bound and cannot be queried: "how many snapshots does PE1 have from
the last week" means listing a directory and parsing every filename by hand,
and pruning old history means the same walk plus manual deletion. This module
adds a second backend, using only stdlib ``sqlite3`` (no new dependency), that
answers those questions with an indexed query instead of a directory walk --
selected with ``NETTOOLS_EVIDENCE_BACKEND=sqlite``; unset or any other value
keeps the file store, so nothing existing breaks.

Both backends implement the same small ``EvidenceStore`` shape: save a
snapshot, load the latest, save/load the golden (pinned) baseline, list a
device's history, and prune by age and/or count. ``network_tools.py``'s
public functions (``save_snapshot``, ``load_latest_snapshot``, etc.) are thin
wrappers over whichever store ``get_store()`` resolves, so ``diff_evidence``
and everything downstream of it needs no change either way -- both backends
hand back the exact same evidence dict shape that was saved.

``detect_flaps`` deliberately keeps its own file-only history read in
``network_tools.py`` rather than going through this abstraction: flap
detection reads a device's *entire* history on every call, and moving that
onto a second backend is future work, not something this phase's fabric-scale
measurement asked for.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Snapshots (files) or the database (sqlite) land here, relative to the
# working directory unless overridden -- same resolution order as every other
# env-then-default path in this project (``inventory_model.resolve_inventory_path``,
# ``fixtures._fixture_dir``).
DEFAULT_SNAPSHOT_DIR = "evidence"
NETTOOLS_EVIDENCE_DIR_ENV = "NETTOOLS_EVIDENCE_DIR"

# The golden (pinned) snapshot's filename (file backend) / kind (sqlite
# backend). Deliberately not timestamp-shaped so it can never be confused
# with -- or accidentally picked up by -- the lexicographic "latest
# timestamped snapshot" glob.
GOLDEN_SNAPSHOT_FILENAME = "golden.json"

# Backend selection: "files" (default) or "sqlite".
EVIDENCE_BACKEND_ENV = "NETTOOLS_EVIDENCE_BACKEND"
DEFAULT_EVIDENCE_BACKEND = "files"

_SQLITE_FILENAME = "evidence.db"


def _snapshot_dir(base_dir: str | None) -> Path:
    return Path(base_dir or os.getenv(NETTOOLS_EVIDENCE_DIR_ENV) or DEFAULT_SNAPSHOT_DIR)


def _timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_text(path: Path, text: str) -> None:
    """Write via tempfile in the same directory + fsync + os.replace.

    Same-directory matters: os.replace is only atomic within a filesystem, so
    a tempfile created under the platform default (often a different mount,
    e.g. a tmpfs ``/tmp``) would turn "atomic replace" back into a non-atomic
    copy across filesystems. ``save_snapshot``/``save_golden_snapshot``
    (file backend) and ``metrics._persist`` used to ``Path.write_text``
    straight to the final path -- a crash or kill mid-write left truncated
    JSON there, which is exactly what B-474 / DEEP-REVIEW-2026-08-17 §2.4
    found. fsync before the rename so the tempfile's bytes are actually on
    disk, not just sitting in the OS write cache, before the rename that
    makes them visible under the final name.
    """

    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        # os.replace failing (this is the crash-mid-write this helper exists
        # to prevent) must leave the final path exactly as it was -- so clean
        # up the tempfile rather than leave a stray ``.tmp`` file behind, and
        # re-raise so the caller's own error handling (or lack of it, for
        # save_snapshot -- a crash here is meant to be loud) still applies.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# Device names are joined directly into a filesystem path by the file backend
# (``FileEvidenceStore._device_dir``); this bounds what a "device" is allowed
# to look like at the storage boundary itself. Normal callers only ever pass
# names already validated by ``inventory_model`` (alphanumerics, ``_``, ``.``,
# ``-``), so this should never fire for a real caller -- it exists because an
# API that merely trusts its callers' discipline instead of enforcing its own
# contract is not actually guarded (B-474 / DEEP-REVIEW-2026-08-17 §2.4,
# EXPERT-PEER-REVIEW-2026-08-17 P1-07). ".." on its own already fails the
# length-1 fullmatch below in every case except "." and ".." themselves
# (both are valid single/double "." characters under this charset), so those
# two are excluded explicitly rather than relying on the regex to catch them.
_DEVICE_NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def _validate_device_name(device: str) -> None:
    """Raise ``ValueError`` if ``device`` is not a safe storage key.

    Applied in both backends' save paths, not just the file one that has the
    literal path-traversal risk -- so the two backends stay behaviorally
    identical about what counts as a valid device rather than one silently
    accepting what the other would refuse.
    """

    if device in (".", "..") or not _DEVICE_NAME_RE.fullmatch(device):
        raise ValueError(f"invalid device name for evidence storage: {device!r}")


def _read_snapshot_json(path: Path) -> dict[str, Any] | None:
    """Parse one snapshot file, or ``None`` (+ a stderr warning) if it will not parse.

    A truncated or otherwise corrupt file must not crash a reader -- but a
    silent ``None`` would itself be the failure this project treats as its
    worst kind: absence read as health, with nothing to tell an operator that
    a device's history just quietly lost a snapshot. So this always prints a
    warning naming the file before returning ``None`` (B-474 /
    DEEP-REVIEW-2026-08-17 §2.4).
    """

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"WARNING: corrupt snapshot file, skipping: {path} ({exc})", file=sys.stderr)
        return None


def _read_evidence_json(raw: str, *, source: str) -> dict[str, Any] | None:
    """Same corrupt-read guard as ``_read_snapshot_json``, for one SQLite row's
    ``evidence`` column -- ``source`` names the row (path + rowid) in the
    warning since there is no filename to point at."""

    try:
        return json.loads(raw)
    except ValueError as exc:
        print(f"WARNING: corrupt snapshot row, skipping: {source} ({exc})", file=sys.stderr)
        return None


def _retained(
    items: list[Any],
    *,
    keep_days: float | None,
    keep_count: int | None,
    age_of: Any,
    now: float,
) -> set[int]:
    """Return the *indices* into ``items`` (oldest-first) that survive pruning.

    A snapshot survives if it satisfies *either* configured rule -- "among the
    most recent ``keep_count``" or "younger than ``keep_days``" -- matching
    the common retention idiom (restic/borg-style: keep the union of what any
    rule wants to keep, delete only what every rule agrees is expendable).
    Neither rule configured means nothing is pruned, not everything.
    """

    if keep_days is None and keep_count is None:
        return set(range(len(items)))

    retained: set[int] = set()
    if keep_count is not None and keep_count > 0:
        retained.update(range(max(0, len(items) - keep_count), len(items)))
    if keep_days is not None:
        cutoff = now - keep_days * 86400
        retained.update(index for index, item in enumerate(items) if age_of(item) >= cutoff)
    return retained


class EvidenceStore(ABC):
    """Common shape both backends implement.

    ``list_history`` returns full evidence dicts, oldest first, golden
    excluded -- the shape flap detection and pruning both need. ``prune``
    never touches a golden snapshot; it is a deliberately pinned baseline, not
    history.
    """

    @abstractmethod
    def save_snapshot(self, evidence: dict[str, Any]) -> str:
        """Persist one evidence collection as a new timestamped entry. Returns its identifier."""

    @abstractmethod
    def load_latest_snapshot(self, device_name: str) -> dict[str, Any] | None:
        """Return a device's most recent timestamped snapshot, or ``None``."""

    @abstractmethod
    def save_golden_snapshot(self, evidence: dict[str, Any]) -> str:
        """Pin one evidence collection as the device's golden baseline. Returns its identifier."""

    @abstractmethod
    def load_golden_snapshot(self, device_name: str) -> dict[str, Any] | None:
        """Return a device's pinned golden snapshot, or ``None`` if never pinned."""

    @abstractmethod
    def list_history(self, device_name: str) -> list[dict[str, Any]]:
        """Return a device's timestamped snapshots, oldest first, golden excluded."""

    @abstractmethod
    def list_devices(self) -> list[str]:
        """Return every device name with at least one stored timestamped snapshot."""

    @abstractmethod
    def prune(
        self,
        *,
        device_name: str | None = None,
        keep_days: float | None = None,
        keep_count: int | None = None,
    ) -> dict[str, Any]:
        """Delete timestamped snapshots outside the retention window. Never touches golden.

        Returns ``{"removed": total, "devices": {name: removed_count, ...}}``.
        """


class FileEvidenceStore(EvidenceStore):
    """The original ``evidence/<device>/<timestamp>.json`` layout."""

    def __init__(self, base_dir: str | None = None) -> None:
        self._root = _snapshot_dir(base_dir)

    def _device_dir(self, device_name: str) -> Path:
        """The one place a device name becomes a path in this backend.

        EER-001: `_validate_device_name` used to be applied on the SAVE paths
        only, so `prune(device_name="../outside")` joined unchecked input
        under the root and unlinked `*.json` OUTSIDE it -- reproduced against
        a canary before this fix. Every read, list and prune call reaches the
        filesystem through this method, so validating here closes the whole
        family at once rather than at each call site, where the next new
        caller would have to remember.

        Two independent gates, deliberately: the charset check rejects the
        traversal *semantically* (a device is `[A-Za-z0-9_.-]{1,64}`, never a
        path), and the resolved-containment assertion below is the structural
        backstop -- it holds even if the charset is one day widened, or a
        symlink inside the root points out of it. The review's own guidance:
        treat containment as defence in depth, not as a replacement for
        semantic validation.
        """

        _validate_device_name(device_name)
        candidate = (self._root / device_name).resolve()
        root = self._root.resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError(
                f"device directory escapes the evidence root: {device_name!r}"
            )
        return self._root / device_name

    def _timestamped_paths(self, device_name: str) -> list[Path]:
        directory = self._device_dir(device_name)
        if not directory.is_dir():
            return []
        return sorted(
            path for path in directory.glob("*.json") if path.name != GOLDEN_SNAPSHOT_FILENAME
        )

    def save_snapshot(self, evidence: dict[str, Any]) -> str:
        device = str(evidence.get("device", "unknown"))
        _validate_device_name(device)
        stamp = _timestamp_now().replace(":", "-")
        directory = self._device_dir(device)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{stamp}.json"
        _atomic_write_text(path, json.dumps(evidence, indent=2))
        return str(path)

    def load_latest_snapshot(self, device_name: str) -> dict[str, Any] | None:
        paths = self._timestamped_paths(device_name)
        if not paths:
            return None
        return _read_snapshot_json(paths[-1])

    def save_golden_snapshot(self, evidence: dict[str, Any]) -> str:
        device = str(evidence.get("device", "unknown"))
        _validate_device_name(device)
        directory = self._device_dir(device)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / GOLDEN_SNAPSHOT_FILENAME
        _atomic_write_text(path, json.dumps(evidence, indent=2))
        return str(path)

    def load_golden_snapshot(self, device_name: str) -> dict[str, Any] | None:
        path = self._device_dir(device_name) / GOLDEN_SNAPSHOT_FILENAME
        if not path.is_file():
            return None
        return _read_snapshot_json(path)

    def list_history(self, device_name: str) -> list[dict[str, Any]]:
        # A corrupt snapshot must not erase the rest of a device's history --
        # skip it (with its own warning from `_read_snapshot_json`) and keep
        # going, rather than letting one bad file take down the whole read.
        parsed = (_read_snapshot_json(path) for path in self._timestamped_paths(device_name))
        return [snapshot for snapshot in parsed if snapshot is not None]

    def list_devices(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return sorted(
            entry.name
            for entry in self._root.iterdir()
            if entry.is_dir() and self._timestamped_paths(entry.name)
        )

    def prune(
        self,
        *,
        device_name: str | None = None,
        keep_days: float | None = None,
        keep_count: int | None = None,
    ) -> dict[str, Any]:
        devices = [device_name] if device_name is not None else self.list_devices()
        now = time.time()
        removed_by_device: dict[str, int] = {}

        for device in devices:
            paths = self._timestamped_paths(device)
            # File mtime approximates capture time -- the timestamp is also
            # encoded in the filename, but with ":" already replaced by "-" to
            # be filesystem-safe, reversing that unambiguously (a "-" also
            # separates the date and appears in the UTC offset) is not worth
            # it when the filesystem already tracks a good-enough time.
            retained = _retained(
                paths,
                keep_days=keep_days,
                keep_count=keep_count,
                age_of=lambda path: path.stat().st_mtime,
                now=now,
            )
            to_delete = [path for index, path in enumerate(paths) if index not in retained]
            for path in to_delete:
                path.unlink(missing_ok=True)
            if to_delete:
                removed_by_device[device] = len(to_delete)

        return {"removed": sum(removed_by_device.values()), "devices": removed_by_device}


class SQLiteEvidenceStore(EvidenceStore):
    """A queryable evidence store: one ``evidence.db`` under the snapshot directory.

    ``sqlite3`` only (stdlib) -- no new dependency. One table, one composite
    index on ``(device, timestamp)`` so "latest for this device" and "this
    device's history" are both indexed lookups, not a directory walk. Exactly
    one row per device has ``kind = "golden"``; every other row is
    ``kind = "snapshot"``. A connection is opened and closed per call: this is
    a single-user lab tool, not a service under write concurrency, so there is
    no benefit to holding a long-lived connection open.
    """

    def __init__(self, base_dir: str | None = None) -> None:
        self._path = _snapshot_dir(base_dir) / _SQLITE_FILENAME
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._path))
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    evidence TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_snapshots_device_timestamp "
                "ON snapshots (device, timestamp)"
            )
            # Migration for databases created before B-474: the old
            # save_golden_snapshot DELETEd and INSERTed through two separate
            # connections, so a DB that predates this fix could already hold
            # more than one 'golden' row for a device -- the exact defect
            # DEEP-REVIEW-2026-08-17 §2.4 found (and load_golden_snapshot's
            # own `ORDER BY id DESC` meant a duplicate was invisible, so
            # nothing would have surfaced it by observation). A UNIQUE index
            # cannot be created over data that already violates it, so any
            # pre-existing duplicates are collapsed down to the highest id
            # (the most recently pinned, and the one `id DESC` was already
            # treating as "the" golden) before the index below is added.
            connection.execute(
                """
                DELETE FROM snapshots
                WHERE kind = 'golden'
                  AND id NOT IN (
                      SELECT MAX(id) FROM snapshots WHERE kind = 'golden' GROUP BY device
                  )
                """
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshots_one_golden "
                "ON snapshots (device) WHERE kind = 'golden'"
            )

    def save_snapshot(self, evidence: dict[str, Any]) -> str:
        return self._insert(evidence, kind="snapshot")

    def save_golden_snapshot(self, evidence: dict[str, Any]) -> str:
        # DELETE + INSERT in one connection/transaction. The previous version
        # deleted the old pin on one connection and inserted the new row via
        # `_insert`'s own separate connection -- a crash between the two left
        # the device with *no* golden row, not a duplicate (B-474 /
        # DEEP-REVIEW-2026-08-17 §2.4). `_insert` is deliberately not reused
        # here for exactly that reason: it opens and commits its own
        # connection, which is the bug.
        device = str(evidence.get("device", "unknown"))
        _validate_device_name(device)
        timestamp = str(evidence.get("timestamp") or _timestamp_now())
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM snapshots WHERE device = ? AND kind = 'golden'", (device,)
            )
            cursor = connection.execute(
                "INSERT INTO snapshots (device, timestamp, kind, evidence) VALUES (?, ?, ?, ?)",
                (device, timestamp, "golden", json.dumps(evidence)),
            )
            row_id = cursor.lastrowid
        return f"sqlite:{self._path}#{row_id}"

    def _insert(self, evidence: dict[str, Any], *, kind: str) -> str:
        device = str(evidence.get("device", "unknown"))
        _validate_device_name(device)
        timestamp = str(evidence.get("timestamp") or _timestamp_now())
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO snapshots (device, timestamp, kind, evidence) VALUES (?, ?, ?, ?)",
                (device, timestamp, kind, json.dumps(evidence)),
            )
            row_id = cursor.lastrowid
        return f"sqlite:{self._path}#{row_id}"

    def load_latest_snapshot(self, device_name: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, evidence FROM snapshots WHERE device = ? AND kind = 'snapshot' "
                "ORDER BY timestamp DESC, id DESC LIMIT 1",
                (device_name,),
            ).fetchone()
        if row is None:
            return None
        return _read_evidence_json(row["evidence"], source=f"{self._path}#{row['id']}")

    def load_golden_snapshot(self, device_name: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, evidence FROM snapshots WHERE device = ? AND kind = 'golden' "
                "ORDER BY id DESC LIMIT 1",
                (device_name,),
            ).fetchone()
        if row is None:
            return None
        return _read_evidence_json(row["evidence"], source=f"{self._path}#{row['id']}")

    def list_history(self, device_name: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, evidence FROM snapshots WHERE device = ? AND kind = 'snapshot' "
                "ORDER BY timestamp ASC, id ASC",
                (device_name,),
            ).fetchall()
        # Same "skip the corrupt one, keep the rest" behavior as the file
        # backend's list_history -- see _read_evidence_json.
        parsed = (
            _read_evidence_json(row["evidence"], source=f"{self._path}#{row['id']}")
            for row in rows
        )
        return [snapshot for snapshot in parsed if snapshot is not None]

    def list_devices(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT device FROM snapshots WHERE kind = 'snapshot' ORDER BY device"
            ).fetchall()
        return [row["device"] for row in rows]

    def prune(
        self,
        *,
        device_name: str | None = None,
        keep_days: float | None = None,
        keep_count: int | None = None,
    ) -> dict[str, Any]:
        # Parity with the file backend (EER-001). This backend has no path to
        # traverse -- every query is parameterised -- so an invalid name here
        # would simply match no rows rather than escape anything. It is
        # refused anyway so the two backends agree on what a device IS,
        # which is the same reasoning `_validate_device_name`'s own docstring
        # already gives for validating on both save paths.
        if device_name is not None:
            _validate_device_name(device_name)
        devices = [device_name] if device_name is not None else self.list_devices()
        now = time.time()
        removed_by_device: dict[str, int] = {}

        with self._connect() as connection:
            for device in devices:
                rows = connection.execute(
                    "SELECT id, timestamp FROM snapshots WHERE device = ? AND kind = 'snapshot' "
                    "ORDER BY timestamp ASC, id ASC",
                    (device,),
                ).fetchall()
                retained = _retained(
                    rows,
                    keep_days=keep_days,
                    keep_count=keep_count,
                    age_of=_row_epoch_seconds,
                    now=now,
                )
                ids_to_delete = [
                    row["id"] for index, row in enumerate(rows) if index not in retained
                ]
                if ids_to_delete:
                    placeholders = ", ".join("?" for _ in ids_to_delete)
                    connection.execute(
                        f"DELETE FROM snapshots WHERE id IN ({placeholders})", ids_to_delete
                    )
                    removed_by_device[device] = len(ids_to_delete)

        return {"removed": sum(removed_by_device.values()), "devices": removed_by_device}


def _row_epoch_seconds(row: sqlite3.Row) -> float:
    """Return a snapshot row's own timestamp as epoch seconds, for age comparison.

    Returns ``+inf`` (never pruned by age -- always ">= cutoff") for a
    timestamp this process cannot parse, rather than raising: older or
    hand-built evidence is not guaranteed to carry a real ISO-8601 timestamp.
    """

    try:
        parsed = datetime.fromisoformat(str(row["timestamp"]))
    except ValueError:
        return float("inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def get_store(base_dir: str | None = None) -> EvidenceStore:
    """Return the configured evidence store: files (default) or sqlite.

    Selected by ``NETTOOLS_EVIDENCE_BACKEND``; any value other than
    ``"sqlite"`` (including unset) keeps the pre-Phase-7 file store, so
    nothing existing changes behavior by default.
    """

    backend = os.getenv(EVIDENCE_BACKEND_ENV, DEFAULT_EVIDENCE_BACKEND).strip().lower()
    if backend == "sqlite":
        return SQLiteEvidenceStore(base_dir)
    return FileEvidenceStore(base_dir)
