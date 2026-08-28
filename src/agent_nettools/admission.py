"""Admission control (B-444) and active-probe budgeting (B-408).

Both rows sat `DEFERRED` -- B-444 "until any multi-device concurrent
deployment", B-408 "until Stage 2 -- probe budgeting matters under event
storms". The deferral condition is being built now: syslog firing
`nettools investigate` automatically, unpaced. `event_routing.py`'s own
docstring says it plainly -- "It listens on nothing, calls nothing, retries
nothing... the receiving process is the operator's infrastructure choice",
and `RoutingDecision.suggested_command()` returns a plain argv list, i.e.
**one investigation is one separate `nettools` OS process**, spawned by
whatever the operator wires up (systemd, n8n, a relay). One root cause
producing forty syslog lines means forty such processes, racing on nine
devices. An in-memory `threading.Semaphore` inside one Python process cannot
see across that boundary -- so both gates below are filesystem `flock`
locks, not in-process primitives, because that is the only mechanism that
is actually true to how this system is deployed.

What was measured, against the live lab, 2026-08-19
-----------------------------------------------------
`collect_evidence()` already opens exactly one SSH session per device (the
existing, deliberate fan-out control -- see its own module docstring in
`network_tools.py`). The question this module answers is how many of *those*
single sessions may run at once.

* Same-device concurrency (N independent `collect_evidence()` calls against
  one device, `PE1`): N=2 completed cleanly (one sample). N=3: 1 of 3 failed
  outright with a device-side transport error (`TCP connection ... failed`).
  N=4: 2 of 4 failed (`Error reading SSH protocol banner`, `Connection reset
  by peer`, `Paramiko: 'No existing session' error`). N=8: 3 of 8 failed the
  same way. These are not retries recovering -- `_with_retries` already ran
  its full budget on each and the connection never came up in time, because
  every one of the other concurrent sessions was contending for the same
  small pool the device (or its container host) offers. This is the same
  shape the build's own field note already reported: "six agents working
  concurrently against this fabric produced real SSH connection failures
  that cleared on retry" -- except a retry only clears it if the *other*
  contending sessions have finished by the time the retry fires, which an
  automated, unpaced trigger cannot promise.

  -> `NETTOOLS_MAX_CONCURRENT_PER_DEVICE` defaults to **1**. Not a round
  number: it is the largest value this measurement never saw fail, and the
  task framing's own "obvious floor" independently agrees -- a second
  concurrent read of the same device cannot learn anything the first
  isn't already about to report, so serializing it costs nothing real.

* Cross-device concurrency (N `collect_evidence()` calls, one each against N
  *different* devices): N=2 and N=4 both completed in a tight ~1.8s band
  with zero failures. N=8 and N=9 (the whole fabric) also completed with
  zero failures, but latency spread badly -- individual collections ranging
  from ~1.8s to as long as ~42.7s (N=8) / ~31.2s (N=9), roughly a 20x tail
  against the clean band. No connection was refused at the transport level
  at 8 or 9; the cost showed up entirely as queueing-shaped latency, most
  plausibly host-level contention across however many XRd containers share
  the lab's underlying compute.

  -> `NETTOOLS_MAX_CONCURRENT_FABRIC` defaults to **4**: the largest
  measured width that stayed inside the clean, tight latency band. Above it,
  nothing broke outright in this measurement, but nothing about that is
  guaranteed at higher event-storm widths this lab cannot exercise (the lab
  only has 9 devices) -- 4 is the boundary this data actually supports, not
  an extrapolation past it.

Because the per-device cap already forces at most one in-flight collection
per device, the fabric-wide cap can never be *defeated* by the per-device
one -- it only ever adds a second, independent ceiling on top, exactly as
asked ("at minimum: a per-device concurrency limit... a fabric-wide cap").

What a refused request returns
-------------------------------
Never a silent empty success. `network_tools.py`'s four real (non-`sender`)
call sites into `_netmiko_send_commands` -- the audit+metrics chokepoint this
module gates -- catch `AdmissionDenied` and turn it into an envelope with
`status=STATUS_ERROR` and a reason. That is deliberately the *existing*
status, not a new one: `checks.py` (frozen with respect to this change, owned
elsewhere) already treats `STATUS_ERROR` as "this intent yielded nothing
trustworthy" and reports the verdict as `unevaluated`, never `healthy` --
see `checks._uneval_reason`. Reusing it is what makes "a refused read is
unevaluated, never a healthy zero" true against code this change cannot
touch, rather than aspirational until that code is taught a new vocabulary
word. `result["admission"]` is additive on top -- present only on a refusal,
naming `scope` and `reason` -- for any caller (including this build's own
tests, and `nettools config show`-style tooling later) that wants to tell
"we chose not to try" apart from "the device actually failed" without
parsing prose out of `errors`.

Active-probe budgeting (B-408)
-------------------------------
`NETTOOLS_ALLOW_ACTIVE_PROBES` is already a binary gate (network_tools.py).
This module adds the rate on top: "a separate allowlist class with its own
budget... passive reads
unlimited; active probes rate-limited per device and per run." Unlike the
concurrency caps above, there is no failure threshold to discover here by
sending more ICMP at the lab -- the "right" probe rate is a traffic-generation
policy choice, not a measured device ceiling, so `NETTOOLS_MAX_ACTIVE_PROBES_
PER_DEVICE`/`_FABRIC` are conservative defaults, declared as such, not
measured boundaries the way the two caps above are.

Fail-open, not fail-closed, on infrastructure trouble
--------------------------------------------------------
If the lock directory cannot be created or written (bad permissions, a
read-only filesystem), every gate here admits anyway -- loudly, on stderr.
This mirrors `network_tools._audit_log`'s own precedent for `NETTOOLS_LOG`:
a broken *local* file is not the safety boundary (`platforms.APPROVED_
COMMANDS` is, and is entirely unaffected by this module's existence) and
must not silently turn into "nothing can ever be collected again". This is
resource protection, not authorization -- fail open with a loud complaint,
never fail closed and silent.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Iterator

from ._env import _float_env, _int_env

__all__ = [
    "AdmissionDenied",
    "AdmissionRefusal",
    "admit",
    "admit_event_run",
    "check_probe_budget",
    "finish_event_run",
]


# --------------------------------------------------------------------------- #
# Env resolution: `_float_env`/`_int_env` now come from `._env`, a leaf
# module with no imports of its own -- both this module and
# `network_tools.py` (which imports THIS module, to gate
# _netmiko_send_commands) can depend on it with no cycle. Before F2
# (release-1.0 cleanup) this module carried its own copy specifically to
# avoid that cycle; `settings.py`'s own module docstring named the resulting
# duplication ("near-duplicates in notifier.py/evidence_budget.py") as an
# accepted cost. F2 removed the cost without reintroducing the cycle.
# --------------------------------------------------------------------------- #


NETTOOLS_ADMISSION_DIR_ENV = "NETTOOLS_ADMISSION_DIR"
NETTOOLS_MAX_CONCURRENT_PER_DEVICE_ENV = "NETTOOLS_MAX_CONCURRENT_PER_DEVICE"
NETTOOLS_MAX_CONCURRENT_FABRIC_ENV = "NETTOOLS_MAX_CONCURRENT_FABRIC"
NETTOOLS_ADMISSION_WAIT_SECONDS_ENV = "NETTOOLS_ADMISSION_WAIT_SECONDS"
NETTOOLS_MAX_ACTIVE_PROBES_PER_DEVICE_ENV = "NETTOOLS_MAX_ACTIVE_PROBES_PER_DEVICE"
NETTOOLS_MAX_ACTIVE_PROBES_FABRIC_ENV = "NETTOOLS_MAX_ACTIVE_PROBES_FABRIC"
NETTOOLS_ACTIVE_PROBE_WINDOW_SECONDS_ENV = "NETTOOLS_ACTIVE_PROBE_WINDOW_SECONDS"
NETTOOLS_MAX_EVENT_RUNS_PER_DEVICE_ENV = "NETTOOLS_MAX_EVENT_RUNS_PER_DEVICE"
NETTOOLS_MAX_EVENT_RUNS_FABRIC_ENV = "NETTOOLS_MAX_EVENT_RUNS_FABRIC"
NETTOOLS_EVENT_RUN_WINDOW_SECONDS_ENV = "NETTOOLS_EVENT_RUN_WINDOW_SECONDS"
NETTOOLS_EVENT_IDEMPOTENCY_WINDOW_SECONDS_ENV = "NETTOOLS_EVENT_IDEMPOTENCY_WINDOW_SECONDS"

DEFAULT_ADMISSION_DIR = "admission"
# Measured 2026-08-19 against the live 9-device lab -- see the module
# docstring for the full numbers. Not round guesses.
DEFAULT_MAX_CONCURRENT_PER_DEVICE = 1
DEFAULT_MAX_CONCURRENT_FABRIC = 4
DEFAULT_ADMISSION_WAIT_SECONDS = 0.0
# Policy defaults, not measured -- see "Active-probe budgeting" above.
DEFAULT_MAX_ACTIVE_PROBES_PER_DEVICE = 3
DEFAULT_MAX_ACTIVE_PROBES_FABRIC = 10
DEFAULT_ACTIVE_PROBE_WINDOW_SECONDS = 60.0
# Event-woken model runs are more expensive than probes. These are stated
# defaults, held in cross-process files for the same reason probe budgets are.
DEFAULT_MAX_EVENT_RUNS_PER_DEVICE = 6
DEFAULT_MAX_EVENT_RUNS_FABRIC = 30
DEFAULT_EVENT_RUN_WINDOW_SECONDS = 3600.0
DEFAULT_EVENT_IDEMPOTENCY_WINDOW_SECONDS = 3600.0
DEFAULT_EVENT_LEASE_SECONDS = 300.0

_POLL_INTERVAL_SECONDS = 0.05
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def _sanitize(name: str) -> str:
    """A device name, made safe as a filename component.

    Every device name in this lab's own inventory is already plain
    alphanumeric (P1, PE1, RR1...), but a lock-file path built from an
    unsanitized name is exactly the kind of thing that becomes a path
    traversal surface the day it is not. Never trust a name as a path.
    """

    return _SAFE_NAME.sub("_", name) or "unknown"


@dataclass(frozen=True)
class AdmissionRefusal:
    """Why one admission request was refused. ``reason`` is always populated
    -- never a bare "refused" with no explanation, the same discipline
    ``checks.unevaluated`` already enforces for its own ``reason`` field."""

    #: "device" | "fabric" | "probe_device" | "probe_fabric"
    scope: str
    reason: str


class AdmissionDenied(Exception):
    """Raised by :func:`admit`/:func:`check_probe_budget` when a request
    cannot be granted right now. Every real (non-``sender``) call site in
    ``network_tools.py`` catches this at the one point it can occur and
    turns it into an envelope -- never lets it surface as a bare traceback,
    and never lets the resulting envelope look like a successful empty
    read. See ``network_tools._mark_admission_refused``.
    """

    def __init__(self, refusal: AdmissionRefusal) -> None:
        super().__init__(refusal.reason)
        self.refusal = refusal


# --------------------------------------------------------------------------- #
# The lock directory. Shared, cross-process, filesystem-based deliberately --
# see the module docstring's "one investigation is one separate OS process".
# --------------------------------------------------------------------------- #


def _admission_dir() -> Path | None:
    """The directory lock files live under, created on first use.

    Returns ``None`` -- never raises -- if it cannot be created or is not
    writable. See the module docstring's "Fail-open" section: every caller
    below treats ``None`` as "admit, but say so on stderr".
    """

    path = Path(os.getenv(NETTOOLS_ADMISSION_DIR_ENV) or DEFAULT_ADMISSION_DIR)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(
            f"WARNING: admission lock directory unusable, admitting unthrottled: "
            f"{path} ({exc})",
            file=sys.stderr,
        )
        return None
    return path


@contextlib.contextmanager
def _held_slot(paths: list[Path], *, wait_seconds: float) -> Iterator[bool]:
    """Try to hold exactly one of ``paths`` -- an flock counting semaphore's
    slots, one file per slot. Yields ``True`` (and holds it for the ``with``
    body's duration) if a slot was free, ``False`` if every slot stayed busy
    through ``wait_seconds``.

    ``flock`` is scoped to the *open file description*, not the process --
    a fresh ``open()`` per attempt (never a shared or duplicated fd) is what
    makes this correct both across threads within one process and across
    completely separate OS processes an orchestrator spawns. An interpreter
    crash mid-hold still releases the lock: the kernel drops it when the
    file descriptor closes, so there is no stale-PID bookkeeping to get
    wrong.
    """

    deadline = time.monotonic() + max(0.0, wait_seconds)
    handle: IO[bytes] | None = None
    try:
        while handle is None:
            for path in paths:
                try:
                    candidate = open(path, "a+b")
                except OSError:
                    continue
                try:
                    fcntl.flock(candidate.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    candidate.close()
                    continue
                handle = candidate
                break
            if handle is not None:
                break
            if time.monotonic() >= deadline:
                yield False
                return
            time.sleep(_POLL_INTERVAL_SECONDS)
        yield True
    finally:
        if handle is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


def fabric_concurrency_cap() -> int:
    """The configured fabric-wide concurrency cap.

    Exposed so a caller that fans out deliberately -- `iter_fabric`, and so
    `check_fabric`/`audit`/`analyze --fabric` above it -- can SHAPE its pool to
    this width instead of being refused for exceeding it. Refusing an
    independent process is right; refusing one process's intentional sweep
    returns `unevaluated` for most of the fabric and calls it an answer
    (OBS-460).

    Read through here rather than duplicated at the call site, so the shaper and
    the gate can never disagree about the limit.
    """

    return _int_env(NETTOOLS_MAX_CONCURRENT_FABRIC_ENV, DEFAULT_MAX_CONCURRENT_FABRIC)


def _device_slot_paths(device_name: str, admission_dir: Path, capacity: int) -> list[Path]:
    safe = _sanitize(device_name)
    return [admission_dir / f"device.{safe}.{i}.lock" for i in range(max(1, capacity))]


def _fabric_slot_paths(admission_dir: Path, capacity: int) -> list[Path]:
    return [admission_dir / f"fabric.{i}.lock" for i in range(max(1, capacity))]


@contextlib.contextmanager
def admit(device_name: str) -> Iterator[None]:
    """Admission gate for one live collection against ``device_name`` (B-444).

    Acquires, in order, (1) one of this device's own slots -- capacity
    ``NETTOOLS_MAX_CONCURRENT_PER_DEVICE`` (default 1) -- and (2) one
    fabric-wide slot -- capacity ``NETTOOLS_MAX_CONCURRENT_FABRIC`` (default
    4). Both defaults are measured, not guessed; see the module docstring.

    Raises :class:`AdmissionDenied` if either is unavailable within
    ``NETTOOLS_ADMISSION_WAIT_SECONDS`` (default 0 -- refuse immediately
    rather than queue, matching "a second concurrent read... buys nothing").
    A device-level refusal never touches the fabric-wide pool at all; a
    fabric-level refusal always releases the device slot it had already
    taken, so a refused request leaves no lock held.

    Wraps ``network_tools._netmiko_send_commands`` -- the one real transport
    entry point every check, template and evidence collection ultimately
    funnels through -- so this is the single chokepoint, not four separate
    gates that could individually drift.
    """

    admission_dir = _admission_dir()
    if admission_dir is None:
        yield  # fail open; _admission_dir() already warned on stderr.
        return

    wait_seconds = _float_env(
        NETTOOLS_ADMISSION_WAIT_SECONDS_ENV, DEFAULT_ADMISSION_WAIT_SECONDS
    )
    device_capacity = max(
        1,
        _int_env(NETTOOLS_MAX_CONCURRENT_PER_DEVICE_ENV, DEFAULT_MAX_CONCURRENT_PER_DEVICE),
    )
    fabric_capacity = max(
        1, _int_env(NETTOOLS_MAX_CONCURRENT_FABRIC_ENV, DEFAULT_MAX_CONCURRENT_FABRIC)
    )

    device_paths = _device_slot_paths(device_name, admission_dir, device_capacity)
    with _held_slot(device_paths, wait_seconds=wait_seconds) as got_device:
        if not got_device:
            raise AdmissionDenied(
                AdmissionRefusal(
                    scope="device",
                    reason=(
                        f"{device_capacity} concurrent collection(s) already in "
                        f"flight against {device_name} "
                        f"(NETTOOLS_MAX_CONCURRENT_PER_DEVICE={device_capacity}); "
                        "a second concurrent read of the same device competes "
                        "with the first for the session budget the device "
                        "itself enforces and buys nothing"
                    ),
                )
            )

        fabric_paths = _fabric_slot_paths(admission_dir, fabric_capacity)
        with _held_slot(fabric_paths, wait_seconds=wait_seconds) as got_fabric:
            if not got_fabric:
                raise AdmissionDenied(
                    AdmissionRefusal(
                        scope="fabric",
                        reason=(
                            f"{fabric_capacity} concurrent collections already "
                            "in flight fabric-wide "
                            f"(NETTOOLS_MAX_CONCURRENT_FABRIC={fabric_capacity})"
                        ),
                    )
                )
            yield


# --------------------------------------------------------------------------- #
# Active-probe budgeting (B-408): a file-backed sliding window, same
# cross-process reasoning as the concurrency gate above.
# --------------------------------------------------------------------------- #


def _check_and_record_window(
    path: Path, *, limit: int, window_seconds: float, now: float
) -> bool:
    """Atomically check-then-record one event against a file-backed sliding
    window keyed by ``path``. Returns ``True`` (and records ``now``) if under
    ``limit`` within the last ``window_seconds``; returns ``False`` (and
    records nothing) otherwise. One exclusive flock over the whole
    read-prune-write, so two concurrent callers can never both observe
    "under budget" for the one remaining slot.
    """

    handle = open(path, "a+b")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        raw = handle.read().decode("utf-8", errors="ignore")
        cutoff = now - window_seconds
        kept: list[float] = []
        for line in raw.splitlines():
            try:
                timestamp = float(line)
            except ValueError:
                continue
            if timestamp >= cutoff:
                kept.append(timestamp)
        if len(kept) >= limit:
            return False
        kept.append(now)
        handle.seek(0)
        handle.truncate()
        handle.write(("\n".join(repr(t) for t in kept) + "\n").encode("utf-8"))
        handle.flush()
        return True
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
    return None
def check_probe_budget(device_name: str) -> None:
    """Rate-gate one active probe (ping/traceroute) (B-408). Raises
    :class:`AdmissionDenied` when either the per-device or the fabric-wide
    probe budget is exhausted within the configured window; returns
    normally (recording this attempt against both windows) when admitted.

    Called from every active-probe call site in ``network_tools.py``,
    always *after* ``NETTOOLS_ALLOW_ACTIVE_PROBES``'s own on/off check and
    always *before* rendering or any device access -- matching
    ``run_template``'s existing "no device, no credentials, until every
    credential-free check has passed" ordering, so a probe over budget is
    refused with no connection ever attempted, same as an unapproved
    command.

    If the device-level check admits but the fabric-level check then
    refuses, the device-level window has already recorded the attempt (this
    function does not roll it back) -- a deliberate, documented, small
    imprecision: it can only make the *device* limit trigger a little early
    under simultaneous device+fabric contention, never late, so it never
    weakens the budget it approximates.
    """

    admission_dir = _admission_dir()
    if admission_dir is None:
        return  # fail open; already warned on stderr.

    window_seconds = _float_env(
        NETTOOLS_ACTIVE_PROBE_WINDOW_SECONDS_ENV, DEFAULT_ACTIVE_PROBE_WINDOW_SECONDS
    )
    device_limit = max(
        1,
        _int_env(
            NETTOOLS_MAX_ACTIVE_PROBES_PER_DEVICE_ENV, DEFAULT_MAX_ACTIVE_PROBES_PER_DEVICE
        ),
    )
    fabric_limit = max(
        1, _int_env(NETTOOLS_MAX_ACTIVE_PROBES_FABRIC_ENV, DEFAULT_MAX_ACTIVE_PROBES_FABRIC)
    )
    now = time.time()

    device_path = admission_dir / f"probes.{_sanitize(device_name)}.log"
    if not _check_and_record_window(
        device_path, limit=device_limit, window_seconds=window_seconds, now=now
    ):
        raise AdmissionDenied(
            AdmissionRefusal(
                scope="probe_device",
                reason=(
                    f"{device_limit} active probe(s) already sent to "
                    f"{device_name} in the last {window_seconds:g}s "
                    f"(NETTOOLS_MAX_ACTIVE_PROBES_PER_DEVICE={device_limit})"
                ),
            )
        )

    fabric_path = admission_dir / "probes.fabric.log"
    if not _check_and_record_window(
        fabric_path, limit=fabric_limit, window_seconds=window_seconds, now=now
    ):
        raise AdmissionDenied(
            AdmissionRefusal(
                scope="probe_fabric",
                reason=(
                    f"{fabric_limit} active probe(s) already sent fabric-wide "
                    f"in the last {window_seconds:g}s "
                    f"(NETTOOLS_MAX_ACTIVE_PROBES_FABRIC={fabric_limit})"
                ),
            )
        )


def check_event_run_budget(device_name: str) -> None:
    """Rate-gate one live event-woken model run (B-707 prerequisite).

    This is deliberately separate from collection admission: one accepted
    event may open several bounded tool calls and buy model tokens, so limiting
    only each individual SSH collection cannot prevent a flap from creating
    unbounded runs. The same cross-process sliding-window primitive prevents
    separate receiver processes from each believing they own the last slot.
    """

    admission_dir = _admission_dir()
    if admission_dir is None:
        return

    window_seconds = _float_env(
        NETTOOLS_EVENT_RUN_WINDOW_SECONDS_ENV, DEFAULT_EVENT_RUN_WINDOW_SECONDS
    )
    device_limit = max(
        1,
        _int_env(NETTOOLS_MAX_EVENT_RUNS_PER_DEVICE_ENV, DEFAULT_MAX_EVENT_RUNS_PER_DEVICE),
    )
    fabric_limit = max(
        1,
        _int_env(NETTOOLS_MAX_EVENT_RUNS_FABRIC_ENV, DEFAULT_MAX_EVENT_RUNS_FABRIC),
    )
    now = time.time()

    device_path = admission_dir / f"events.{_sanitize(device_name)}.log"
    if not _check_and_record_window(
        device_path, limit=device_limit, window_seconds=window_seconds, now=now
    ):
        raise AdmissionDenied(
            AdmissionRefusal(
                scope="event_device",
                reason=(
                    f"{device_limit} event run(s) already started for {device_name} "
                    f"in the last {window_seconds:g}s "
                    f"(NETTOOLS_MAX_EVENT_RUNS_PER_DEVICE={device_limit})"
                ),
            )
        )

    fabric_path = admission_dir / "events.fabric.log"
    if not _check_and_record_window(
        fabric_path, limit=fabric_limit, window_seconds=window_seconds, now=now
    ):
        raise AdmissionDenied(
            AdmissionRefusal(
                scope="event_fabric",
                reason=(
                    f"{fabric_limit} event run(s) already started fabric-wide "
                    f"in the last {window_seconds:g}s "
                    f"(NETTOOLS_MAX_EVENT_RUNS_FABRIC={fabric_limit})"
                ),
            )
        )


def claim_event_id(event_id: str) -> None:
    """Claim an event identity once per window, or raise on a replay.

    Event retries and collector replays may reach separate receiver processes.
    A process-local set would therefore provide no idempotency guarantee; this
    uses the same flock-protected admission directory as the rate budgets.
    """

    admission_dir = _admission_dir()
    if admission_dir is None:
        return
    window_seconds = _float_env(
        NETTOOLS_EVENT_IDEMPOTENCY_WINDOW_SECONDS_ENV, DEFAULT_EVENT_IDEMPOTENCY_WINDOW_SECONDS
    )
    path = admission_dir / "events.idempotency.log"
    now = time.time()
    handle = open(path, "a+b")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        cutoff = now - window_seconds
        kept: list[tuple[float, str]] = []
        duplicate = False
        for line in handle.read().decode("utf-8", errors="ignore").splitlines():
            try:
                timestamp_text, prior_id = line.split(" ", 1)
                timestamp = float(timestamp_text)
            except ValueError:
                continue
            if timestamp >= cutoff:
                kept.append((timestamp, prior_id))
                duplicate = duplicate or prior_id == event_id
        if duplicate:
            raise AdmissionDenied(
                AdmissionRefusal(
                    scope="event_idempotency",
                    reason=f"event_id {event_id} was already claimed in the last {window_seconds:g}s",
                )
            )
        kept.append((now, event_id))
        handle.seek(0)
        handle.truncate()
        handle.write("".join(f"{timestamp!r} {prior_id}\n" for timestamp, prior_id in kept).encode("utf-8"))
        handle.flush()
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _event_state_path(admission_dir: Path) -> Path:
    return admission_dir / "events.state.json"


def _load_event_state(handle: IO[bytes]) -> dict[str, object]:
    handle.seek(0)
    try:
        state = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    events = state.get("events")
    starts = state.get("starts")
    if not isinstance(events, dict):
        state["events"] = {}
    if not isinstance(starts, list):
        state["starts"] = []
    return state


def _write_event_state(handle: IO[bytes], state: dict[str, object]) -> None:
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps(state, sort_keys=True).encode("utf-8"))
    handle.flush()
    os.fsync(handle.fileno())


def admit_event_run(event_id: str, device_name: str) -> None:
    """Atomically admit one event run across identity and both run budgets.

    A refusal changes no counters. Accepted work has a bounded lease; a
    terminal retryable failure releases its identity for a later attempt,
    while a completed event remains deduplicated for the configured window.
    """

    admission_dir = _admission_dir()
    if admission_dir is None:
        return
    window_seconds = _float_env(
        NETTOOLS_EVENT_RUN_WINDOW_SECONDS_ENV, DEFAULT_EVENT_RUN_WINDOW_SECONDS
    )
    idempotency_window = _float_env(
        NETTOOLS_EVENT_IDEMPOTENCY_WINDOW_SECONDS_ENV,
        DEFAULT_EVENT_IDEMPOTENCY_WINDOW_SECONDS,
    )
    device_limit = max(
        1, _int_env(NETTOOLS_MAX_EVENT_RUNS_PER_DEVICE_ENV, DEFAULT_MAX_EVENT_RUNS_PER_DEVICE)
    )
    fabric_limit = max(
        1, _int_env(NETTOOLS_MAX_EVENT_RUNS_FABRIC_ENV, DEFAULT_MAX_EVENT_RUNS_FABRIC)
    )
    now = time.time()
    lease_seconds = min(DEFAULT_EVENT_LEASE_SECONDS, max(1.0, idempotency_window))
    path = _event_state_path(admission_dir)
    handle = open(path, "a+b")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        state = _load_event_state(handle)
        events = state["events"]
        starts = state["starts"]
        assert isinstance(events, dict) and isinstance(starts, list)
        cutoff = now - window_seconds
        idempotency_cutoff = now - idempotency_window
        retained_starts = [
            entry for entry in starts
            if isinstance(entry, dict) and isinstance(entry.get("timestamp"), (int, float))
            and entry["timestamp"] >= cutoff
        ]
        retained_events: dict[str, object] = {}
        for prior_id, entry in events.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("updated"), (int, float)):
                continue
            if entry["updated"] >= idempotency_cutoff:
                retained_events[str(prior_id)] = entry
        prior = retained_events.get(event_id)
        if isinstance(prior, dict):
            status = prior.get("status")
            active = status in {"accepted", "running"} and (
                now - float(prior["updated"]) < lease_seconds
            )
            completed = status in {"completed", "terminal_failed"}
            if active or completed:
                raise AdmissionDenied(
                    AdmissionRefusal(
                        scope="event_idempotency",
                        reason=(
                            f"event_id {event_id} is {status} within the "
                            f"{idempotency_window:g}s idempotency window"
                        ),
                    )
                )
        device_starts = sum(
            1 for entry in retained_starts if entry.get("device") == device_name
        )
        if device_starts >= device_limit:
            raise AdmissionDenied(
                AdmissionRefusal(
                    scope="event_device",
                    reason=(
                        f"{device_limit} event run(s) already started for {device_name} "
                        f"in the last {window_seconds:g}s "
                        f"(NETTOOLS_MAX_EVENT_RUNS_PER_DEVICE={device_limit})"
                    ),
                )
            )
        if len(retained_starts) >= fabric_limit:
            raise AdmissionDenied(
                AdmissionRefusal(
                    scope="event_fabric",
                    reason=(
                        f"{fabric_limit} event run(s) already started fabric-wide "
                        f"in the last {window_seconds:g}s "
                        f"(NETTOOLS_MAX_EVENT_RUNS_FABRIC={fabric_limit})"
                    ),
                )
            )
        retained_starts.append({"timestamp": now, "device": device_name, "event_id": event_id})
        retained_events[event_id] = {"device": device_name, "status": "accepted", "updated": now}
        state["starts"] = retained_starts
        state["events"] = retained_events
        _write_event_state(handle, state)
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def finish_event_run(event_id: str, *, completed: bool) -> None:
    """Record a terminal outcome for an admitted event without raising."""

    admission_dir = _admission_dir()
    if admission_dir is None:
        return
    handle = open(_event_state_path(admission_dir), "a+b")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        state = _load_event_state(handle)
        events = state["events"]
        assert isinstance(events, dict)
        entry = events.get(event_id)
        if isinstance(entry, dict):
            entry["status"] = "completed" if completed else "retryable_failed"
            entry["updated"] = time.time()
            _write_event_state(handle, state)
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
