"""B-201/B-630 — the trigger-intake watcher: reads Loki, matches the
mnemonics.yaml trigger table, emits a routing decision, and STOPS.

What this module is not
------------------------
It does not run `investigate`. It does not open a ticket. It does not retry,
schedule itself, or hold state between calls. `event_routing.py`'s own
docstring states the shape this module inherits and does not depart from:
"It listens on nothing, calls nothing, retries nothing." Periodic invocation
(cron, a systemd timer, the same n8n/relay-container choice
`event_routing.py` already names) is the operator's infrastructure decision,
not this module's. **Auto-execution is a separate decision the operator has
not made** (B-201's brief, verbatim) — wiring this module's output into
`investigate` would change the failure mode of the whole system, from "a
human reads a dry-run surface" to "a false positive runs unattended", and
that line is not this change's to move.

What "the table" actually is
------------------------------
`mnemonics.yaml`'s `trigger` field (see that file's own header comment for
the full rationale) states, per mnemonic, whether seeing it via Loki
specifically should fire an investigation — a MUCH narrower question than
`investigate_with`, which only asks whether a flow conceptually applies. The
measured answer, as of the 2026-08-19 sweep this module's docstring and
`mnemonics.yaml` both cite: **zero mnemonics currently clear the bar** —
either because they fire in steady state (the operator's own SSHD example),
because the flow they would name needs a subject (a local interface) the
message text cannot supply, or because they were never observed reaching
Loki at all in the measured window. That is not a placeholder result; it is
what "derive the candidate list from what this fabric actually logs"
produces when applied honestly, the same shape OBS-183 already established
for three of four protocol tools. The mechanism below is built and tested
against a table that can, in principle, say `fires: true` — proven with a
synthetic table in `tests/test_event_watch.py`, since a mechanism that has
only ever been exercised by an empty result is not proven (OBS-181).

Collapse before deciding: the dedup/rate-limiting design
-----------------------------------------------------------
One root cause commonly produces many log lines. Measured on this fabric,
2026-08-19: a single LDP TCP-authentication misconfiguration between P1 and
PE1 produced 15 lines (13 `IP-TCP-3-NOAUTH` + 2 `IP-TCP-3-BADAUTH`) in one
17-minute window, all naming the same peer. `SECURITY-SSHD_SYSLOG_PRX-3-
ERR_GENERAL` produced 981 lines across 9 devices in 7 days from one source.
Emitting one routing decision per log line would mean an orchestrator sees
15 "investigate this" suggestions for one fault, or — if that mnemonic ever
fires — 981 for one steady-state condition. `_group_records` below collapses
every record sharing one **device + mnemonic + extracted subject** into a
single decision carrying `occurrence_count`, `first_seen` and `last_seen`,
before any trigger decision is made. This is the Loki-source analogue of
what `event_routing.py`'s own docstring says Alertmanager already does
natively for the alert path (grouping, dedup, inhibition, repeat-
suppression) — Loki has no such layer of its own, so it is implemented here,
in code, once, rather than left to whichever orchestrator reads the output.

What IS implemented (this change):
* Exact-duplicate suppression from pipeline redelivery — inherited for free
  from `logs_loki.run_named_query`, which already runs `log_window.dedupe`
  (keyed on device timestamp + mnemonic + text) before this module ever
  sees a record. B-206b measured the same event stored 1,346 times upstream.
* Same-root-cause collapse within one fetched window — records sharing
  device + mnemonic + extracted subject become one decision with a count
  and a first/last-seen span (device-clock strings, taken from Loki's own
  `direction=backward` ordering rather than re-parsed — see
  `_group_records`'s docstring for why re-parsing would be a second, worse
  bug).

What is NOT implemented, deliberately, and why:
* **Cross-invocation suppression** (don't re-fire for a subject already
  investigated N minutes ago). Every call here is stateless, by the same
  design choice `event_routing.py` already made — there is no store, no TTL
  policy, and no answer yet to "where does that state live" (a file, this
  operator's own choice of scheduler, a future ticket-lookup). Building one
  without that answer would be inventing infrastructure nobody asked for.
  The mitigation today is architectural, not a missing feature: this module
  is never auto-wired to `investigate`, so a human is the suppression layer
  until B-201 graduates past DEFERRED (BACKLOG.md; gated on B-426's fault-
  injection acceptance test, per the operator's own prior instruction on
  that item).
* **Cross-mnemonic fusion** (LINK-3-UPDOWN and its paired LINEPROTO-5-UPDOWN
  are, per `mnemonics.yaml`'s own severity_note, one physical event, not
  two — but they are two DIFFERENT mnemonics, and `_group_records` collapses
  repeats of one mnemonic, not known co-occurring pairs of two). The raw
  material for this already exists as prose (`severity_note` fields
  documenting exactly which mnemonics pair, and in what order) but turning
  that into an enforced fusion rule is untested, separate work; `mnemonics.
  yaml`'s ROUTING-BGP-5-NSR_STATE_CHANGE and PKT_INFRA-LINEPROTO-5-UPDOWN
  entries both name this gap explicitly rather than silently double-firing.
* **Cross-device correlation** (one interface flap producing a LINK-3-UPDOWN
  on both ends of a wire). Each device's window is fetched and grouped
  independently; nothing here compares across devices. `evidence-
  reduction.md`'s clock-skew concern (B-415) is exactly why this is not
  attempted casually — ordering unsynchronised clocks from two sources needs
  its own, reviewed mechanism, not a shortcut bolted onto a dry-run watcher.

Absence is not zero, here too
--------------------------------
`logs_loki.coverage_from_loki` is called for every fetched window and
carried on `WatchReport.coverage` verbatim — the same `Coverage.gaps()`
discipline `investigation.py` already applies to `show logging`, extended to
this module's own read. A window with no matching records looks identical,
by count alone, to a window Loki never answered; `WatchReport.status` and
`coverage["query_complete"]` are what tell the two apart, and a human
reading this module's dry-run output can see, per device, whether the
absence of a decision means "nothing happened" or "the query failed" or
"only severities 3/4 could have reached this source at all" (B-206a).

Never a path to a model
--------------------------
Nothing in this module builds a prompt, calls a model, or holds evidence
past the point of extracting a syntactically-validated subject from message
text — the same `event_routing.py` shape this module reuses rather than
reimplements. `RoutingDecision.suggested_command()` returns an argv **list**,
never a shell string, and this module never executes it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from . import event_routing, logs_loki
from . import trigger_table as _trigger_table
from .coverage import Coverage
from .event_routing import RoutingDecision
from .inventory_model import load_inventory_file
from .knowledge import load_mnemonic_table

__all__ = [
    "DEFAULT_WATCH_LIMIT",
    "DEFAULT_WATCH_WINDOW_SECONDS",
    "LokiObservation",
    "TriggerTableInconsistency",
    "WatchReport",
    "build_trigger_index",
    "validate_trigger_table",
    "watch_device",
    "watch_fabric",
]

#: A poll-style default: this module is meant to be invoked periodically
#: (the operator's scheduler, not this module's own loop — see the module
#: docstring), so the default lookback is short. 15 minutes. Override for an
#: ad hoc, longer-window human review (as the measurement behind
#: `mnemonics.yaml`'s own `trigger.measured` fields used, at 7 days — Loki's
#: own retention ceiling, `logs_loki.LOKI_QUERIES["logs_for_device"]`).
DEFAULT_WATCH_WINDOW_SECONDS = 900

#: Comfortably above what one 15-minute window needs even from the noisiest
#: measured device (PE2 at 35/hour would be ~9 in 15 minutes); still well
#: under `logs_loki`'s own ceiling of 1000.
DEFAULT_WATCH_LIMIT = 500


TriggerTableInconsistency = _trigger_table.TriggerTableInconsistency


# --------------------------------------------------------------------------- #
# The trigger index: mnemonics.yaml's `trigger` field, keyed for lookup.
# --------------------------------------------------------------------------- #


def build_trigger_index(
    table: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """``mnemonic -> its trigger block``, from ``mnemonics.yaml`` by default.

    ``table=`` is the injection seam a test uses to supply a synthetic
    table — the same idiom as `logs_loki`'s ``fetcher=`` and
    `network_tools`'s ``sender=`` — so a positive-control test can prove a
    mnemonic CAN fire without needing one to exist in the live, reviewed
    table today (OBS-181: a mechanism only ever exercised by refusals is not
    proven to accept anything).
    """

    entries = table if table is not None else load_mnemonic_table()
    return _trigger_table.build_trigger_index(entries)


def validate_trigger_table(table: Iterable[Mapping[str, Any]] | None = None) -> None:
    """Every ``fires: true`` entry must have a working route, or raise.

    "Working" means: `investigate_with` names a flow, AND that exact
    mnemonic has a matching `event_routing.MNEMONIC_FLOW_TABLE` entry (the
    thing that actually supplies a subject extractor). Checked eagerly —
    called once at the top of `watch_device`/`watch_fabric` — so a bad
    review lands as an immediate, loud failure the first time anything runs
    against it, not as a silently-always-false decision an operator could
    mistake for "the mechanism works and nothing happens to qualify".
    """

    entries = table if table is not None else load_mnemonic_table()
    _trigger_table.validate_trigger_table(
        entries,
        routable_mnemonics=(mnemonic for mnemonic, _flow, _extract in event_routing.MNEMONIC_FLOW_TABLE),
    )


# --------------------------------------------------------------------------- #
# Collapse: device + mnemonic + extracted subject -> one group.
# --------------------------------------------------------------------------- #


def _flow_and_extractor_for(
    mnemonic: str,
) -> tuple[str, Callable[[str], str | None]] | None:
    """The one lookup into `event_routing.MNEMONIC_FLOW_TABLE`, shared by
    every caller that needs either half — never two separate scans for the
    flow name and the extractor of the same mnemonic."""

    for table_mnemonic, flow, extract in event_routing.MNEMONIC_FLOW_TABLE:
        if table_mnemonic == mnemonic:
            return flow, extract
    return None


def _extractor_for(mnemonic: str) -> Callable[[str], str | None] | None:
    found = _flow_and_extractor_for(mnemonic)
    return found[1] if found else None


def _group_records(
    records: list[dict[str, Any]],
) -> dict[tuple[str, str | None], list[dict[str, Any]]]:
    """Collapse repeated log lines from one root cause into one group.

    Grouped by ``(mnemonic, subject)`` — never by mnemonic alone, so two
    different peers or interfaces sharing one mnemonic type stay distinct
    (they are different root causes), and never by raw line, so N
    redelivered lines about the SAME condition become one group, not N.
    ``subject`` is ``None`` when the mnemonic has no
    `event_routing.MNEMONIC_FLOW_TABLE` extractor, or when the extractor
    finds nothing in this particular line's text — both fold every such
    record for one mnemonic into a single "no subject" group rather than
    scattering them, which is exactly what the measured 13-line
    `IP-TCP-3-NOAUTH` burst needs (see the module docstring).

    Records with an empty ``mnemonic`` (the wire line did not match
    `logs_loki`'s own parser — `meta.unparsed_lines` already counts these)
    are skipped here; there is nothing to route for a line with no
    recognised mnemonic, and re-counting it would double-report a number
    `logs_loki` already reports once, honestly.

    Ordering within a group is **not re-sorted**. `logs_loki.run_named_query`
    requests ``direction="backward"`` (Loki's own newest-first ordering) and
    its `dedupe` step only filters, preserving that order — so
    ``group[0]`` is the most recent occurrence and ``group[-1]`` the oldest,
    for free. The alternative — parsing device-clock strings like
    ``"Aug 19 03:40:30.065 UTC"`` to sort by hand — has no year in it and
    would silently mis-order any window crossing a year boundary or, worse,
    sort lexicographically wrong for single- vs double-digit days (``"Aug 2"``
    sorts after ``"Aug 19"`` as a string). Trusting the source's own
    documented ordering avoids inventing a second, worse bug to prevent a
    first one that circumstances here don't even produce.
    """

    groups: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
    for record in records:
        mnemonic = record.get("mnemonic") or ""
        if not mnemonic:
            continue
        extract = _extractor_for(mnemonic)
        subject = extract(record.get("text", "")) if extract else None
        groups.setdefault((mnemonic, subject), []).append(record)
    return groups


# --------------------------------------------------------------------------- #
# Per-group decision.
# --------------------------------------------------------------------------- #


def _apply_trigger_policy(
    decision: RoutingDecision,
    trigger_index: Mapping[str, Mapping[str, Any]],
) -> RoutingDecision:
    """Apply reviewed Loki trigger policy after shared event routing."""

    mnemonic = decision.matched
    if mnemonic is None:
        return decision
    entry = trigger_index.get(mnemonic)

    if entry is None:
        return RoutingDecision(
            routable=False, source_kind="loki", matched=mnemonic, device=decision.device,
            reason=f"mnemonic {mnemonic!r} has no trigger entry in "
                   "mnemonics.yaml — unclassified, not cleared to trigger",
            transition=decision.transition, raw_event=decision.raw_event,
        )

    if not entry.get("fires"):
        return RoutingDecision(
            routable=False, source_kind="loki", matched=mnemonic, device=decision.device,
            reason=str(entry.get("reason") or "excluded by mnemonics.yaml's trigger table"),
            transition=decision.transition, raw_event=decision.raw_event,
        )
    if not decision.routable:
        return decision
    return RoutingDecision(
        routable=True, flow=decision.flow, device=decision.device, subject=decision.subject,
        source_kind="loki", matched=mnemonic, transition=decision.transition,
        reason=f"{mnemonic} routes to the {decision.flow} flow "
               "(cleared to trigger by mnemonics.yaml, observed via Loki)",
        raw_event=decision.raw_event,
    )


def _decision_for_group(
    records: list[dict[str, Any]],
    *,
    device: str,
    trigger_index: Mapping[str, Mapping[str, Any]],
) -> RoutingDecision:
    """Prefer a fault occurrence without treating a later recovery as one.

    A collapsed Loki group can contain a Down followed by its Up recovery.
    The former remains actionable; an all-recovery group is refused by the
    shared router. Records are newest-first, so this chooses the newest fault.
    """

    decisions = [event_routing.route_loki_record(record, device=device) for record in records]
    selected = next((decision for decision in decisions if decision.transition != "up"), decisions[0])
    return _apply_trigger_policy(selected, trigger_index)


# --------------------------------------------------------------------------- #
# Public shapes.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LokiObservation:
    """One collapsed group's verdict, plus the counting metadata the
    collapse itself produced. ``decision.routable`` is the only field an
    orchestrator needs to branch on; the rest is what a human reviewing the
    dry-run surface needs to trust that number."""

    decision: RoutingDecision
    occurrence_count: int
    #: Device-clock timestamp strings, most-recent and oldest in this
    #: window's group, respectively — see `_group_records`'s docstring for
    #: why these are carried verbatim rather than parsed.
    last_seen: str | None
    first_seen: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.decision.as_dict(),
            "occurrence_count": self.occurrence_count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


@dataclass(frozen=True)
class WatchReport:
    """One device's dry-run result. Always structured, never a traceback —
    a fetch failure is ``status="error"`` with ``observations`` empty, the
    same "never raise past the tool boundary" discipline `logs_loki.py`
    itself uses, so a caller (or `watch_fabric`, iterating nine of these)
    handles every device uniformly."""

    device: str
    status: str  # "success" | "error"
    coverage: dict[str, Any] | None
    observations: tuple[LokiObservation, ...] = field(default_factory=tuple)
    unparsed_lines: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)

    def routable_count(self) -> int:
        return sum(1 for o in self.observations if o.decision.routable)

    def as_dict(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "status": self.status,
            "coverage": self.coverage,
            "observations": [o.as_dict() for o in self.observations],
            "unparsed_lines": self.unparsed_lines,
            "routable_count": self.routable_count(),
            "errors": list(self.errors),
        }


# --------------------------------------------------------------------------- #
# The two entry points.
# --------------------------------------------------------------------------- #


def watch_device(
    device: str,
    *,
    since_seconds: int = DEFAULT_WATCH_WINDOW_SECONDS,
    limit: int = DEFAULT_WATCH_LIMIT,
    fetcher: Callable[[str, dict[str, str]], dict[str, Any]] | None = None,
    trigger_table: Iterable[Mapping[str, Any]] | None = None,
) -> WatchReport:
    """Fetch one device's recent Loki window, collapse, decide, stop.

    ``fetcher=``/``trigger_table=`` are the two injection seams: the first
    is `logs_loki.run_named_query`'s own (never a real HTTP call in a test);
    the second lets a test supply a synthetic trigger table so the routing
    mechanism itself can be proven to fire, independent of whether the live,
    reviewed table currently has any `fires: true` entries (it does not, as
    of 2026-08-19 — see the module docstring).

    Never raises for a fetch problem — that comes back as ``status="error"``,
    matching `logs_loki`'s own contract. **Does** raise
    `TriggerTableInconsistency` for a bad trigger table, deliberately (see
    that exception's docstring): a data review defect is not the same kind
    of failure as an unreachable Loki, and must not be swallowed the same way.
    """

    validate_trigger_table(trigger_table)
    trigger_index = build_trigger_index(trigger_table)

    result = logs_loki.run_named_query(
        "logs_for_device", device=device, since_seconds=since_seconds, limit=limit,
        fetcher=fetcher,
    )

    if result["status"] != logs_loki.STATUS_SUCCESS:
        return WatchReport(
            device=device, status="error", coverage=None,
            errors=tuple(result.get("errors") or ()),
        )

    parsed = result["data"]["parsed"]
    coverage: Coverage = logs_loki.coverage_from_loki(parsed, device)

    groups = _group_records(parsed["records"])
    observations = tuple(
        LokiObservation(
            decision=_decision_for_group(
                group_records,
                device=device,
                trigger_index=trigger_index,
            ),
            occurrence_count=len(group_records),
            last_seen=group_records[0].get("timestamp") or None,
            first_seen=group_records[-1].get("timestamp") or None,
        )
        for (mnemonic, subject), group_records in groups.items()
    )

    return WatchReport(
        device=device, status="success", coverage=coverage.as_dict(),
        observations=observations,
        unparsed_lines=int(parsed.get("meta", {}).get("unparsed_lines") or 0),
    )


def watch_fabric(
    devices: Iterable[str] | None = None,
    *,
    since_seconds: int = DEFAULT_WATCH_WINDOW_SECONDS,
    limit: int = DEFAULT_WATCH_LIMIT,
    fetcher: Callable[[str, dict[str, str]], dict[str, Any]] | None = None,
    trigger_table: Iterable[Mapping[str, Any]] | None = None,
) -> tuple[WatchReport, ...]:
    """`watch_device` over every inventory device, or a caller-given subset.

    ``devices=None`` reads every device `inventory_model.load_inventory_file`
    knows — the same source `event_routing._known_devices` uses — so a fresh
    device added to `inventory/lab.yaml` is watched without this module
    changing. Each device's own `TriggerTableInconsistency` risk is
    identical (it is the same table for all nine), so the validation cost is
    paid once, not per device — `watch_device`'s own internal call becomes a
    cheap no-op repeat rather than nine separate raises for one bad entry.
    """

    validate_trigger_table(trigger_table)
    names = list(devices) if devices is not None else [d.name for d in load_inventory_file().devices]
    return tuple(
        watch_device(
            name, since_seconds=since_seconds, limit=limit,
            fetcher=fetcher, trigger_table=trigger_table,
        )
        for name in names
    )


# --------------------------------------------------------------------------- #
# The dry-run surface. `python -m agent_nettools.event_watch` — never wired
# into `cli.py`. See the module docstring's "What this module is not".
# --------------------------------------------------------------------------- #


def _main(argv: list[str]) -> int:
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(
        prog="python -m agent_nettools.event_watch",
        description=(
            "Dry run only: fetch each device's recent Loki window, collapse "
            "repeated lines from one root cause, and print what WOULD route. "
            "Never runs `investigate`; a human reads this output."
        ),
    )
    parser.add_argument(
        "--device", action="append", dest="devices",
        help="repeatable; default is every inventory device",
    )
    parser.add_argument("--since-seconds", type=int, default=DEFAULT_WATCH_WINDOW_SECONDS)
    parser.add_argument("--limit", type=int, default=DEFAULT_WATCH_LIMIT)
    args = parser.parse_args(argv)

    reports = watch_fabric(
        devices=args.devices, since_seconds=args.since_seconds, limit=args.limit,
    )
    print(_json.dumps([r.as_dict() for r in reports], indent=2))

    if reports and all(r.status == "error" for r in reports):
        return 2
    return 0 if sum(r.routable_count() for r in reports) else 1


if __name__ == "__main__":  # pragma: no cover - exercised via _main() in tests
    import sys

    sys.exit(_main(sys.argv[1:]))
