"""B-446: the ticket -- an append-only markdown flight recorder, one file per
interaction, that an escalation team can paste straight into a real ticket
without asking "what did you actually run, and when?"

The requirement, verbatim
--------------------------
`docs/OPERATIONS-REVIEW.md` lines 211-228 is a network engineer's own list of
what a "ticket-ready evidence package" needs: incident/run ID and tool
version; start/finish time and device clock information; the object under
investigation; current state; last reset reason; the exact route/next-hop/
interface checked; the commands that succeeded, failed or were not
supported; *"the cited evidence or compact command excerpts -- not only
opaque evidence-key names"*; log coverage and its gaps; what the tool did
not check; and the proposed cause kept visibly separate from confirmed
observations. That is the spec this module builds against.

The one rule that makes this module worth building at all
-----------------------------------------------------------
**The ticket records what the CODE observed, never what a model SAYS it
did.** `docs/build/FINDINGS.md` OBS-165 and `MCP-EXPERIMENT.md` §12.5 measured
this directly: one model claimed it covered all five dependency rungs while
listing three, a second model correctly named the two rungs it omitted --
and the two self-reports were **indistinguishable at read time**. Both were
fluent, specific and confident; nothing in either answer's form said which
one a reader was holding. A ticket that recorded a model's summary of its
own coverage would inherit that exact failure mode, silently, on the one
artifact this project explicitly wants an escalation team to trust without
re-deriving it by hand. So every field this module writes has to come from
instrumentation -- a timer, a counter, a typed result object -- never from a
model's prose about what it did. Where a caller has no instrumented value
for a field the spec asks for, the honest move is to leave the field out,
not to ask a model to fill the gap; see the accompanying build report for
the fields this module could not source and why.

Ledger sibling, not ledger duplicate
--------------------------------------
`ledger.py` (B-485) is this module's closest relative and was read closely
before writing a line here: lazy path resolution, degrade-safe writes that
report `persisted`/`warning` instead of swallowing a failure, append-only
storage, a human verdict that the tool can never supply for itself. This
module reuses every one of those conventions and deliberately does **not**
reuse `ledger.py` itself (no import) -- the two files were being built in
the same session by separate tracks, and a hard import dependency between
two modules actively changing underfoot is a self-inflicted breakage risk
neither module's job requires taking on. Where the vocabulary genuinely
overlaps (the three-value outcome a human records: confirmed correct,
incorrect, or -- until then -- unknown) this module repeats the same three
spellings on purpose, as a documented convention, not a runtime coupling.

What is different, and why it cannot just be "ledger, but markdown":
the ledger is *one growing log* that many diagnoses append to, read back as
a corpus (`summary()`, accuracy by source/outcome). A ticket is *one file
per interaction* -- a self-contained bundle a human pastes into a real
ticketing system, per the spec above. So where the ledger has one
process-wide `default_ledger` instance that many diagnoses accumulate into,
this module's process-wide default (`default_recorder`) is a lightweight
*factory* that mints a brand new `Ticket` (a brand new file) on every call --
the "one shared log" shape does not apply here, and forcing it to would
produce one giant markdown file interleaving unrelated interactions, which
is worse than no ticket at all for an escalation team trying to isolate one
incident. This module intentionally builds no cross-ticket aggregation
(no `summary()`, no accuracy-by-outcome roll-up) -- that is the ledger's job,
and duplicating it here was explicitly out of scope for this change.

The header format: a fenced JSON block, not YAML front matter
-----------------------------------------------------------------
Both read as "front matter" to a human. JSON was chosen because it is
already this codebase's house style for every structured artifact a human
might also open by hand -- `ledger.py`'s JSONL entries, `metrics.py`'s
snapshot, `evidence_store.py`'s saved evidence, the payload
`InvestigationResult.to_payload()` renders. Nothing in `src/agent_nettools`
writes YAML; `inventory_model.py` only *reads* it, for a config file humans
hand-edit, which is a different job (hand-editing) from this module's
(machine-written, only ever hand-*read*). `json.dumps` also gives an exact,
untunable round trip for arbitrary strings -- newlines, quotes, backticks,
markdown-looking substrings inside a device name or a pasted question --
with zero risk of YAML's well-known footguns (bare `no`/`yes`/`on` becoming
booleans, indentation-significant multiline strings). A fenced code block
renders cleanly in every markdown viewer a human already uses to read
`FINDINGS.md`, and is trivial to locate programmatically by its info-string
without a YAML parser dependency this project does not otherwise need.

Append-only, `FINDINGS.md`'s rule applied to one file instead of a log book
-------------------------------------------------------------------------------
`FINDINGS.md`'s own rules (its file header) are "never edit or delete an
entry; to correct one, write a new entry that references the old ID." This
module applies exactly that discipline to a single file: every `record_*`
call *appends* one new `## ` section (heading, optional human-readable
narrative, one fenced JSON block) to the end of the file and never opens the
file for anything but `"a"` (append) or, for the very first write, `"x"`
(exclusive create -- see `_claim_path`). Nothing already written is ever
reread, reformatted or rewritten. Two direct consequences fall out of that
for free: a process that crashes mid-investigation leaves a ticket that is
truncated but still fully parseable up to the last completed section
(the same crash-tolerance property JSONL logging gives `ledger.py` and
`network_tools._audit_log`); and a human's verdict, recorded by
`record_ticket_outcome` at any later time -- a separate process, a different
day -- is *also* just another appended section, never an edit to the
"Answer" section it comments on. `read_ticket` resolves "the current
outcome" the same way `ledger.diagnoses()` resolves "the current verdict":
the *last* section of a given kind wins, and every earlier one -- including
a superseded outcome -- stays in the file, in `sections`, forever.

Why section headings are not numbered
------------------------------------------
`InvestigationResult.to_payload()` numbers its rungs (`"position": n, "of":
N`) specifically so a restatement that drops one is visibly short next to
the full list (OBS-115). That precedent does not transfer here: those
numbers are rendered once, in one process, from a complete list already in
hand. A ticket's sections accumulate *incrementally*, and `record_ticket_outcome`
can append to a ticket file from a process that has never read the file and
has no way to know how many sections already exist without paying for a
read first. A stale or guessed number would be actively misleading -- worse
than an honest, unnumbered `## Outcome update` heading. Ordering is still
exactly preserved (append order *is* file order), and `read_ticket`'s
`sections` list carries it; nothing here needed a number to stay correct.

Degrade-safe: writes, never the caller
------------------------------------------
Every public entry point that touches disk -- opening a ticket, every
`record_*`, `close()`, `record_ticket_outcome` -- returns a `TicketWriteResult`
carrying `persisted: bool` and `warning: str | None`, exactly
`ledger.LedgerWriteResult`'s shape, and **never raises for an I/O or
serialisation problem**. The one place this module goes further than
`ledger.py`: `_write_block` wraps both the `json.dumps` call *and* the disk
write in the same broad `except Exception` (not just
`OSError`/`TypeError`/`ValueError`) -- mirrored from
`network_tools._netmiko_send_commands`'s own `except Exception as exc:  #
noqa: BLE001` around a live session, for the same reason: this module sits
on a side channel to a live investigation, and a bug in *this module's own
rendering code* (not just a hostile disk) must not be allowed to take the
investigation down with it. A caller that hands `record_answer` a `cause`
object `json.dumps` cannot encode gets a degraded, warned write -- never a
traceback out of the middle of an incident.

Directory resolution: a real default, unlike `ledger.py`
--------------------------------------------------------------
`ledger.py` ships with *no* default on-disk path -- diagnoses are
in-memory-only unless a caller opts in -- because `settings.py` belonged to
another track during that session and the ledger is a slow-accumulating
accuracy corpus, not something every run needs on disk immediately. Neither
reason applies here: this module owns its one `settings.py` row (see
`NETTOOLS_TICKET_DIR` there), and the entire point of a ticket, per the
spec quoted above, is a persistent bundle an escalation team can open --
not an opt-in. So `TicketRecorder` resolves a real default directory
(`DEFAULT_TICKETS_DIR = "tickets"`, mirroring `evidence_store.DEFAULT_SNAPSHOT_DIR
= "evidence"`) the same *lazy* way `metrics._metrics_path` and
`ledger.DiagnosisLedger._load_once` resolve theirs: read `os.environ` only
inside `TicketRecorder._resolve_dir`, called at `.open()` time, never at
import or construction time -- `NETTOOLS_TICKET_DIR` is only meaningful once
`.env` has actually been loaded, which `cli.main()`/`mcp_server.server` do
after this module is already imported as a dependency.

What this module does not do
---------------------------------
It does not call itself. Nothing in `cli.py`, `investigation.py`,
`agent_loop.py`, `event_routing.py` or `mcp_server/server.py` calls
`open_ticket` yet -- those files belong to other tracks this session (see
the accompanying build report for the exact call this needs at each entry
point). It provides the recorder and the file format only. It does not
decide *when* a ticket should be opened, does not read `InvestigationResult`
or any other project type directly (every `record_*` method takes plain,
already-extracted values so this module has no import-time dependency on
the investigation layer), and does not aggregate across tickets -- that is
either a future module or, if the fields genuinely belong there, the
ledger's.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__

#: The human verdict on one ticket's answer. Deliberately the same three
#: spellings `ledger.py` uses for the same concept (a human confirming
#: whether a diagnosis was right) -- see the module docstring's "Ledger
#: sibling, not ledger duplicate" section for why this is a documented
#: convention, not an import.
CONFIRMED_CORRECT = "confirmed_correct"
INCORRECT = "incorrect"
UNKNOWN = "unknown"
OUTCOMES: tuple[str, ...] = (CONFIRMED_CORRECT, INCORRECT, UNKNOWN)

#: Section kinds. Stored inside each section's JSON block as "kind"; never
#: in the markdown heading, which exists for a human, not the parser.
KIND_QUESTION = "question"
KIND_INTENT = "intent"
KIND_TOOL_EVENT = "tool_event"
KIND_DEVICE_INTERACTION = "device_interaction"
KIND_EVIDENCE_SOURCE = "evidence_source"
KIND_CONTEXT_FOOTPRINT = "context_footprint"
KIND_ANSWER = "answer"
KIND_OUTCOME = "outcome"
KIND_CLOSED = "closed"

_HEADER_FENCE_INFO = "json-ticket-header"
_SECTION_FENCE_INFO = "json-ticket-section"
TICKET_SCHEMA_VERSION = 1

NETTOOLS_TICKET_DIR_ENV = "NETTOOLS_TICKET_DIR"
DEFAULT_TICKETS_DIR = "tickets"

#: Bound on `excerpt`/`detail`-shaped free-text fields. Same value and the
#: same reasoning as `mcp_server/boundary.py`'s `MAX_ERROR_CHARS` (B-458: "a
#: transport exception can embed device output in an error string") -- a
#: bounded excerpt is what the spec asks for ("compact command excerpts --
#: not only opaque evidence-key names"), and a caller passing a raw
#: multi-kilobyte capture into one of these fields by mistake must not turn
#: one ticket section into a second, unaccounted-for copy of the evidence
#: store. This caps one call's contribution; it does not enforce a
#: file-wide budget -- see the accompanying build report.
_MAX_TEXT_FIELD_CHARS = 400

#: How many filename suffixes `_claim_path` will try before giving up and
#: degrading. See `_claim_path`'s own docstring.
_MAX_PATH_ATTEMPTS = 25


def _timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_nonempty_str(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")
    return value


def _require_nonneg_int(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative int, got {value!r}")
    return value


def _cap_text(value: str | None, limit: int = _MAX_TEXT_FIELD_CHARS) -> str | None:
    """Bound a free-text field. `None` passes through; anything else is
    coerced to `str` first so a caller's mistake here degrades (a slightly
    odd rendering) rather than raising -- consistent with this module's
    wider "never raise on bad input, degrade and say so" posture, applied at
    the field level instead of only at the write level."""

    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated, {len(text)} chars total]"


def _merge_extra(fields: dict[str, Any], extra: dict[str, Any] | None) -> dict[str, Any]:
    """`extra` first, named fields second, so a caller's `extra` dict can
    never clobber a field this method already named explicitly -- e.g.
    `extra={"tool": "spoofed"}` passed to `record_tool_event` cannot
    overwrite the real `tool` argument."""

    merged: dict[str, Any] = dict(extra) if extra else {}
    merged.update(fields)
    return merged


def _blockquote(text: str) -> str:
    """Prefix every line of free-form narrative with `"> "`.

    This is the whole reason adversarial narrative -- a pasted question that
    happens to contain a line of three backticks, or a line starting with
    `#` -- cannot corrupt the file's structure. CommonMark fences and ATX
    headings must start at column 0; a blockquoted line starts with `"> "`
    and can therefore never be mistaken, by a human's renderer or by this
    module's own line-scanning parser, for one of *this module's* fence
    delimiters or section headings. See `_parse_ticket_text`, which relies
    on exactly this property.
    """

    lines = text.splitlines() or [""]
    return "\n".join((f"> {line}" if line else ">") for line in lines) + "\n"


def _heading_safe(text: str) -> str:
    """Flatten a caller-supplied string so it cannot forge document structure.

    `_blockquote` protects the NARRATIVE parameter, and its docstring claims
    adversarial input "cannot corrupt the file's structure". That claim was
    true of narrative and **false of every other caller string that reaches a
    heading** -- `subject`, `tool`, `device`, `evidence_key` -- each of which
    was interpolated raw into a markdown heading line.

    Measured, not theorised (adversarial bug hunt, 2026-08-19): a subject
    carrying embedded newlines, a `## Outcome update` heading and a
    `json-ticket-section` fence produced a ticket whose `read_ticket()` outcome
    was `{"outcome": "confirmed_correct", "by": "attacker-via-cli"}`. A forged
    human verdict, on a diagnosis nobody judged, in the artefact whose entire
    purpose is that **the tool cannot mark its own homework**. Reachable
    through the real CLI, on any flow whose `subject_present` fails by
    returning rather than raising.

    A heading is one line by definition, so the fix is to make it one line:
    newlines and carriage returns become spaces, and a leading backtick run is
    neutralised. Nothing is dropped -- the value stays readable and the
    authoritative copy is in the JSON block below the heading, where it is
    already quoted by `json.dumps`.
    """

    flat = " ".join(str(text).splitlines())
    return flat.replace("`", "'").strip() or "(empty)"


def _slug(text: str) -> str:
    """Filesystem-safe stem for the ticket filename's subject component.

    Same shape as `fixtures.command_slug` (lowercase, non-alnum runs
    collapsed to one `-`, no leading/trailing `-`), applied to a free-form
    subject string instead of an approved device command -- reusing the
    established idiom rather than inventing a second one.
    """

    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "ticket"  # An empty/all-punctuation subject still needs a legal filename.


def _write_block(
    path: Path,
    open_mode: str,
    *,
    heading: str,
    fence_info: str,
    data: dict[str, Any],
    narrative: str | None = None,
) -> tuple[bool, str | None]:
    """Render one header/section block and write it in a single shot.

    ``open_mode`` is ``"x"`` (exclusive create -- the ticket's very first
    write; see `_claim_path`) or ``"a"`` (append -- every write after).
    ``json.dumps`` and the blockquote/text assembly happen *inside* this
    try block on purpose: a caller handing `record_answer` a `cause` object
    `json.dumps` cannot encode, or a non-string `narrative`, must degrade
    the same way a full disk does -- not raise out of the middle of a live
    investigation. The broad ``except Exception`` (not just
    ``OSError``/``TypeError``/``ValueError``) is deliberate for the same
    reason `network_tools._netmiko_send_commands` catches broadly around a
    live session: this is the one place in the module where a bug in this
    module's *own* code must not be allowed to reach the caller. Never
    raises except `FileExistsError`, which the exclusive-create retry loop
    in `_claim_path` specifically wants to see.
    """

    try:
        parts = [heading]
        if narrative:
            parts.append(_blockquote(str(narrative)))
            parts.append("\n")
        parts.append(f"```{fence_info}\n")
        # default=str: a non-JSON-serialisable value (an object slipped into
        # `extra`/`cause`/`coherence`) becomes its str() instead of raising --
        # belt-and-suspenders alongside the broad except below.
        parts.append(json.dumps(data, indent=2, sort_keys=True, default=str))
        parts.append("\n```\n\n")
        text = "".join(parts)

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, open_mode, encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        return True, None
    except FileExistsError:
        # Deliberate, but ONLY for the exclusive-create claim: `_claim_path`
        # needs to SEE this to retry with a numeric suffix. On an append it
        # must degrade like anything else -- re-raising it there breaks the
        # one guarantee this module has ("a ticket write failure never fails
        # an investigation"), which is exactly how it broke: a tickets
        # directory that is actually a FILE makes the mkdir below raise
        # FileExistsError on every append, straight through to the caller.
        # Found by the orchestrator's own probe, 2026-08-18.
        if open_mode == "x":
            raise
        warning = f"ticket write failed ({path}): the ticket directory is not a directory"
        print(f"WARNING: {warning}", file=sys.stderr)
        return False, warning
    except Exception as exc:  # noqa: BLE001 -- see docstring: rendering AND writing must both degrade, never raise.
        warning = f"ticket write failed ({path}): {exc}"
        print(f"WARNING: {warning}", file=sys.stderr)
        return False, warning


def _claim_path(directory: Path, stamp: str, slug: str, header_text_parts: dict[str, Any]) -> tuple[Path, bool, str | None]:
    """Exclusively create the ticket file, retrying with a numeric suffix on
    a same-instant collision.

    Only the header write needs this: every later `record_*` targets a path
    already known to exist, so it always opens `"a"`. Filenames carry
    microsecond resolution (`stamp`), so a real collision needs two tickets
    for the same subject opened within the same microsecond -- vanishingly
    unlikely for this project's single-user CLI, but "vanishingly unlikely"
    silently merging two different interactions' sections into one file
    would be exactly the kind of defect this module's whole design exists to
    prevent, so it is handled rather than assumed away. Bounded at
    `_MAX_PATH_ATTEMPTS`: a persistently unwritable directory must degrade
    (`persisted=False`, a warning), not loop.
    """

    candidate = directory / f"{stamp}_{slug}.md"
    for attempt in range(_MAX_PATH_ATTEMPTS):
        if attempt:
            candidate = directory / f"{stamp}_{slug}-{attempt + 1}.md"
        try:
            persisted, warning = _write_block(
                candidate, "x",
                heading=f"# Ticket: {_heading_safe(header_text_parts['subject'])}\n\n",
                fence_info=_HEADER_FENCE_INFO,
                data=header_text_parts,
            )
            return candidate, persisted, warning
        except FileExistsError:
            continue  # Same-instant collision -- try the next suffix.
    warning = (
        f"could not claim a free ticket filename under {directory} "
        f"after {_MAX_PATH_ATTEMPTS} attempts"
    )
    print(f"WARNING: {warning}", file=sys.stderr)
    return candidate, False, warning


def _validate_outcome(outcome: str, by: str) -> tuple[str, str]:
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}, got {outcome!r}")
    by = _require_nonempty_str(
        "by", by
    )  # No default identity -- the tool must never mark its own homework here either.
    return outcome, by


@dataclass(frozen=True)
class TicketWriteResult:
    """What happened when one block (header or section) was written.

    Same shape and the same reason as `ledger.LedgerWriteResult`:
    ``persisted`` is false both for "the write failed" and would be false for
    "no directory configured" if this module had that mode -- it does not
    (see the module docstring's "Directory resolution" section), so in
    practice a false here always means a real, warned failure. ``path`` is
    always populated, even on failure, because the path is computed
    in-memory before any disk access is attempted -- a caller can always
    report *where* a ticket was trying to write even when it could not.
    """

    path: str
    recorded_at: str
    persisted: bool
    warning: str | None = None


class Ticket:
    """One open ticket: one markdown file, appended to as an interaction
    proceeds. Construct through `TicketRecorder.open` / `open_ticket`, not
    directly -- those mint `run_id`, resolve the directory, and perform the
    first (header) write; this class only knows how to append to a path it
    has already been handed.

    Thread-safe (a lock guards every append), matching `ledger.DiagnosisLedger`
    -- cheap, and this project already runs a thread pool over `check_fabric`
    and a tool-calling loop in `agent_loop.py`, either of which could in
    principle hold one ticket open across concurrent tool calls.
    """

    def __init__(
        self,
        *,
        path: Path,
        run_id: str,
        header: dict[str, Any],
        open_result: TicketWriteResult,
    ) -> None:
        self.path = path
        self.run_id = run_id
        self.header = dict(header)
        #: The result of the header write specifically -- exposed so a
        #: caller can tell, right at `open_ticket()`, whether this ticket is
        #: actually landing on disk before doing any further work.
        self.open_result = open_result
        self._lock = threading.Lock()
        self._sections_written = 0
        self._closed = False

    def _write(
        self, kind: str, fields: dict[str, Any], *, title: str, narrative: str | None = None
    ) -> TicketWriteResult:
        data: dict[str, Any] = {"kind": kind, "recorded_at": _timestamp_now(), **fields}
        with self._lock:
            persisted, warning = _write_block(
                self.path, "a",
                heading=f"## {_heading_safe(title)}\n\n",
                fence_info=_SECTION_FENCE_INFO,
                data=data,
                narrative=narrative,
            )
            self._sections_written += 1
        return TicketWriteResult(
            path=str(self.path), recorded_at=data["recorded_at"],
            persisted=persisted, warning=warning,
        )

    # -- the question as asked --------------------------------------------

    def record_question(
        self,
        question: str,
        *,
        device: str | None = None,
        subject: str | None = None,
        flow_hint: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> TicketWriteResult:
        """The raw question/subject text, verbatim -- never reworded by a
        model. `question` is not length-capped (unlike `excerpt`/`detail`):
        it is operator-typed text, not a possible copy of device output, and
        the spec explicitly wants what was actually asked."""

        question = _require_nonempty_str("question", question)
        fields = _merge_extra(
            {"question": question, "device": device, "subject": subject, "flow_hint": flow_hint},
            extra,
        )
        return self._write(KIND_QUESTION, fields, title="Question", narrative=question)

    # -- how intent was built ----------------------------------------------

    def record_intent(
        self,
        *,
        flow: str | None = None,
        resolved_subject: str | None = None,
        resolver: str | None = None,
        notes: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> TicketWriteResult:
        """How the raw question became a resolved intent: which flow, which
        resolved subject, and what resolved it (e.g. `"inventory_resolver"`,
        `"explicit"`) -- code-side facts about resolution, not a model's
        account of its own reasoning."""

        fields = _merge_extra(
            {"flow": flow, "resolved_subject": resolved_subject, "resolver": resolver, "notes": notes},
            extra,
        )
        return self._write(KIND_INTENT, fields, title="Intent", narrative=notes)

    # -- the tool timeline ---------------------------------------------------

    def record_tool_event(
        self,
        tool: str,
        *,
        status: str,
        device: str | None = None,
        command: str | None = None,
        duration_ms: float | None = None,
        retries: int | None = None,
        started_at: str | None = None,
        detail: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> TicketWriteResult:
        """One entry in the tool timeline. Called once per tool/command
        invocation, as it happens -- the timeline in `read_ticket()` is
        assembled by collecting every `tool_event` section in append order,
        not written as one batch at the end, which is what makes it a
        genuine flight recording rather than a summary produced after the
        fact. `detail` is capped (`_cap_text`) -- see `_MAX_TEXT_FIELD_CHARS`'s
        docstring on why a free-text status field is exactly where a raw
        device excerpt tends to leak in by accident (B-458)."""

        tool = _require_nonempty_str("tool", tool)
        status = _require_nonempty_str("status", status)
        fields = _merge_extra(
            {
                "tool": tool, "status": status, "device": device, "command": command,
                "duration_ms": duration_ms, "retries": retries, "started_at": started_at,
                "detail": _cap_text(detail),
            },
            extra,
        )
        title = f"Tool event -- {tool}" + (f" @ {device}" if device else "")
        return self._write(KIND_TOOL_EVENT, fields, title=title)

    # -- device interactions -------------------------------------------------

    def record_device_interaction(
        self,
        device: str,
        *,
        session_count: int,
        latency_ms: float | None = None,
        retries: int | None = None,
        commands_run: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> TicketWriteResult:
        """One device's session-level cost for this interaction: how many
        SSH sessions it took, total latency, and retries consumed -- the
        `network_tools._netmiko_send_commands`/`metrics.record_collection`
        shape, per device, per interaction (metrics.py aggregates across
        interactions; this is the one-interaction slice the spec's "device
        interactions" bullet asks for).

        `retries` defaults to `None`, not `0` -- it used to default to `0`,
        which is indistinguishable from "zero retries were measured" and is
        exactly OBS-188's defect class (a default standing in for a
        measurement never taken). No caller in this codebase measures a
        per-device retry count today: `_netmiko_send_commands` computes one,
        but `network_tools._section_from_combined` and the template runners
        (`run_template`/`run_templates_split`) strip it out of every per-
        intent/per-template envelope before it reaches an `Observation`, so
        `EvidenceEpoch` never sees it and cannot pass it on. Pass an actual
        int only when one was actually measured; `None` here means "not
        measured," matching `latency_ms`'s and `commands_run`'s own
        defaults."""

        device = _require_nonempty_str("device", device)
        session_count = _require_nonneg_int("session_count", session_count)
        if retries is not None:
            retries = _require_nonneg_int("retries", retries)
        fields = _merge_extra(
            {
                "device": device, "session_count": session_count, "latency_ms": latency_ms,
                "retries": retries, "commands_run": commands_run,
            },
            extra,
        )
        return self._write(KIND_DEVICE_INTERACTION, fields, title=f"Device interaction -- {device}")

    # -- evidence provenance per source --------------------------------------

    def record_evidence_source(
        self,
        evidence_key: str,
        *,
        device: str,
        source: str,
        command: str | None = None,
        collected_at: str | None = None,
        excerpt: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> TicketWriteResult:
        """Where one piece of cited evidence actually came from. `source`
        has no default (mirrors `ledger.py`'s `source` field and its own
        reasoning verbatim: a default of `"live"` would silently mislabel a
        `--from-fixtures` replay, and a default of `"fixture"` would
        undercount the real signal -- forcing the caller to say which, every
        time, is the whole point). `excerpt` is capped (`_cap_text`) -- the
        spec explicitly wants "compact command excerpts, not only opaque
        evidence-key names," and compact is the operative word."""

        evidence_key = _require_nonempty_str("evidence_key", evidence_key)
        device = _require_nonempty_str("device", device)
        source = _require_nonempty_str("source", source)
        fields = _merge_extra(
            {
                "evidence_key": evidence_key, "device": device, "source": source,
                "command": command, "collected_at": collected_at, "excerpt": _cap_text(excerpt),
            },
            extra,
        )
        return self._write(
            KIND_EVIDENCE_SOURCE, fields, title=f"Evidence -- {evidence_key} @ {device}"
        )

    # -- the context footprint -----------------------------------------------

    def record_context_footprint(
        self,
        *,
        chars_sent: int,
        chars_withheld: int = 0,
        per_section_chars: dict[str, int] | None = None,
        notes: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> TicketWriteResult:
        """What actually crossed into a model prompt, and what did not --
        the `evidence_budget.py`/grounding "withheld" shape, measured in
        characters (this project's own established proxy for tokens; see
        `evidence_budget.py`). `chars_withheld` includes both truncation
        (`evidence_budget._truncate_middle`) and an outright withheld
        paraphrase (`investigation.py`'s `WITHHELD` status) -- the caller
        knows which, this field only needs the total a reader can weigh
        against `chars_sent`."""

        chars_sent = _require_nonneg_int("chars_sent", chars_sent)
        chars_withheld = _require_nonneg_int("chars_withheld", chars_withheld)
        fields = _merge_extra(
            {
                "chars_sent": chars_sent, "chars_withheld": chars_withheld,
                "per_section_chars": per_section_chars, "notes": notes,
            },
            extra,
        )
        return self._write(KIND_CONTEXT_FOOTPRINT, fields, title="Context footprint", narrative=notes)

    # -- the answer -----------------------------------------------------------

    def record_answer(
        self,
        finding: str,
        *,
        trustworthy: bool,
        cause: dict[str, Any] | None = None,
        coherence: dict[str, Any] | None = None,
        report_status: str | None = None,
        correlation_status: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> TicketWriteResult:
        """The deterministic descent's own answer -- `finding`/`trustworthy`/
        `cause`/`coherence` are `InvestigationResult`'s own field names
        (`descent.finding`, `.trustworthy`, `.descent.cause`,
        `.descent.coherence`), passed through unchanged rather than
        re-derived, so this module never becomes a second place that could
        disagree with the descent about its own answer."""

        finding = _require_nonempty_str("finding", finding)
        if not isinstance(trustworthy, bool):
            raise ValueError(f"trustworthy must be a bool, got {trustworthy!r}")
        fields = _merge_extra(
            {
                "finding": finding, "trustworthy": trustworthy, "cause": cause,
                "coherence": coherence, "report_status": report_status,
                "correlation_status": correlation_status,
            },
            extra,
        )
        return self._write(KIND_ANSWER, fields, title="Answer")

    # -- the outcome slot -------------------------------------------------------

    def record_outcome(self, outcome: str, *, by: str, note: str | None = None) -> TicketWriteResult:
        """Append a human verdict from within the same process/lifecycle
        that opened this ticket. For the far more common case -- a human
        reviewing a closed ticket later, in a separate process -- use the
        module-level `record_ticket_outcome(path, ...)` instead; both
        funnel through the same `_write_block`/append machinery and are
        indistinguishable to `read_ticket`."""

        outcome, by = _validate_outcome(outcome, by)
        fields = {"outcome": outcome, "by": by, "note": note}
        return self._write(KIND_OUTCOME, fields, title="Outcome update", narrative=note)

    # -- close --------------------------------------------------------------

    def close(self) -> TicketWriteResult:
        """Mark the interaction complete. Does **not** write a default
        `outcome` -- an unresolved outcome is reported as `"unknown"` by
        `read_ticket` the same way `ledger.diagnoses()` reports one, as a
        real value computed at read time, never as something writing an
        explicit placeholder record would risk being mistaken for an actual
        human verdict. Safe to call more than once (each call appends
        another `Closed` marker; `read_ticket` reports the last one) --
        useful if a caller's own error-handling path calls `close()` in a
        `finally` block after an earlier, already-successful `close()`."""

        self._closed = True
        return self._write(
            KIND_CLOSED, {"sections_written": self._sections_written}, title="Closed"
        )


class TicketRecorder:
    """Configuration and factory for opening tickets. Constructing one does
    no I/O and reads no environment variable -- see the module docstring's
    "Directory resolution" section for why that has to be lazy.
    """

    def __init__(self, tickets_dir: str | os.PathLike[str] | None = None) -> None:
        self._explicit_dir = tickets_dir

    def _resolve_dir(self) -> Path:
        raw = self._explicit_dir or os.getenv(NETTOOLS_TICKET_DIR_ENV, "").strip() or DEFAULT_TICKETS_DIR
        return Path(raw)

    def open(
        self,
        subject: str,
        *,
        entry_point: str,
        device: str | None = None,
        flow: str | None = None,
        run_id: str | None = None,
        tool_version: str | None = None,
    ) -> Ticket:
        """Open one ticket: resolve the directory, mint (or accept) a
        `run_id`, and perform the header write. Never raises -- a failed
        header write still returns a fully usable `Ticket` whose every
        subsequent call will (independently, honestly) also try and likely
        also fail, exactly `ledger.py`'s "no permanent broken flag" posture
        (a transient failure, e.g. a briefly full disk, is allowed to
        recover on a later call rather than being latched as permanent).

        ``entry_point`` has no default, deliberately -- `"cli:investigate"`,
        `"agent_loop"`, `"route_event"`, `"mcp:<tool>"`, whatever the caller
        actually is. A defaulted value here would silently mislabel every
        entry point that forgot to pass one, exactly the failure mode
        `ledger.py`'s `source` field has no default to avoid.

        ``run_id`` is minted with `uuid.uuid4().hex` when not supplied --
        the same mechanism `ledger._new_id()` uses for a diagnosis id -- so
        a caller that already has one (e.g. one minted once per interaction
        and shared with a future `ledger.record_diagnosis` call once that
        wiring exists) can pass it through instead, making this ticket's
        `run_id` the join key the module docstring promises.
        """

        subject = _require_nonempty_str("subject", subject)
        entry_point = _require_nonempty_str("entry_point", entry_point)
        run_id = run_id or uuid.uuid4().hex
        opened_at = _timestamp_now()
        header: dict[str, Any] = {
            "schema_version": TICKET_SCHEMA_VERSION,
            "run_id": run_id,
            "opened_at_utc": opened_at,
            "entry_point": entry_point,
            "device": device,
            "subject": subject,
            "flow": flow,
            "tool_version": tool_version or __version__,
        }
        directory = self._resolve_dir()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        slug = _slug(subject)

        path, persisted, warning = _claim_path(directory, stamp, slug, header)
        open_result = TicketWriteResult(
            path=str(path), recorded_at=opened_at, persisted=persisted, warning=warning
        )
        return Ticket(path=path, run_id=run_id, header=header, open_result=open_result)


#: The process-wide default factory. Constructing it does no I/O (see
#: `TicketRecorder`'s own docstring), so a module-level instance at import
#: time is safe -- the same reasoning `ledger.default_ledger` and
#: `metrics.default_collector` already give for theirs.
default_recorder = TicketRecorder()


def open_ticket(
    subject: str,
    *,
    entry_point: str,
    device: str | None = None,
    flow: str | None = None,
    run_id: str | None = None,
    tool_version: str | None = None,
    recorder: TicketRecorder | None = None,
) -> Ticket:
    return (recorder or default_recorder).open(
        subject, entry_point=entry_point, device=device, flow=flow,
        run_id=run_id, tool_version=tool_version,
    )


def record_ticket_outcome(
    path: str | os.PathLike[str], outcome: str, *, by: str, note: str | None = None
) -> TicketWriteResult:
    """Append a human verdict to an existing ticket file, from any process.

    Mirrors `ledger.record_verdict`'s shape and its "no default identity,
    fail loudly on a bad value" rule verbatim: `outcome` must be one of
    `OUTCOMES` and `by` must be a real, non-empty name -- both raise
    `ValueError` immediately rather than being silently ignored, because a
    verdict is a deliberate one-off human action and a dropped one is
    exactly the invisible non-result this whole module exists to prevent.
    Unlike `ledger.record_verdict`, there is no `diagnosis_found`-style
    lookup: a ticket is one file, so the path itself is the only identity
    that could possibly be wrong, and a caller pointing this at a path that
    is not a ticket at all will simply get a normal degraded-write warning
    from `_write_block` when the append is attempted.
    """

    outcome, by = _validate_outcome(outcome, by)
    path = Path(path)
    data = {"kind": KIND_OUTCOME, "recorded_at": _timestamp_now(), "outcome": outcome, "by": by, "note": note}
    persisted, warning = _write_block(
        path, "a", heading="## Outcome update\n\n", fence_info=_SECTION_FENCE_INFO,
        data=data, narrative=note,
    )
    return TicketWriteResult(
        path=str(path), recorded_at=data["recorded_at"], persisted=persisted, warning=warning
    )


# --------------------------------------------------------------------------- #
# Reading a ticket back -- the round trip the spec requires ("queryable for
# analysis", not just readable by a person)
# --------------------------------------------------------------------------- #


def _read_fence_body(lines: list[str], start: int) -> tuple[list[str], int]:
    """Lines from `start` up to (not including) the next bare-```` line.

    Returns `(body_lines, index_just_past_the_closing_fence)`. A plain
    line-scan rather than one clever regex on purpose: this parser is the
    one place a subtly wrong pattern would corrupt data silently -- a ticket
    that "parses" into the wrong dict is worse than one that visibly fails
    -- and a state machine over lines is far easier to reason about (and to
    get right) than a DOTALL/MULTILINE regex over the whole file.
    """

    body: list[str] = []
    i = start
    while i < len(lines) and lines[i].rstrip() != "```":
        body.append(lines[i])
        i += 1
    return body, i + 1  # Skip the closing fence line itself.


def _parse_ticket_text(text: str) -> dict[str, Any]:
    """One pass over the file: the header dict, and every section in append
    order as `{"heading": str | None, "narrative": str, "data": dict}`.

    Relies on the invariant `_write_block` guarantees: a fence-opener line
    (`` ```json-ticket-header `` / `` ```json-ticket-section ``) and a
    section heading (`## `) can only ever appear at column 0 in text this
    module itself wrote -- never inside blockquoted narrative, which is
    exactly what `_blockquote` exists to guarantee (see its docstring).
    """

    lines = text.splitlines()
    header: dict[str, Any] | None = None
    sections: list[dict[str, Any]] = []
    current_heading: str | None = None
    narrative_lines: list[str] = []

    def flush_narrative() -> str:
        stripped = [
            ln[2:] if ln.startswith("> ") else (ln[1:] if ln == ">" else ln)
            for ln in narrative_lines
        ]
        return "\n".join(stripped).strip()

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.rstrip() == f"```{_HEADER_FENCE_INFO}":
            body, i = _read_fence_body(lines, i + 1)
            try:
                header = json.loads("\n".join(body))
            except ValueError:
                header = None  # A maimed header (e.g. a crash mid-write) is reported, not fatal.
            continue
        if line.startswith("## "):
            current_heading = line[3:].strip()
            narrative_lines = []
            i += 1
            continue
        if line.rstrip() == f"```{_SECTION_FENCE_INFO}":
            body, i = _read_fence_body(lines, i + 1)
            try:
                data = json.loads("\n".join(body))
            except ValueError:
                data = {}  # A maimed trailing section (crash mid-append) is skipped, not fatal.
            if data:
                sections.append(
                    {"heading": current_heading, "narrative": flush_narrative(), "data": data}
                )
            narrative_lines = []
            continue
        if current_heading is not None:
            narrative_lines.append(line)
        i += 1

    return {"header": header, "sections": sections}


def read_ticket(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Parse a ticket file back into a dict. The round trip this module's
    "queryable for analysis" requirement rests on.

    ``outcome`` is always present and defaults to `{"outcome": "unknown",
    "by": None, "note": None, "recorded_at": None}` when no outcome section
    exists -- present, not absent, the same discipline
    `ledger.diagnoses()` applies to an unverdicted diagnosis, for the same
    reason: an `unknown` a caller has to remember to check for separately is
    an `unknown` that will eventually be read as a pass.
    """

    text = Path(path).read_text(encoding="utf-8")
    parsed = _parse_ticket_text(text)
    header = parsed["header"] or {}
    sections: list[dict[str, Any]] = parsed["sections"]

    def latest(kind: str) -> dict[str, Any] | None:
        matches = [s["data"] for s in sections if s["data"].get("kind") == kind]
        return matches[-1] if matches else None

    def all_of(kind: str) -> list[dict[str, Any]]:
        return [s["data"] for s in sections if s["data"].get("kind") == kind]

    outcome = latest(KIND_OUTCOME) or {
        "kind": KIND_OUTCOME, "outcome": UNKNOWN, "by": None, "note": None, "recorded_at": None,
    }

    return {
        "path": str(path),
        "header": header,
        "run_id": header.get("run_id"),
        "sections": sections,
        "question": latest(KIND_QUESTION),
        "intent": latest(KIND_INTENT),
        "timeline": all_of(KIND_TOOL_EVENT),
        "device_interactions": all_of(KIND_DEVICE_INTERACTION),
        "evidence": all_of(KIND_EVIDENCE_SOURCE),
        "context_footprint": latest(KIND_CONTEXT_FOOTPRINT),
        "answer": latest(KIND_ANSWER),
        "outcome": outcome,
        "closed": latest(KIND_CLOSED),
    }
