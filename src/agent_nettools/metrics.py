"""Operational metrics: per-device collection outcomes, latency, retries, and
health verdict counts by severity -- exposed as JSON or Prometheus text
exposition format via ``nettools metrics``.

Why this exists
-----------------
Every check in this package already reports its own outcome (``status`` in
the result envelope) and the audit log (``NETTOOLS_LOG``) already records
each command's own duration -- but nothing aggregates across calls to answer
fleet-scale operational questions cheaply: "what fraction of PE* devices
failed to collect in the last run", "how much retry churn is this network
generating", "how many devices are sitting at critical severity right now".
This module is that aggregation layer, written from scratch with the
standard library only (Prometheus text exposition format is a simple
line-based text format -- no client library needed to produce it).

Where it is wired in (and, just as importantly, where it is not)
--------------------------------------------------------------------
``network_tools._netmiko_send_commands`` is the single function every real
device connection flows through (see its own docstring), so it is the one
hook point that covers ``run_intent``, ``run_template``, and
``collect_evidence`` alike without duplicating a call at each of their call
sites -- exactly the same reasoning ``_audit_log`` already follows, and it is
recorded there for the same reason: the ``sender=`` test-injection path
never touches this function, so unit tests that use ``sender=`` do not
pollute metrics, matching the audit log's existing "real transport activity
only" behavior. ``health.evaluate_device`` records one verdict-severity
count per device evaluated, which also covers ``evaluate_fabric`` (it calls
``evaluate_device`` once per device) with a single hook point.

Nothing about a result envelope changes because of this module -- every
``record_*`` call is a side effect on a counter, never a transformation of
what a tool returns. This is deliberately observational only.

One process per CLI invocation, and what that means for persistence
------------------------------------------------------------------------
``nettools <command>`` is a fresh process every time, so in-memory counters
alone would make ``nettools metrics`` always report zero except for activity
that happened moments earlier in the *same* process (still meaningful for a
single call that touches many devices, e.g. ``nettools fabric bgp``, and for
the MCP server, which is long-lived for its whole session). Setting
``NETTOOLS_METRICS_FILE`` opts into on-disk persistence -- the path is
resolved lazily on first use (never at import time, so it does not race
``cli.py``'s own ``.env`` loading), and best-effort written back after every
mutation via ``_atomic_write_text``, the same "swallow a failure, never
raise" idiom ``evidence_store.py`` and the audit log already use. Unset (the
default) means metrics are in-memory only for the current process -- nothing
is written to disk unless a caller opts in, matching ``NETTOOLS_LOG``'s own
default-off posture, and specifically to avoid a metrics file appearing
unbidden in a user's working directory (or the test suite's).

Cross-process correctness (EER-011)
------------------------------------
``admission.py``'s own docstring explains why this matters concretely: one
root cause producing forty syslog lines means forty separate ``nettools`` OS
processes, each recording its own collections/verdicts/paraphrases into the
*same* ``NETTOOLS_METRICS_FILE`` -- plus the long-lived MCP server, which
holds counters in memory for its whole session alongside every short-lived
CLI invocation. A ``threading.Lock`` only ever protects one process's own
memory; two processes each reading ``success: 10`` and each writing back
``success: 11`` lose one increment silently, and ``_atomic_write_text``
guarantees the file is never *torn* by that race, which is exactly why the
loss doesn't announce itself as a parse error.

So every mutation (``record_collection``/``record_paraphrase``/
``record_verdict``) that has a file configured now holds an *exclusive
cross-process* ``fcntl.flock`` across the entire reload-mutate-persist
sequence -- re-reading the file's current content fresh under that lock
before applying its own delta, not trusting whatever baseline this process
cached earlier. ``snapshot()``/``render_json()``/``render_prometheus()`` take
the same lock to reload fresh too, so a long-lived process (the MCP server)
never reports a baseline that is stale relative to what other processes have
since written.

The lock is a ``<path>.lock`` sidecar file, not ``path`` itself -- see
``_cross_process_lock``'s docstring for why ``_atomic_write_text``'s
``os.replace`` makes locking ``path`` directly unsafe to combine with an
atomic write.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

# Shared with the evidence store's file backend rather than duplicated: both
# modules write JSON straight to a final path and both were named in B-474 /
# DEEP-REVIEW-2026-08-17 §2.4 for the same reason (a crash mid-write leaves
# truncated JSON). No import cycle -- evidence_store.py does not import this
# module, so this direction is safe.
from .evidence_store import _atomic_write_text

NETTOOLS_METRICS_FILE_ENV = "NETTOOLS_METRICS_FILE"

SEVERITIES: tuple[str, ...] = ("ok", "info", "warning", "unreachable", "critical")

_EMPTY_DEVICE_STATS: dict[str, float] = {
    "success": 0,
    "failure": 0,
    "latency_total_s": 0.0,
    "latency_count": 0,
    "retries_total": 0,
}


def _metrics_path(explicit: str | None) -> Path | None:
    """Resolve the persistence path, or ``None`` for in-memory-only mode.

    Read lazily by every caller (never cached at import time): the env var is
    only meaningful once ``.env`` has actually been loaded, which happens
    inside ``cli.main()``/``mcp_server.server`` -- after this module is
    already imported as a dependency of ``network_tools``/``health``.
    """

    raw = explicit or os.getenv(NETTOOLS_METRICS_FILE_ENV, "").strip()
    return Path(raw) if raw else None


#: The paraphrase grounding outcomes worth counting (B-457). Mirrors
#: `investigation`'s status constants without importing them -- `metrics` sits
#: below the investigation layer and must not depend upward.
PARAPHRASE_OUTCOMES: tuple[str, ...] = (
    "emitted", "withheld", "coverage_limited", "not_attempted",
)


def _lock_path(path: Path) -> Path:
    """The sidecar lock file for one metrics file -- see
    ``_cross_process_lock``'s docstring for why this is a separate file
    rather than ``path`` itself."""

    return path.with_name(path.name + ".lock")


@contextlib.contextmanager
def _cross_process_lock(path: Path):
    """Hold an exclusive, blocking ``fcntl.flock`` across one read-modify-write
    of ``path`` (EER-011). Reuses the idiom ``admission.py``'s ``_held_slot``/
    ``_check_and_record_window`` already establish for this exact problem --
    a filesystem lock, not an in-process primitive, because a
    ``threading.Lock`` cannot see across the OS-process boundary this project
    is actually deployed across (see ``admission.py``'s module docstring: one
    root cause can mean forty separate ``nettools`` processes).

    Why a **sidecar** ``<path>.lock`` file, not ``path`` itself: ``_persist``
    writes via ``_atomic_write_text``, which is a tempfile + ``os.replace`` --
    and ``os.replace`` swaps out the *inode* the final name points at.
    ``flock`` is scoped to the open file description, not the name, so a lock
    held on an fd opened against ``path`` keeps guarding the *old* inode after
    a replace; the next opener of ``path`` gets a fresh fd on the *new* inode,
    unlocked, and two writers can then both believe they hold the lock. A
    lock file that this code never replaces -- only ever opens and flocks --
    sidesteps that: the thing being locked never changes identity underfoot.

    Trade-off accepted: this adds one always-present companion file per
    metrics file that nothing ever deletes (the same permanent-lock-file
    shape ``admission.py``'s own device/fabric slot files already use) in
    exchange for keeping ``_atomic_write_text``'s crash-safety guarantee
    (B-474: no reader ever observes a torn/truncated write) *together with*
    cross-process mutual exclusion. The alternative -- lock and rewrite
    ``path`` in place, ``admission._check_and_record_window``'s idiom --
    avoids the extra file but gives back exactly the crash-safety
    ``_atomic_write_text`` was added for: a kill -9 mid in-place-write would
    once again leave truncated JSON behind. Durability was chosen over
    saving one file.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(_lock_path(path), "a+b")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


class MetricsCollector:
    """Thread-safe counters for device collections and health verdicts.

    Safe under ``check_fabric``'s thread pool (a lock guards every mutation).
    In-memory state is always the source of truth for the running process
    between mutations; an on-disk file, if configured, is re-read fresh under
    a cross-process lock immediately before every mutation and every read
    (EER-011) -- see the module docstring's "Cross-process correctness"
    section for why a once-per-process cached baseline silently lost
    concurrent updates.
    """

    def __init__(self, path: str | None = None) -> None:
        self._lock = threading.Lock()
        self._explicit_path = path
        self._collections: dict[str, dict[str, float]] = {}
        self._verdicts: dict[str, int] = dict.fromkeys(SEVERITIES, 0)
        #: Paraphrase grounding outcomes (B-457). A **tool-health** signal, and
        #: deliberately not a network one -- reviewer B's framing. A rising
        #: `withheld` rate means the model layer has stopped producing
        #: verifiable prose; it says nothing about the fabric, and wiring it to
        #: anything that pages would be the conflation the exit-code scheme
        #: exists to prevent.
        self._paraphrases: dict[str, int] = dict.fromkeys(PARAPHRASE_OUTCOMES, 0)

    def _apply_raw_document(self, raw: Any) -> None:
        """Replace in-memory state with one parsed on-disk metrics document.

        Shared by every locked reload (EER-011): the document just read is
        authoritative for what every cooperating process has persisted so
        far, so this *replaces* ``self._collections`` wholesale (a device
        absent from a fresh read is absent because no persisted mutation
        currently claims it -- keeping a stale in-memory entry around would
        be exactly the kind of drift this reload exists to remove) rather
        than merging key-by-key. ``verdicts``/``paraphrases`` already iterate
        every known key (``SEVERITIES``/``PARAPHRASE_OUTCOMES``) so they are
        naturally a full overwrite too.
        """

        collections = raw.get("collections") if isinstance(raw, dict) else None
        if isinstance(collections, dict):
            self._collections = {
                str(name): {
                    "success": float(stats.get("success", 0)),
                    "failure": float(stats.get("failure", 0)),
                    "latency_total_s": float(stats.get("latency_total_s", 0.0)),
                    "latency_count": float(stats.get("latency_count", 0)),
                    "retries_total": float(stats.get("retries_total", 0)),
                }
                for name, stats in collections.items()
                if isinstance(stats, dict)
            }
        verdicts = raw.get("verdicts") if isinstance(raw, dict) else None
        if isinstance(verdicts, dict):
            for severity in SEVERITIES:
                self._verdicts[severity] = int(verdicts.get(severity, 0))
        # Its own guard, not nested under the verdicts one: a file carrying
        # paraphrase counts and no verdicts would otherwise drop them silently.
        paraphrases = raw.get("paraphrases") if isinstance(raw, dict) else None
        if isinstance(paraphrases, dict):
            for outcome in PARAPHRASE_OUTCOMES:
                self._paraphrases[outcome] = int(paraphrases.get(outcome, 0))

    def _reload_from_disk_locked(self) -> None:
        """Refresh in-memory state from what is actually on disk right now.

        Must only be called while ``_cross_process_lock`` is held, and
        immediately before applying one mutation's delta (or building a
        snapshot) -- this is what makes the read visible to this process
        the *latest* one, not a baseline some earlier call cached (EER-011).
        A missing or unparsable file (nothing persisted yet, or a corrupt
        read) leaves the current in-memory state exactly as it was.
        """

        path = _metrics_path(self._explicit_path)
        if path is None:
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self._apply_raw_document(raw)

    def _persist(self) -> None:
        path = _metrics_path(self._explicit_path)
        if path is None:
            return
        try:
            # Atomic (tempfile + fsync + os.replace), not a direct write --
            # this file is rewritten whole on every mutation (see the module
            # docstring), so a crash mid-write used to leave truncated JSON
            # that the next process's reload would then fail to parse (B-474
            # / DEEP-REVIEW-2026-08-17 §2.4). Called only while
            # `_cross_process_lock` is held (see `_mutate`), so this write is
            # never raced by another process's read-modify-write.
            _atomic_write_text(
                path,
                json.dumps(
                    {"collections": self._collections, "verdicts": self._verdicts,
                     "paraphrases": self._paraphrases}, indent=2
                ),
            )
        except OSError:
            pass  # Best-effort, same as the audit log: never break a caller over this.

    def _mutate(self, apply_delta: Callable[[], None]) -> None:
        """Run one state change under the in-process lock and, when a file is
        configured, under the cross-process lock too (EER-011).

        With a file configured: reload fresh from disk *first* (folding in
        whatever any other process has since written), then apply this
        call's own delta on top of that fresh state, then persist -- all
        without releasing the cross-process lock, so no other process's
        write can land between this reload and this write. That ordering is
        the whole fix: two processes each starting from a stale
        ``success: 10`` and each writing ``success: 11`` is exactly the lost
        update this closes.

        Without a file configured, this is unchanged from before: mutate
        in-memory state only, no I/O at all.
        """

        with self._lock:
            path = _metrics_path(self._explicit_path)
            if path is None:
                apply_delta()
                return
            with _cross_process_lock(path):
                self._reload_from_disk_locked()
                apply_delta()
                self._persist()

    def record_collection(
        self, device: str, *, success: bool, duration_s: float, retries: int = 0
    ) -> None:
        """Record one device collection's outcome (one call to ``_netmiko_send_commands``)."""

        def apply_delta() -> None:
            stats = self._collections.setdefault(device, dict(_EMPTY_DEVICE_STATS))
            stats["success" if success else "failure"] += 1
            stats["latency_total_s"] += duration_s
            stats["latency_count"] += 1
            stats["retries_total"] += retries

        self._mutate(apply_delta)

    def record_paraphrase(self, outcome: str) -> None:
        """Record one paraphrase grounding outcome. Unknown outcomes are ignored.

        **Why this exists.** Before B-439 a report that failed grounding exited
        2, so a systematic grounding regression was visible in exit codes. The
        authoritative report is now rendered from typed fields and cannot fail,
        so a rejected *paraphrase* correctly no longer changes the exit code --
        and that removed the only signal a systematic regression had (B-457).

        The status was already in the payload and on stderr. **A field nobody
        aggregates is not detection**, which is the whole content of the item: a
        per-run field tells you about one run, and the failure this guards
        against is a change in the *rate*.
        """

        if outcome not in PARAPHRASE_OUTCOMES:
            return

        def apply_delta() -> None:
            self._paraphrases[outcome] += 1

        self._mutate(apply_delta)

    def record_verdict(self, severity: str) -> None:
        """Record one device's health verdict severity. Unknown severities are ignored."""

        if severity not in SEVERITIES:
            return

        def apply_delta() -> None:
            self._verdicts[severity] += 1

        self._mutate(apply_delta)

    def snapshot(self) -> dict[str, Any]:
        """Return the current aggregated state as plain JSON-serializable data.

        When a file is configured this reloads fresh under the cross-process
        lock first (EER-011), the same as a mutation -- otherwise a
        long-lived process (the MCP server, for its whole session) would keep
        reporting the baseline it happened to read at its first call, stale
        relative to every CLI invocation that persisted since.
        """

        with self._lock:
            path = _metrics_path(self._explicit_path)
            if path is not None:
                with _cross_process_lock(path):
                    self._reload_from_disk_locked()
            devices: dict[str, Any] = {}
            for name, stats in sorted(self._collections.items()):
                count = stats["latency_count"]
                devices[name] = {
                    "success": int(stats["success"]),
                    "failure": int(stats["failure"]),
                    "retries_total": int(stats["retries_total"]),
                    "latency_total_s": round(stats["latency_total_s"], 6),
                    "latency_count": int(count),
                    "latency_avg_s": round(stats["latency_total_s"] / count, 6) if count else None,
                }
            verdicts = dict(self._verdicts)
            paraphrases = dict(self._paraphrases)

        return {
            "collections": devices,
            "totals": {
                "success": sum(d["success"] for d in devices.values()),
                "failure": sum(d["failure"] for d in devices.values()),
                "retries_total": sum(d["retries_total"] for d in devices.values()),
            },
            "verdicts": verdicts,
            "paraphrases": paraphrases,
        }

    def reset(self) -> None:
        """Clear all state, in-memory and (if configured) on disk. Test use only.

        Persists the wipe under the same cross-process lock a mutation takes
        (EER-011), so this cannot race a concurrent process's own
        reload-mutate-persist and land as a torn interleaving. That said,
        this is still a wipe of a **shared** file: if ``NETTOOLS_METRICS_FILE``
        happens to point at a real, shared path rather than a test's own
        ``tmp_path``, ``reset()`` clears every other process's counters too,
        not just this one's. That hazard predates the flock fix and is
        orthogonal to it -- flagged for the backlog, not fixed here (see the
        EER-011 report).
        """

        with self._lock:
            self._collections = {}
            self._verdicts = dict.fromkeys(SEVERITIES, 0)
            self._paraphrases = dict.fromkeys(PARAPHRASE_OUTCOMES, 0)
            path = _metrics_path(self._explicit_path)
            if path is None:
                return
            with _cross_process_lock(path):
                self._persist()

    def render_json(self) -> str:
        return json.dumps(self.snapshot(), indent=2)

    def render_prometheus(self) -> str:
        return render_prometheus(self.snapshot())


def _escape_label_value(value: str) -> str:
    """Escape a Prometheus label value per the text exposition format's rules."""

    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_prometheus(snapshot: dict[str, Any]) -> str:
    """Render one metrics snapshot as Prometheus text exposition format.

    Hand-written, stdlib only -- the format is deliberately simple (one
    ``# HELP``/``# TYPE`` pair per metric family, then one line per label
    combination), so no client library is needed just to produce it.
    """

    lines: list[str] = []

    def emit(name: str, help_text: str, metric_type: str, rows: list[tuple[str, float]]) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {metric_type}")
        lines.extend(f"{name}{labels} {value}" for labels, value in rows)

    devices = snapshot["collections"]

    emit(
        "nettools_device_collections_total",
        "Device collection attempts by outcome.",
        "counter",
        [
            (f'{{device="{_escape_label_value(name)}",outcome="{outcome}"}}', stats[outcome])
            for name, stats in devices.items()
            for outcome in ("success", "failure")
        ],
    )
    emit(
        "nettools_device_collection_latency_seconds_sum",
        "Sum of per-device collection latency, in seconds.",
        "counter",
        [
            (f'{{device="{_escape_label_value(name)}"}}', stats["latency_total_s"])
            for name, stats in devices.items()
        ],
    )
    emit(
        "nettools_device_collection_latency_seconds_count",
        "Count of per-device collection latency observations.",
        "counter",
        [
            (f'{{device="{_escape_label_value(name)}"}}', stats["latency_count"])
            for name, stats in devices.items()
        ],
    )
    emit(
        "nettools_device_retries_total",
        "Total retries consumed during device collection.",
        "counter",
        [
            (f'{{device="{_escape_label_value(name)}"}}', stats["retries_total"])
            for name, stats in devices.items()
        ],
    )
    emit(
        "nettools_health_verdicts_total",
        "Health verdicts recorded, by severity.",
        "counter",
        [(f'{{severity="{severity}"}}', count) for severity, count in snapshot["verdicts"].items()],
    )
    emit(
        "nettools_paraphrase_outcomes_total",
        "Model paraphrase grounding outcomes. A TOOL-HEALTH signal: a rising "
        "withheld rate means the model layer stopped producing verifiable prose, "
        "and says nothing about the network. Never page on this.",
        "counter",
        [(f'{{outcome="{outcome}"}}', count)
         for outcome, count in snapshot.get("paraphrases", {}).items()],
    )

    return "\n".join(lines) + "\n"


# The process-wide collector network_tools.py / health.py record into.
# Constructing it does no I/O (persistence, if any, is resolved lazily on
# first use -- see the module docstring), so a module-level instance is safe
# to create at import time.
default_collector = MetricsCollector()


def record_collection(device: str, *, success: bool, duration_s: float, retries: int = 0) -> None:
    default_collector.record_collection(device, success=success, duration_s=duration_s, retries=retries)


def record_paraphrase(outcome: str) -> None:
    default_collector.record_paraphrase(outcome)


def record_verdict(severity: str) -> None:
    default_collector.record_verdict(severity)


def snapshot() -> dict[str, Any]:
    return default_collector.snapshot()


def render_json() -> str:
    return default_collector.render_json()


def render_prometheus_text() -> str:
    return default_collector.render_prometheus()


def reset() -> None:
    """Reset the process-wide default collector. Test use only."""

    default_collector.reset()
