"""The diagnosis accuracy ledger (B-485): an append-only record of what this
tool diagnosed, and -- separately, later, by a human -- whether it was right.

Why this exists
-----------------
This project's whole trust story so far rests on a small number of blind
trials, each reported by hand in a document (`docs/build/MVP0-REVIEW.md` and
friends). That is honest, but it does not accumulate: a tool running in
production for months should be building its *own* record of when its
diagnoses were confirmed correct and when they were not, instead of resting
forever on trials someone wrote up once. This module is that record. It does
not replace the blind trials -- it is what the tool has instead of trials
once nobody is running them anymore.

The one rule that makes this worth building at all
-----------------------------------------------------
**The tool must never mark its own homework.** `record_diagnosis` is called
automatically (once wiring lands -- see the module's own limits below) and
writes what the descent found: device, subject, flow, finding, whether
`InvestigationResult.trustworthy` was true. It never writes a verdict.
`record_verdict` is a *separate* call, made only by a human, naming who said
so and when, and its `outcome` has no default -- there is no code path that
can leave a diagnosis marked `confirmed_correct` without a person having
typed that in. Until a human calls `record_verdict`, `diagnoses()` reports
`outcome="unknown"` -- not absent, not blank, a real value that shows up in
every count `summary()` produces. An `unknown` that quietly counted as a pass
would make this ledger worse than the trials it is meant to replace, because
a green ledger would then mean nothing looked at it rather than everything
having been checked.

Append-only, the same way `FINDINGS.md` is
-----------------------------------------------
A diagnosis entry is never edited after it is written. A verdict is never
edited either -- a human who wants to correct an earlier verdict calls
`record_verdict` again with the same `diagnosis_id`; `diagnoses()` resolves
the *latest* verdict per id (append order is chronological, one writer, see
`DiagnosisLedger._append`), but every verdict ever recorded, including
superseded ones, stays in `entries()`. That is `FINDINGS.md`'s own rule --
"never edit or delete an earlier entry; if it turns out to be wrong, write a
new one that corrects it" -- applied to data instead of prose.

Storage: metrics.py's idiom, evidence_store.py's honesty about failure
---------------------------------------------------------------------------
The persistence shape is `metrics.py`'s: an optional on-disk path, read
lazily once per process on first use (never at import time, for the same
`.env`-ordering reason `metrics.py` gives), in-memory state always
authoritative for the running process. It departs from `metrics.py` in the
file *format*: metrics rewrites one small, fixed-shape JSON document on every
mutation, which is right for a handful of counters and wrong for a log that
only grows -- rewriting the whole thing on every diagnosis would make the
corruption window scale with the ledger's own size. A ledger is instead
written the way `network_tools._audit_log` already writes `NETTOOLS_LOG`:
one JSON object per line, opened in append mode, so a crash can only ever
maim the newest line and never touches an earlier one. Reading a possibly-
maimed file back reuses `evidence_store.py`'s already-established shape --
skip the bad line, warn on stderr, keep the rest (`_read_snapshot_json`) --
rather than a fourth invention: this is the same "file backend, corrupt
entries are skipped and warned about, never fatal" idiom used twice already,
applied to a JSON Lines instead of a JSON-per-file layout.

Where this deliberately parts ways with `metrics.py`: a lost metric is
genuinely fine to lose silently (see that module's own docstring). A lost
diagnosis record is not -- "degrade safely" here means the write failure
must be visible to whatever called `record_diagnosis`, not swallowed. That
is why `record_diagnosis`/`record_verdict` return a result object carrying
`persisted` and `warning` instead of `None`, unlike `metrics._persist`'s bare
`except OSError: pass`. A ledger that fails to persist and says nothing about
it is exactly the "green flag over a degraded read" shape this project has
hit three times before (`PROCESS.md` SS0.12) -- pointed at a logging path
instead of a check this time, but the same failure.

No new environment variable
-------------------------------
This ledger has no dedicated env var and no default on-disk path. `settings.py`
is owned by another track this session, so nothing here reads or declares one
-- `DiagnosisLedger(path=...)` (or the module-level functions' `ledger=`
override) is how a caller opts a ledger into persistence, exactly the same
shape `metrics.MetricsCollector(path=...)` and `evidence_store.get_store
(base_dir=...)` already use. Without an explicit path, every ledger here
(including the module-level `default_ledger`) is in-memory only: real within
one process, gone the moment it exits. That is a real limit for a tool where
`nettools <command>` is a fresh process every time (`metrics.py`'s own
docstring) -- this ships fully correct and fully tested, but does not yet
accumulate anything across separate `nettools investigate` invocations. See
the module docstring's closing note on what wiring closes that gap, and the
build report for the exact env var this needs once `settings.py` is free.

What "source" is for, and why it has no default
----------------------------------------------------
`record_diagnosis` requires `source` ("live" or "fixture", or any other
non-empty label a caller invents) with **no default value**. The lab's own
`--from-fixtures` replay is deterministic, canned evidence, not a diagnosis
about a real device at the moment it ran -- if fixture-replay runs and live
runs land in the ledger indistinguishably, the accuracy signal this module
exists to build is quietly diluted by every demo and every test fixture
replay ever run, and nothing about the summary would show it happening. That
is `PROCESS.md` SS0.13's "identity" face -- a corpus shows width only in the
dimensions where it varies -- so `source` is required rather than defaulted:
a default of `"live"` would be silently *wrong* for the common case
(`--from-fixtures` is the no-lab, no-credentials path most runs of this
tool will actually take), and a default of `"fixture"` would undercount the
real signal. Forcing the caller to say which, every time, is cheaper than
either guess and is the whole reason this field exists.

What this module does not do
---------------------------------
It does not call itself. `investigate()` (`investigation.py`) and `cli.py`
are owned by other tracks this session, so nothing here is wired into the
live `nettools investigate` path yet -- see the report accompanying this
change for the exact call this needs at each site, offered as a diff sketch
rather than applied. It does not replay or re-verify a diagnosis; it only
records what was claimed and, later, what a human said about it. And it does
not infer a verdict from anything the tool itself produced -- `trustworthy`
describes the descent's own confidence, not the ledger's opinion of whether
the descent was right, and the two are recorded as separate fields on
purpose so nobody can later confuse "the tool thought this was solid" with
"a human confirmed this was correct".

``run_id`` (B-486's ledger-integration gap, closed on the write side here)
-----------------------------------------------------------------------------
`record_diagnosis` accepts an optional `run_id` -- the identifier
`ticket.open_ticket`/`session_memory.record_turn` already mint and pass
around for the *same* investigation (`ticket.Ticket.run_id`), not a second id
this module invents. It is deliberately **not** required and has no
relationship to this ledger's own `id` field: `id` is this ledger entry's own
identity (what `record_verdict` targets); `run_id` is the identity of the
*investigation run* that produced the diagnosis, which is what a consumer
needs to walk back to the concrete ticket/evidence bundle -- two different
questions, two different fields, on purpose. A caller that omits it (every
call site before wave 2 wires `cli.py`'s ticket handle through) gets
`run_id: None` on the written record -- present, not absent, the same
"unevaluated, not silently ok" discipline this whole codebase already
applies elsewhere. An empty or whitespace-only string is normalised to
`None` rather than stored verbatim, for the same reason: a missing run id
must never be indistinguishable from an empty-string one by whichever reader
checks for its presence later (`incident_correlation.py`'s own consumer of
this field treats `None` and only `None` as "unrecorded"). Old rows written
before this change simply have no `run_id` key at all; `dict.get("run_id")`
already returns `None` for those with no reader change required -- this
ledger is append-only JSONL (see "Storage" above), so there is no migration
and never will be one.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: A human verdict on one diagnosis. `UNKNOWN` is both the resting state
#: before any verdict exists *and* a value a human may set explicitly
#: (looked, could not tell) -- two different facts that `diagnoses()`
#: reports the same way today. See the module docstring's opening section
#: for why an `unknown` must never be read as a pass.
CONFIRMED_CORRECT = "confirmed_correct"
INCORRECT = "incorrect"
UNKNOWN = "unknown"
OUTCOMES: tuple[str, ...] = (CONFIRMED_CORRECT, INCORRECT, UNKNOWN)

#: Not an enum -- `source` accepts any non-empty label so a future caller
#: (a second replay mode, a demo harness) is not blocked on this module. These
#: are just the two the lab actually has today.
SOURCE_LIVE = "live"
SOURCE_FIXTURE = "fixture"

_DIAGNOSIS = "diagnosis"
_VERDICT = "verdict"


def _timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _require_nonempty_str(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")
    return value


@dataclass(frozen=True)
class LedgerWriteResult:
    """What happened when a diagnosis was recorded.

    ``persisted`` is false both for "no path configured" (in-memory-only
    mode, not a fault) and for "a path is configured and the write failed" --
    ``warning`` is what tells those two apart, and is the field a caller
    folds into its own output so a diagnosis that could not be logged still
    says so, rather than the failure being swallowed the way `metrics.py`'s
    counters intentionally are (see the module docstring).
    """

    id: str
    recorded_at: str
    persisted: bool
    warning: str | None = None


@dataclass(frozen=True)
class VerdictWriteResult:
    """What happened when a verdict was recorded.

    ``diagnosis_found`` is best-effort, not a guard: it reflects only what
    this process has loaded from ``path`` so far, so ``False`` here can mean
    either a real typo in ``diagnosis_id`` or a diagnosis this reader simply
    has not seen yet. It is reported, not enforced -- refusing to record a
    correction because the local view is incomplete would be worse than
    recording it and letting a reader of the ledger notice the mismatch.
    """

    id: str
    recorded_at: str
    persisted: bool
    warning: str | None = None
    diagnosis_found: bool = False


class DiagnosisLedger:
    """One append-only ledger. See the module docstring for the full design.

    Thread-safe (a lock guards every mutation, matching `metrics.MetricsCollector`
    -- cheap, and this project already runs a thread pool over `check_fabric`).
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self._path = Path(path) if path else None
        self._lock = threading.Lock()
        self._entries: list[dict[str, Any]] = []
        self._loaded = False

    # -- loading -------------------------------------------------------

    def _load_once(self) -> None:
        if self._loaded:
            return
        self._loaded = True  # Only ever attempted once per process (metrics.py's rule).
        if self._path is None or not self._path.is_file():
            return
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            print(
                f"WARNING: could not read diagnosis ledger {self._path}: {exc}",
                file=sys.stderr,
            )
            return
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError as exc:
                # Same "skip the corrupt one, keep the rest, warn on stderr"
                # shape as evidence_store._read_snapshot_json -- a maimed
                # trailing line (the one a crash mid-append could produce)
                # must not erase every entry that came before it.
                print(
                    f"WARNING: corrupt ledger line, skipping: {self._path}:{line_no} ({exc})",
                    file=sys.stderr,
                )
                continue
            if isinstance(record, dict):
                self._entries.append(record)

    # -- writing ---------------------------------------------------------

    def _append(self, record: dict[str, Any]) -> tuple[bool, str | None]:
        """Best-effort append to disk. Never raises -- see the module docstring.

        Returns ``(persisted, warning)``. ``persisted`` is false, with no
        warning, when no path is configured at all -- that is a mode, not a
        failure.
        """

        if self._path is None:
            return False, None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return True, None
        except (OSError, TypeError, ValueError) as exc:
            # OSError: permission, full disk, a directory in the way.
            # TypeError/ValueError: a caller passed something json.dumps
            # cannot encode (e.g. a non-serialisable object in `cause`).
            # Either way the diagnosis itself already happened and is
            # already in self._entries -- bookkeeping failing must not
            # un-happen it.
            warning = f"diagnosis ledger write failed ({self._path}): {exc}"
            print(f"WARNING: {warning}", file=sys.stderr)
            return False, warning

    def record_diagnosis(
        self,
        *,
        device: str,
        subject: str,
        flow: str,
        finding: str,
        trustworthy: bool,
        source: str,
        cause: dict[str, Any] | None = None,
        reason: str | None = None,
        report_status: str | None = None,
        correlation_status: str | None = None,
        run_id: str | None = None,
    ) -> LedgerWriteResult:
        """Append one diagnosis. Called by the tool -- never carries a verdict.

        ``trustworthy`` describes the descent's own confidence
        (`InvestigationResult.trustworthy`); it is not, and must never become,
        a stand-in for a human's verdict -- that is the entire point of this
        module (see "The one rule that makes this worth building at all"
        above). ``source`` has no default; see the module docstring.

        ``run_id`` is optional and unrelated to this entry's own ``id`` -- see
        the module docstring's ``run_id`` section. A blank or whitespace-only
        string is normalised to ``None``, the same "absent, never an empty
        stand-in" rule ``NETTOOLS_ACTOR``'s own fallback chain already
        follows elsewhere in this codebase.
        """

        device = _require_nonempty_str("device", device)
        subject = _require_nonempty_str("subject", subject)
        flow = _require_nonempty_str("flow", flow)
        finding = _require_nonempty_str("finding", finding)
        source = _require_nonempty_str("source", source)
        if not isinstance(trustworthy, bool):
            raise ValueError(f"trustworthy must be a bool, got {trustworthy!r}")
        run_id = run_id.strip() if isinstance(run_id, str) and run_id.strip() else None

        record: dict[str, Any] = {
            "kind": _DIAGNOSIS,
            "id": _new_id(),
            "recorded_at": _timestamp_now(),
            "device": device,
            "subject": subject,
            "flow": flow,
            "finding": finding,
            "trustworthy": trustworthy,
            "source": source,
            "cause": cause,
            "reason": reason,
            "report_status": report_status,
            "correlation_status": correlation_status,
            "run_id": run_id,
        }
        with self._lock:
            self._load_once()
            self._entries.append(record)
            persisted, warning = self._append(record)
        return LedgerWriteResult(
            id=record["id"], recorded_at=record["recorded_at"],
            persisted=persisted, warning=warning,
        )

    def record_verdict(
        self,
        diagnosis_id: str,
        outcome: str,
        *,
        by: str,
        note: str | None = None,
    ) -> VerdictWriteResult:
        """Append one human verdict on a previously recorded diagnosis.

        The only place in this module a caller supplies ``confirmed_correct``
        or ``incorrect`` -- ``record_diagnosis`` cannot produce either.
        ``outcome`` and ``by`` are validated and raise on a bad value rather
        than being silently ignored (`metrics.record_verdict`'s "unknown
        severities are ignored" is right for high-volume telemetry and wrong
        here: a verdict is a deliberate, one-off human action, and a silently
        dropped one is exactly the invisible-non-result this module exists to
        prevent -- it should fail loudly at the point someone can still fix
        the call, not be discovered missing months later).
        """

        diagnosis_id = _require_nonempty_str("diagnosis_id", diagnosis_id)
        if outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {OUTCOMES}, got {outcome!r}")
        by = _require_nonempty_str(
            "by", by
        )  # The tool must never mark its own homework -- there is no default identity here.

        record: dict[str, Any] = {
            "kind": _VERDICT,
            "id": _new_id(),
            "recorded_at": _timestamp_now(),
            "diagnosis_id": diagnosis_id,
            "outcome": outcome,
            "by": by,
            "note": note,
        }
        with self._lock:
            self._load_once()
            diagnosis_found = any(
                e.get("kind") == _DIAGNOSIS and e.get("id") == diagnosis_id
                for e in self._entries
            )
            self._entries.append(record)
            persisted, warning = self._append(record)
        return VerdictWriteResult(
            id=record["id"], recorded_at=record["recorded_at"],
            persisted=persisted, warning=warning, diagnosis_found=diagnosis_found,
        )

    # -- reading back ------------------------------------------------------

    def entries(self) -> tuple[dict[str, Any], ...]:
        """Every raw entry -- diagnoses and verdicts, in append order. Never mutated."""

        with self._lock:
            self._load_once()
            return tuple(dict(entry) for entry in self._entries)

    def diagnoses(self) -> tuple[dict[str, Any], ...]:
        """Every diagnosis, each carrying its *resolved* current outcome.

        The latest verdict for a diagnosis id wins -- append order is
        chronological (one writer, JSONL), so "latest" is simply the last
        matching verdict entry. A diagnosis with no verdict yet resolves to
        ``outcome="unknown"``, ``outcome_by=None``, ``outcome_at=None`` --
        present, not omitted, which is what makes it visible in `summary()`.
        """

        with self._lock:
            self._load_once()
            entries = list(self._entries)

        order: list[str] = []
        by_id: dict[str, dict[str, Any]] = {}
        for entry in entries:
            entry_id = entry.get("id")
            if entry.get("kind") == _DIAGNOSIS and isinstance(entry_id, str):
                by_id[entry_id] = dict(entry)
                order.append(entry_id)

        latest_verdict: dict[str, dict[str, Any]] = {}
        verdict_counts: dict[str, int] = {}
        for entry in entries:
            if entry.get("kind") != _VERDICT:
                continue
            diagnosis_id = entry.get("diagnosis_id")
            if not isinstance(diagnosis_id, str):
                continue
            verdict_counts[diagnosis_id] = verdict_counts.get(diagnosis_id, 0) + 1
            latest_verdict[diagnosis_id] = entry  # Last write in append order wins.

        resolved: list[dict[str, Any]] = []
        for diagnosis_id in order:
            record = by_id[diagnosis_id]
            verdict = latest_verdict.get(diagnosis_id)
            record["outcome"] = verdict["outcome"] if verdict else UNKNOWN
            record["outcome_by"] = verdict["by"] if verdict else None
            record["outcome_at"] = verdict["recorded_at"] if verdict else None
            record["outcome_note"] = verdict.get("note") if verdict else None
            record["verdict_count"] = verdict_counts.get(diagnosis_id, 0)
            resolved.append(record)
        return tuple(resolved)

    def summary(self) -> dict[str, Any]:
        """Counts by outcome, source and trustworthiness. `unknown` always present.

        ``vacuous`` names the empty case explicitly (mirrors
        `grounding.GroundingResult.vacuous`) -- a fresh ledger's
        ``by_outcome`` is ``{confirmed_correct: 0, incorrect: 0, unknown: 0}``,
        which reads exactly like "a spotless record" unless something says
        plainly that zero diagnoses were ever recorded. That is
        `PROCESS.md` SS0.12/SS0.13's "absence read as health" shape, pointed
        at this module's own output.
        """

        diagnoses = self.diagnoses()
        by_outcome = dict.fromkeys(OUTCOMES, 0)
        by_source: dict[str, dict[str, Any]] = {}
        by_trustworthy: dict[str, dict[str, Any]] = {
            "true": dict.fromkeys(OUTCOMES, 0),
            "false": dict.fromkeys(OUTCOMES, 0),
        }

        for diagnosis in diagnoses:
            outcome = diagnosis.get("outcome", UNKNOWN)
            if outcome not in by_outcome:
                outcome = UNKNOWN  # Defensive only -- diagnoses() never emits anything else.
            by_outcome[outcome] += 1

            source = diagnosis.get("source") or "unknown"
            bucket = by_source.setdefault(
                source, {"total": 0, "by_outcome": dict.fromkeys(OUTCOMES, 0)}
            )
            bucket["total"] += 1
            bucket["by_outcome"][outcome] += 1

            trust_key = "true" if diagnosis.get("trustworthy") else "false"
            by_trustworthy[trust_key][outcome] += 1

        return {
            "total_diagnoses": len(diagnoses),
            "vacuous": len(diagnoses) == 0,
            "by_outcome": by_outcome,
            "by_source": by_source,
            "by_trustworthy": by_trustworthy,
        }

    def render_summary_text(self) -> str:
        """One human-readable block. `unknown` is never omitted, even at zero."""

        summary = self.summary()
        if summary["vacuous"]:
            return "diagnosis ledger: 0 diagnoses recorded (vacuous -- nothing to report on)"

        by_outcome = summary["by_outcome"]
        lines = [
            f"diagnosis ledger: {summary['total_diagnoses']} diagnoses -- "
            f"confirmed_correct={by_outcome[CONFIRMED_CORRECT]} "
            f"incorrect={by_outcome[INCORRECT]} "
            f"unknown={by_outcome[UNKNOWN]}",
        ]
        for source in sorted(summary["by_source"]):
            bucket = summary["by_source"][source]
            counts = bucket["by_outcome"]
            lines.append(
                f"  source={source}: total={bucket['total']} "
                f"confirmed_correct={counts[CONFIRMED_CORRECT]} "
                f"incorrect={counts[INCORRECT]} unknown={counts[UNKNOWN]}"
            )
        return "\n".join(lines)

    def reset(self) -> None:
        """Clear all state, in-memory and (if configured) on disk. Test use only.

        Truncating an append-only file looks like a contradiction with the
        "never edit an entry" rule; it is not one -- this is the same
        test-isolation carve-out `metrics.MetricsCollector.reset` already
        documents, never called from a production code path.
        """

        with self._lock:
            self._entries = []
            self._loaded = True
            if self._path is not None:
                try:
                    self._path.unlink(missing_ok=True)
                except OSError:
                    pass


# The process-wide default, exactly `metrics.py`'s `default_collector` shape:
# constructing it does no I/O, so a module-level instance at import time is
# safe. `path=None` means in-memory-only -- see the module docstring's "No
# new environment variable" section for why there is no env-driven default
# here the way `metrics.default_collector` has one.
default_ledger = DiagnosisLedger()


def record_diagnosis(
    *,
    device: str,
    subject: str,
    flow: str,
    finding: str,
    trustworthy: bool,
    source: str,
    cause: dict[str, Any] | None = None,
    reason: str | None = None,
    report_status: str | None = None,
    correlation_status: str | None = None,
    run_id: str | None = None,
    ledger: DiagnosisLedger | None = None,
) -> LedgerWriteResult:
    return (ledger or default_ledger).record_diagnosis(
        device=device, subject=subject, flow=flow, finding=finding,
        trustworthy=trustworthy, source=source, cause=cause, reason=reason,
        report_status=report_status, correlation_status=correlation_status,
        run_id=run_id,
    )


def record_verdict(
    diagnosis_id: str,
    outcome: str,
    *,
    by: str,
    note: str | None = None,
    ledger: DiagnosisLedger | None = None,
) -> VerdictWriteResult:
    return (ledger or default_ledger).record_verdict(diagnosis_id, outcome, by=by, note=note)


def entries(*, ledger: DiagnosisLedger | None = None) -> tuple[dict[str, Any], ...]:
    return (ledger or default_ledger).entries()


def diagnoses(*, ledger: DiagnosisLedger | None = None) -> tuple[dict[str, Any], ...]:
    return (ledger or default_ledger).diagnoses()


def summary(*, ledger: DiagnosisLedger | None = None) -> dict[str, Any]:
    return (ledger or default_ledger).summary()


def render_summary_text(*, ledger: DiagnosisLedger | None = None) -> str:
    return (ledger or default_ledger).render_summary_text()


def reset(*, ledger: DiagnosisLedger | None = None) -> None:
    """Reset the process-wide default ledger (or an explicit one). Test use only."""

    (ledger or default_ledger).reset()
