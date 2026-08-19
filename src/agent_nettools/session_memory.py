"""B-407 -- session memory: a pointer from one interactive sitting to its most
recent turn, so a second `nettools` invocation in the same session can
resolve "what were we just talking about" without the human restating it.

Why this exists, and why it is smaller than the name "memory" suggests
------------------------------------------------------------------------
This build already has four durable records of what happened: `evidence_store`
(device snapshots over time, queryable, prunable), `ledger` (what was
diagnosed and, later, a human's verdict -- deliberately no `outcome` the tool
can supply itself), `ticket` (one flight recorder per run: question, tool
timeline, model exchange, answer), and `FINDINGS.md`/`BACKLOG.md` (the
project's own record of itself). D14 asks a fifth question none of them
answer: *"is this new, or part of a pattern?"* -- and its own text is explicit
that the general answer to that question, a schema'd, code-derived event
store keyed by object, is **deferred to Stage 2**, on purpose, because at
Stage 1 a human is present and knows whether something is new; memory earns
its place only once the agent is woken by an event with nobody there to ask.
`docs/design/evidence-reduction.md` SS10 repeats the same gate from a second
direction ("historical baselines and rarity ... **Build it once, at Stage 2,
as B-203/B-204 -- not twice**"), and `BACKLOG.md` still carries B-203/B-204/
B-205 as `DEFERRED until Stage 2` as of this change. B-201 (event-driven
intake) and B-207/B-208 (the Stage 2 transition gate) are *also* still
`DEFERRED` -- so the condition that was supposed to pull memory forward has
not arrived, and this module does not build it. See "What this module
deliberately does not build" below for the full reasoning, including why it
does not reuse the literal filename `evidence_store.py` a prior instruction
named -- that name already belongs to one of the four records above, and
reusing it would either overwrite a durable record this change was told not
to duplicate, or silently fork it in two.

What actually earns its place at Stage 1: B-407, session memory, multi-turn
------------------------------------------------------------------------------
`BACKLOG.md` carries B-407 as `OPEN` (not `DEFERRED`), sized `S`, with its own
one-line brief: *"Only useful at Stage 1 where a human is present. Genuinely
small."* That is a different claim from D14's general memory, and it survives
the same test the general claim did not: **what does turn 2 need from turn 1
that re-reading `evidence_store`/`ticket` would not already supply?**

Re-reading evidence never tells a caller *which* evidence to re-read. A human
who just asked "why is the BGP session on PE2 down" and now asks "and what
about the interface under it" is relying on "it" resolving to PE2 and the
session's own dependency chain -- and no amount of re-reading `evidence_store`
(keyed by device, not by "what we were just discussing") or a ticket file
(one per run, with no index of "which one was the most recent") answers
*which* device, subject, flow, or ticket a bare "it" refers to. That is the
one concrete gap: not the content (the ticket already has the finding, the
tool timeline, the model exchange -- duplicating any of that here would be
exactly the disagreement risk this change was warned against), but the
*pointer* to the content. This module is that pointer, nothing else.

Three consequences fall out of "it is a pointer, not a record":

1. **One row per session, overwritten, not appended.** `ledger`/`ticket`/
   `FINDINGS.md` are append-only because they are a record of what happened
   and every entry has permanent evidentiary value. A session's "current
   turn" does not -- it is scratch state, closer to a shell's `$OLDPWD` than
   to a log line, and keeping only the latest is what keeps this module from
   becoming a second, competing history of turns (`ticket.py`'s own `run_id`
   field is already the join key for that, if a caller chooses to thread it
   through; this module does not attempt to replace it).
2. **No finding text, ever.** `record_turn` accepts `device`, `subject`,
   `flow`, `run_id` and `ticket_path` -- identity and a pointer, never
   `finding`, `cause`, or anything a human would read as "the answer".
   Turn 2 that wants the answer follows `ticket_path` and reads the ticket,
   which stays the single place that content can disagree with itself.
3. **Every field is a plain copy of an already-typed value the caller had in
   hand.** Exactly `subject`/`device`/`flow`/`run_id`, the same names
   `ticket.TicketRecorder.open()` already receives, copied by the caller into
   this module's `record_turn` alongside (or immediately after) opening the
   ticket. No inference, no judgement, nothing derived by computation --
   which is what makes "derived, never authored" (D14's own rule) close to
   automatic here: there is no step in this module capable of getting an
   answer wrong, only of forgetting to be called. Compare `ledger.py`'s
   `record_diagnosis`, which *does* compute something (`trustworthy` reflects
   the descent's own confidence) -- this module computes nothing at all.

What this module deliberately does not build
-------------------------------------------------
**Not B-203/B-204/B-205 (the general operational-memory event store).**
Three independent reasons converge, not one:

- D14's own text gates it to Stage 2 and states the revisit condition
  explicitly: *"Stage 1 investigations repeatedly stall on questions only
  history can answer."* No such stall is recorded in `FINDINGS.md` as of this
  change. On the contrary, D14's own motivating example -- *"this interface
  has flapped fourteen times this month"* -- is **already answered** today by
  `network_tools.detect_flaps`, which reads a device's full snapshot history
  from `evidence_store` and reports every field whose transition count
  crosses a threshold, derived from code, historical only, no model in the
  loop. The capability D14 uses to justify itself already exists via the
  first durable record, without a fifth one.
- `docs/design/cache-spike.md` (the M4 spike) is the closest precedent in
  this repo to this exact decision, and it refused to build for the same
  shape of reason: *"Do not build `evidence_store.py`-shaped scaffolding for
  this ahead of time ... it is solving a fundamentally different granularity
  and lifecycle ... Building that shape now, against no real [reader/writer],
  would be guessing at the shape of a contract nobody has written yet."* A
  schema'd event store with no event-driven intake (B-201, `DEFERRED`) to
  feed it is exactly that empty shell -- the writer would have nothing but
  `evidence_store` snapshots to derive events from, which is the store this
  module already has, not a new one.
- It would durably overlap `evidence_store` (both would answer "what has
  this object done over time") while being allowed to disagree with it,
  which is the specific failure this change was warned to avoid: a fifth
  record that can drift from a fourth is worse than no fifth record.

**Not wired into `cli.py`, `investigation.py`, or `agent_loop.py`.** Those
files belong to other tracks this session and are out of scope for this
change. This module ships the recorder, the query surface, and its tests
only -- the same posture `ledger.py` and `ticket.py` themselves shipped
under, each documenting the exact call its own wiring needs without applying
it. The call this module needs, once a caller owns session identity: after
`ticket.open_ticket(...)` returns and a flow/device/subject are resolved,
`session_memory.record_turn(session_id, device=..., subject=..., flow=...,
run_id=ticket.run_id, ticket_path=ticket.path)`; at the start of a run, before
prompting the human for a device/subject that was not supplied on the command
line, `session_memory.recall(session_id)` and fall back to its `turn` fields
only when `outcome == FOUND`. Minting/holding `session_id` itself (one
per interactive sitting -- a shell PID, an explicit `--session` flag, a
generated value cached in a dotfile) is a CLI-layer decision this module does
not make.

Found nothing vs. could not find out (OBS-188, OBS-202's shape, applied here)
----------------------------------------------------------------------------------
This build's single most repeated defect is absence reported as a value.
`config_diff.py`'s `CANNOT_COMPARE` (B-106/D16) is the pattern this module
copies directly, one level up: a session lookup has **three** outcomes, not
two. `FOUND` -- a turn exists, and `turn` is populated. `NOT_FOUND` -- the
store was read successfully and no turn was ever recorded for this session id
(a fresh session is a normal, checked answer, not silence). `CANNOT_RECALL`
-- the store could not be read at all (corrupt file, an unreadable backend);
the query did not run to a real answer, and reporting this as `NOT_FOUND`
would make a caller silently forget an existing session was ever underway,
indistinguishable from it genuinely being new -- the exact "a name/lookup
that fails to match reads the same as a thing with nothing to report" shape
OBS-202 measured for interface names and OBS-188 measured for a failed
pytest subprocess. `RecallResult.__post_init__` refuses to construct a
`CANNOT_RECALL` with no `reason`, mirroring `FieldDiff.__post_init__`'s
refusal to construct a reason-less `CANNOT_COMPARE` (B-540).

Storage: `evidence_store.py`'s shape, not its import
---------------------------------------------------------
Reuses the established idiom -- an `ABC` plus a `get_store()` factory --
rather than inventing a parallel one, per the M4 spike's own note that this
*pattern* (not its granularity or lifecycle) is the right thing to imitate.
Does not import from `evidence_store.py` or `ledger.py`: `ticket.py`'s own
docstring gives the reason this module also follows -- these files are
plausibly being changed by separate tracks in the same session, and a hard
import dependency between two modules that are each somebody else's is a
self-inflicted breakage risk this module's job does not require taking on.
Small helpers (`_atomic_write_text`, id/timestamp helpers) are therefore
re-declared locally rather than imported, the same trade `ticket.py` names
explicitly for the same reason.

Only one backend exists today: `FileSessionMemoryStore`, one small JSON file
per session (default directory `DEFAULT_SESSION_MEMORY_DIR`,
`session_memory/<session_id>.json`, resolved by `get_store(base_dir=...)`
exactly the way `evidence_store.get_store(base_dir=...)` resolves its own
directory). `evidence_store.py` earned its SQLite backend (B-205) from a
real, demonstrated need: cross-device, cross-time queries ("every interface
that flapped five times this week") that a directory walk cannot answer
efficiently. Session memory has no such need -- it is a single exact-key
lookup for one session at a time, never a query across many -- so building a
second backend now would repeat the cache spike's "empty shell" mistake at a
smaller scale: capability added and never exercised (OBS-121).

**No new environment variable, for the same reason `ledger.py` has none: the
module that owns `settings.py` is another track this session**, and
`tests/test_settings.py::test_every_source_env_var_is_declared` fails loudly
(by design) on any `_ENV`-suffixed constant this module cannot also declare
in `settings.SETTINGS`. Unlike `ledger.py`, though, this module still ships a
*real* default directory rather than an in-memory-only one: `ledger.py`'s
in-memory default is fine because a slow-accumulating accuracy corpus losing
one process's worth of diagnoses is a real but acceptable limit; a session
memory whose entire purpose is surviving *between* separate `nettools`
process invocations would be pointless with an in-memory-only default, since
"nettools <command> is a fresh process every time" (`metrics.py`'s own
docstring) is exactly the boundary this module exists to survive. So
`get_store(base_dir=None)` resolves to the literal relative path
`DEFAULT_SESSION_MEMORY_DIR` when no `base_dir` is given -- a real default,
just not an environment-configurable one yet. Once a track owns
`settings.py`, adding `NETTOOLS_SESSION_MEMORY_DIR` there and reading it here
(mirroring `NETTOOLS_TICKET_DIR`) is a small, additive follow-up; it is not
done in this change because it cannot be done honestly without touching a
file this change was told not to touch.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "CANNOT_RECALL",
    "DEFAULT_SESSION_MEMORY_DIR",
    "FOUND",
    "NOT_FOUND",
    "OUTCOMES",
    "FileSessionMemoryStore",
    "RecallResult",
    "RecordWriteResult",
    "SessionMemoryStore",
    "Turn",
    "forget",
    "get_store",
    "list_sessions",
    "prune",
    "recall",
    "record_turn",
]

#: The three outcomes a session lookup can report. Closed, mirroring
#: `config_diff.OUTCOMES` -- see the module docstring's "Found nothing vs.
#: could not find out" section.
FOUND = "found"
NOT_FOUND = "not_found"
CANNOT_RECALL = "cannot_recall"
OUTCOMES: frozenset[str] = frozenset({FOUND, NOT_FOUND, CANNOT_RECALL})

#: The real (but not environment-configurable -- see the module docstring's
#: "Storage" section on why) default directory, resolved by `get_store` when
#: no `base_dir` is given. Deliberately NOT named with an `_ENV` suffix and
#: NOT read via `os.getenv` anywhere in this module: `settings.py` is out of
#: scope for this change, and `tests/test_settings.py` fails loudly on any
#: undeclared env var discovered in `src/` -- see that module docstring
#: section for the full reasoning.
DEFAULT_SESSION_MEMORY_DIR = "session_memory"

#: Session ids are joined directly into a filesystem path
#: (`FileSessionMemoryStore._path`). Bounds what a session id is allowed to
#: look like at the storage boundary itself, the same discipline
#: `evidence_store._validate_device_name` applies to device names and for
#: the same reason (B-474 / DEEP-REVIEW-2026-08-17 SS2.4): an API that merely
#: trusts its callers' discipline instead of enforcing its own contract is
#: not actually guarded. "." and ".." pass the length-1/2 character-class
#: fullmatch below (both are legal runs of "."), so they are excluded
#: explicitly rather than relied on the regex alone to catch.
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,128}")


def _validate_session_id(session_id: Any) -> str:
    if (
        not isinstance(session_id, str)
        or session_id in (".", "..")
        or not _SESSION_ID_RE.fullmatch(session_id)
    ):
        raise ValueError(f"invalid session id for session memory storage: {session_id!r}")
    return session_id


def _timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_dir(base_dir: str | None) -> Path:
    return Path(base_dir or DEFAULT_SESSION_MEMORY_DIR)


def _atomic_write_text(path: Path, text: str) -> None:
    """Write via tempfile in the same directory + fsync + os.replace.

    Re-declared locally rather than imported from `evidence_store.py` -- see
    the module docstring's "Storage" section. Same-directory tempfile matters
    for the same reason `evidence_store._atomic_write_text` gives: `os.replace`
    is only atomic within one filesystem.
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
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


@dataclass(frozen=True)
class Turn:
    """One session's most recent turn: identity and a pointer, never content.

    Every field here is a plain copy of a value the caller already had --
    see the module docstring's "Three consequences" section. There is no
    `finding` field on purpose: the ticket named by `ticket_path` is the
    single place that content lives, so this module can never disagree with
    it about what was found.
    """

    session_id: str
    recorded_at: str
    device: str | None = None
    subject: str | None = None
    flow: str | None = None
    run_id: str | None = None
    ticket_path: str | None = None


@dataclass(frozen=True)
class RecallResult:
    """What a session lookup found -- see the module docstring's "Found
    nothing vs. could not find out" section. Frozen and validated in
    `__post_init__` for the same reason `config_diff.FieldDiff` is: a result
    that could be constructed inconsistently a few frames from the read that
    produced it is a result nothing downstream can trust.
    """

    outcome: str
    session_id: str
    turn: Turn | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"RecallResult.outcome must be one of {sorted(OUTCOMES)}, got {self.outcome!r}")
        # The guard this module exists to enforce -- an absence must never
        # render as an unexplained value (OBS-188, OBS-202), applied to
        # session lookup exactly as B-540 applies it to config_diff.
        if self.outcome == CANNOT_RECALL and not self.reason:
            raise ValueError(
                "a cannot_recall RecallResult must carry a reason -- an absence with "
                "no explanation is indistinguishable from a session nobody checked"
            )
        if self.outcome == FOUND and self.turn is None:
            raise ValueError("a found RecallResult must carry a turn")
        if self.outcome != FOUND and self.turn is not None:
            raise ValueError(f"a {self.outcome} RecallResult must not carry a turn")


@dataclass(frozen=True)
class RecordWriteResult:
    """What happened when one turn was recorded. Same shape as
    `ledger.LedgerWriteResult`/`ticket.TicketWriteResult`: a write failure
    degrades (`persisted=False`, `warning` set) rather than raising, because
    a side-channel pointer must not be allowed to take an investigation down
    with it -- the same reasoning `ticket._write_block`'s broad `except`
    gives, applied here to a much smaller write.
    """

    session_id: str
    recorded_at: str
    persisted: bool
    warning: str | None = None


class SessionMemoryStore(ABC):
    """Common shape every backend implements. See the module docstring's
    "Storage" section for why only one backend exists today."""

    @abstractmethod
    def record_turn(
        self,
        session_id: str,
        *,
        device: str | None = None,
        subject: str | None = None,
        flow: str | None = None,
        run_id: str | None = None,
        ticket_path: str | None = None,
    ) -> RecordWriteResult:
        """Overwrite this session's current turn. Raises `ValueError` for an
        invalid `session_id` or a turn carrying no identity fields at all
        (every field besides `session_id` is `None`) -- both are caller bugs,
        not degrade-safe I/O conditions, and are validated eagerly rather
        than written as a useless record."""

    @abstractmethod
    def recall(self, session_id: str) -> RecallResult:
        """Look up a session's current turn. Never raises for a session that
        was never seen or a backing store that could not be read -- both are
        reported via `RecallResult.outcome` (`NOT_FOUND`/`CANNOT_RECALL`).
        Still raises `ValueError` for a structurally invalid `session_id`,
        the same eager-validation split `record_turn` makes."""

    @abstractmethod
    def forget(self, session_id: str) -> None:
        """Remove a session's current turn, if any. Never raises for a
        session that was never seen."""

    @abstractmethod
    def list_sessions(self) -> list[str]:
        """Return every session id with a currently recorded turn."""

    @abstractmethod
    def prune(self, *, keep_days: float) -> dict[str, Any]:
        """Forget every session whose turn was last recorded more than
        ``keep_days`` ago. Returns ``{"removed": [session_id, ...]}``.
        Hygiene only -- a long-running lab minting one session id per
        terminal would otherwise accumulate one file per session forever;
        this is not modelled on `evidence_store.prune`'s
        keep-days-or-keep-count union (there is exactly one row per session
        to keep or discard, not a history to retain a slice of)."""


class FileSessionMemoryStore(SessionMemoryStore):
    """One small JSON file per session: ``<dir>/<session_id>.json``."""

    def __init__(self, base_dir: str | None = None) -> None:
        self._root = _session_dir(base_dir)

    def _path(self, session_id: str) -> Path:
        _validate_session_id(session_id)
        return self._root / f"{session_id}.json"

    def record_turn(
        self,
        session_id: str,
        *,
        device: str | None = None,
        subject: str | None = None,
        flow: str | None = None,
        run_id: str | None = None,
        ticket_path: str | None = None,
    ) -> RecordWriteResult:
        path = self._path(session_id)
        if device is None and subject is None and flow is None and run_id is None and ticket_path is None:
            raise ValueError(
                "record_turn requires at least one of device/subject/flow/run_id/"
                "ticket_path -- a turn carrying none of them is not worth recording"
            )
        recorded_at = _timestamp_now()
        turn = Turn(
            session_id=session_id, recorded_at=recorded_at, device=device, subject=subject,
            flow=flow, run_id=run_id, ticket_path=ticket_path,
        )
        try:
            _atomic_write_text(path, json.dumps(turn.__dict__, indent=2, sort_keys=True))
            return RecordWriteResult(
                session_id=session_id, recorded_at=recorded_at, persisted=True,
            )
        except (OSError, TypeError, ValueError) as exc:
            warning = f"session memory write failed ({path}): {exc}"
            print(f"WARNING: {warning}", file=sys.stderr)
            return RecordWriteResult(
                session_id=session_id, recorded_at=recorded_at, persisted=False, warning=warning,
            )

    def recall(self, session_id: str) -> RecallResult:
        path = self._path(session_id)
        if not path.is_file():
            return RecallResult(outcome=NOT_FOUND, session_id=session_id)
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            turn = Turn(**data)
        except (OSError, ValueError, TypeError) as exc:
            # Corrupt or unreadable is CANNOT_RECALL, never NOT_FOUND -- see
            # the module docstring's "Found nothing vs. could not find out".
            reason = f"session memory file unreadable or corrupt: {path} ({exc})"
            print(f"WARNING: {reason}", file=sys.stderr)
            return RecallResult(outcome=CANNOT_RECALL, session_id=session_id, reason=reason)
        return RecallResult(outcome=FOUND, session_id=session_id, turn=turn)

    def forget(self, session_id: str) -> None:
        self._path(session_id).unlink(missing_ok=True)

    def list_sessions(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return sorted(p.stem for p in self._root.glob("*.json"))

    def prune(self, *, keep_days: float) -> dict[str, Any]:
        cutoff = time.time() - keep_days * 86400
        removed: list[str] = []
        for session_id in self.list_sessions():
            path = self._path(session_id)
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
                    removed.append(session_id)
            except OSError:
                continue
        return {"removed": removed}


def get_store(base_dir: str | None = None) -> SessionMemoryStore:
    """Return the session memory store: `FileSessionMemoryStore`, resolved
    against ``base_dir`` or `DEFAULT_SESSION_MEMORY_DIR`.

    Only one backend exists today -- see the module docstring's "Storage"
    section for why a second one has no demonstrated need yet, and why this
    factory has no environment-variable selector (unlike
    `evidence_store.get_store`) until a track that owns `settings.py` adds
    one. `base_dir` is the only override, exactly `evidence_store.get_store`'s
    own parameter of the same name and purpose.
    """

    return FileSessionMemoryStore(base_dir)


def record_turn(
    session_id: str,
    *,
    device: str | None = None,
    subject: str | None = None,
    flow: str | None = None,
    run_id: str | None = None,
    ticket_path: str | None = None,
    store: SessionMemoryStore | None = None,
) -> RecordWriteResult:
    return (store or get_store()).record_turn(
        session_id, device=device, subject=subject, flow=flow, run_id=run_id,
        ticket_path=ticket_path,
    )


def recall(session_id: str, *, store: SessionMemoryStore | None = None) -> RecallResult:
    return (store or get_store()).recall(session_id)


def forget(session_id: str, *, store: SessionMemoryStore | None = None) -> None:
    (store or get_store()).forget(session_id)


def list_sessions(*, store: SessionMemoryStore | None = None) -> list[str]:
    return (store or get_store()).list_sessions()


def prune(*, keep_days: float, store: SessionMemoryStore | None = None) -> dict[str, Any]:
    return (store or get_store()).prune(keep_days=keep_days)
