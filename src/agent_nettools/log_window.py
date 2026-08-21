"""Shape a log window before a model ever sees it.

Pure functions over already-parsed `logging` records. No I/O, no device access,
no model.

Why this is code and not prompt instructions
---------------------------------------------
A raw `show logging last 200` window on this fabric is **68% the collector's
own SSH sessions**, and most of the remainder is `exec` session registration
noise from the same source. Measured on PE2's `broken` capture: 200 entries in,
**28** genuine network events out.

Leaving that reduction to the model was the alternative, and it is worse on
three counts, in increasing order of importance:

1. **Context budget.** 37,962 characters of window, of which ~34,000 are noise
   the model must read and discard on every single call.
2. **Testability.** "The model usually ignores SSH churn" is not a property
   anything can assert. :func:`drop_collector_noise` is.
3. **Determinism.** The same window must produce the same filtered set every
   time, or two runs of one investigation can correlate against different
   evidence and reach different conclusions. That is the reproducibility the
   whole deterministic-descent argument rests on, and handing it to a
   probabilistic step at the last moment would give it away for nothing.

The corollary matters too: because filtering is code, **what was dropped is
knowable**. :func:`shape_window` reports the counts, so a report can say "28 of
200 entries were network events" rather than silently presenting 28 and
implying that was all there was.

Attribution, not content matching
----------------------------------
`docs/design/evidence-reduction.md` §3 reduction 2 states the rule this module
now follows, and the first implementation of it did not:

    Filter by source, not by content. A rule that drops "SSH session
    disconnected" would also drop a genuine SSH problem on a device.

The original :data:`COLLECTOR_FACILITIES` dropped every record in two
facilities. That is a content rule wearing a provenance label: it deletes
`SECURITY-SSHD_SYSLOG_PRX` because SSH events are *usually* the collector here,
not because it established that any particular one was. Measured cost on PE2's
`broken` window: **eight** entries removed that no evidence attributes to this
tool, one of them

    Aug 16 07:41:32.006 UTC  sshd[202504]: process_output:
    ssh_packet_write_poll: Connection reset by peer

— an interactive session dying **22 seconds before** the interfaces went down.
On this fabric that was the capture script itself. In production the same line
is an operator's session dropping mid-change, which is exactly the kind of
thing a timeline is for. A noise filter that can silently delete the tool's own
damage is the wrong filter.

Every rule in :data:`COLLECTOR_NOISE` therefore matches on **who generated the
record**, and the two available forms of that are named in
:class:`Attribution`. A record whose provenance cannot be established is kept.
That is the conservative direction, it is the one that costs eight lines rather
than an incident, and :func:`ShapedWindow.unattributed_kept` reports it so the
cost stays visible instead of becoming folklore.

What this module does *not* do
-------------------------------
`evidence-reduction.md` §3 lists five reductions. This module implements 1
(partly — the window is bounded at the source by `show logging last <n>`, not
yet by a time range derived from the investigation), 2, and 5. It does **not**
implement 3 (template extraction) or 4 (aggregation), and that is deliberate
rather than pending:

* The mnemonic **is** the template key on IOS-XR — see the document's own
  worked example. Grouping is a `Counter` over `record["mnemonic"]`, not a
  clustering problem, so there is no algorithmic work being deferred.
* At 28 records the aggregate carries no information the records do not.
  Aggregation earns its place when a window is large enough that counts and
  rates are the evidence, which on this source it is not and on B-206's Loki
  source it will be.

Tracked as **B-414**, which generalises normalisation across devices and
sources. The one property that must survive that work is pinned here now:
:func:`shape_window` never removes a singleton, because a lone occurrence of a
critical event among thousands of routine ones is frequently the answer.

**Cross-device clock-skew detection lives in `clock_skew.py`, not here
(B-415).** This module orders and shapes one device's own window; whether two
*devices'* windows can be safely compared by timestamp at all is a different
question, over records this module has no reason to hold onto once shaped.
`clock_skew.assess_clock_skew` answers it from `logs_loki.py`-shaped records
(each device's own timestamp plus Loki's ingest time as a shared reference)
and is deliberately a sibling module rather than a new responsibility bolted
onto `shape_window` — see its own module docstring for how it relates to (and
does not duplicate) `epoch.py`'s `Coherence` (B-436), a differently-scoped
skew over a different clock.

A measured correction to OBS-014
---------------------------------
OBS-014 found one event stored **1,346 times** and concluded any count over
that corpus is fiction without deduplication. That is true of **Loki**, and it
is *not* true of the device's own buffer: deduplicating PE2's 200-entry window
removes exactly **zero** records. The duplication is introduced by the syslog
pipeline, not present at the source.

:func:`dedupe` is kept anyway, because B-206 will read the same records back
out of Loki where the duplication is real. It is a no-op on this source and
load-bearing on the next one, which is worth stating so nobody deletes it as
dead code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any

from .coverage import Coverage, severities_at_or_below

__all__ = [
    "COLLECTOR_NOISE",
    "COLLECTOR_SOURCES",
    "Attribution",
    "MnemonicAggregate",
    "NoiseRule",
    "ShapedWindow",
    "aggregate_by_mnemonic",
    "coverage_from_logging",
    "dedupe",
    "drop_collector_noise",
    "filter_to_subject",
    "parse_device_timestamp",
    "seconds_between",
    "shape_window",
]

_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


class Attribution(Enum):
    """How a :class:`NoiseRule` establishes that this tool caused a record.

    Both members are claims about *provenance* — who produced the line. Neither
    is a claim about what the line says is wrong, which is the distinction
    `evidence-reduction.md` §3 reduction 2 turns on and the reason a filter can
    be aggressive without being dangerous.
    """

    #: Every address the record names is a known management host. A session
    #: event from an address that is not one is somebody else's session.
    SOURCE_ADDRESS = "source-address"

    #: The record names the process and the session object that produced it.
    #: Used where the transport carries no address to attribute against.
    GENERATING_PROCESS = "generating-process"


#: Management hosts that reach these devices. Measured across every committed
#: `show logging` fixture: 2,723 SSH records name only `.1`, `.2`, `.5` and
#: `.6`. The lab devices themselves are `.11`–`.31`, so no record in the corpus
#: is device-to-device SSH — every one of them is something on the management
#: bridge connecting in.
#:
#: Declared as data rather than a subnet test on purpose. `172.20.250.0/24` is
#: also where the devices' own management interfaces live, so a subnet rule
#: would attribute a device-sourced session to the collector.
COLLECTOR_SOURCES: frozenset[str] = frozenset(
    {"172.20.250.1", "172.20.250.2", "172.20.250.5", "172.20.250.6"}
)


@dataclass(frozen=True)
class NoiseRule:
    """One declared, named, reviewable reason to drop a record.

    The same discipline as `template_parsers.IgnoreRule`, for the same reason:
    a regex that quietly swallows unrecognised lines defeats the mechanism it
    is supposed to implement. A rule states its facility, how it attributes the
    record, and why — and :meth:`describe` puts all three in the audit trail.
    """

    facility: str
    attribution: Attribution
    reason: str
    #: `SOURCE_ADDRESS`: the addresses that count as this tool's own.
    sources: frozenset[str] = frozenset()
    #: `GENERATING_PROCESS`: substrings that must *all* be present, together
    #: naming the process and the session object that emitted the record.
    markers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.attribution is Attribution.SOURCE_ADDRESS and not self.sources:
            raise ValueError(f"{self.facility}: source attribution with no sources")
        if self.attribution is Attribution.GENERATING_PROCESS and not self.markers:
            raise ValueError(f"{self.facility}: process attribution with no markers")

    def matches(self, record: dict[str, Any]) -> bool:
        """True when this record is provably this tool's own noise.

        False whenever provenance cannot be established — an unattributable
        record is kept, never dropped on the balance of probability.
        """

        if record.get("facility") != self.facility:
            return False
        text = record.get("text", "")
        if self.attribution is Attribution.SOURCE_ADDRESS:
            found = _IPV4.findall(text)
            # `all()` over an empty list is True, so the emptiness check is
            # load-bearing: a session event naming no address at all is exactly
            # the unattributable case.
            return bool(found) and all(address in self.sources for address in found)
        return all(marker in text for marker in self.markers)

    def describe(self) -> str:
        return f"{self.facility} ({self.attribution.value}): {self.reason}"


#: Records generated by this tool's own presence rather than by the network.
#:
#: Dropping these is not hiding evidence: no investigation of a BGP session has
#: ever been advanced by knowing that the collector logged in again. Dropping
#: their *unattributable* neighbours would be, which is why each rule has to
#: prove the attribution rather than assume it.
COLLECTOR_NOISE: tuple[NoiseRule, ...] = (
    NoiseRule(
        facility="SECURITY-SSHD_SYSLOG_PRX",
        attribution=Attribution.SOURCE_ADDRESS,
        reason=(
            "netmiko sessions opening and closing -- 1,187 of 1,219 lines "
            "fabric-wide (OBS-014). Dropped only when every address the record "
            "names is a management host, so a session failure from anywhere "
            "else survives"
        ),
        sources=COLLECTOR_SOURCES,
    ),
    NoiseRule(
        facility="SYSDB-SYSDB",
        attribution=Attribution.GENERATING_PROCESS,
        reason=(
            "`exec` re-registering a vty's sysdb path on every login -- 843 of "
            "843 such records fabric-wide name both the client and the vty, so "
            "the process that emitted them is stated rather than inferred"
        ),
        markers=("client 'exec'", "/vty/"),
    ),
)


@dataclass(frozen=True)
class ShapedWindow:
    """A filtered window, and an honest account of what was removed."""

    records: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    total_in: int = 0
    duplicates_removed: int = 0
    collector_noise_removed: int = 0
    unrelated_removed: int = 0
    #: Records in a noise rule's facility that the rule could not attribute,
    #: and therefore kept. Reported so the conservative choice stays visible.
    unattributed_kept: int = 0
    #: What the source was able to tell us. ``None`` when the caller shaped raw
    #: records without the parser's meta -- which is itself a gap, and
    #: ``grounding.check_absence_coverage`` treats it as one.
    coverage: Coverage | None = None

    @property
    def is_empty(self) -> bool:
        return not self.records

    def summary(self) -> str:
        """One line a report can cite about the window itself."""

        return (
            f"{len(self.records)} of {self.total_in} log entries retained "
            f"({self.collector_noise_removed} collector-generated, "
            f"{self.duplicates_removed} duplicate, "
            f"{self.unrelated_removed} unrelated to the subject; "
            f"{self.unattributed_kept} session events kept because their "
            f"source could not be established)"
        )


def dedupe(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop records identical in device timestamp, mnemonic and text.

    Keyed on the **device's own** timestamp, never an ingest timestamp: the
    same event re-delivered carries the same device clock reading and a
    different arrival time, so keying on arrival would preserve every copy.

    The mnemonic is in the key even though the text alone would nearly always
    separate two events, because `evidence-reduction.md` §7's template-collision
    failure mode is exactly two distinct event types merging into one. Two
    records with different mnemonics are different events by definition, and no
    reduction step here may decide otherwise.
    """

    seen: set[tuple[str, str, str]] = set()
    kept: list[dict[str, Any]] = []
    for record in records:
        key = (record.get("timestamp", ""), record.get("mnemonic", ""), record.get("text", ""))
        if key in seen:
            continue
        seen.add(key)
        kept.append(record)
    return kept


def drop_collector_noise(
    records: list[dict[str, Any]], *, rules: tuple[NoiseRule, ...] = COLLECTOR_NOISE
) -> tuple[list[dict[str, Any]], int]:
    """Remove entries a rule can attribute to this tool's own polling.

    Returns the kept records and the number of records that sit in a rule's
    facility but that the rule could **not** attribute. Those are kept, and the
    count is returned rather than logged, because "the filter declined to act"
    is a fact a report should be able to state.
    """

    facilities = {rule.facility for rule in rules}
    kept: list[dict[str, Any]] = []
    unattributed = 0
    for record in records:
        if any(rule.matches(record) for rule in rules):
            continue
        if record.get("facility") in facilities:
            unattributed += 1
        kept.append(record)
    return kept, unattributed


def filter_to_subject(records: list[dict[str, Any]], subject: str | None) -> list[dict[str, Any]]:
    """Keep entries that mention the subject, when a subject is given.

    Deliberately a plain substring match over the message text, and
    deliberately **not** applied by default. An interface shutdown that
    isolates a peer never names the peer's address, so filtering a
    `bgp_session` window to the peer address would discard the very events that
    explain it -- as measured on the `broken` label, where the causal events
    name `GigabitEthernet0/0/0/0` and `P1`, not `10.255.0.12`.

    `evidence-reduction.md` §3 reduction 5 projects to the subject on both
    evidence axes. That is right for configuration and wrong here: on the
    historical axis the projection is by **device and window**, never by
    subject identifier, because the events that explain a subject are the ones
    that do not name it.
    """

    if not subject:
        return list(records)
    return [r for r in records if subject in r.get("text", "")]


def coverage_from_logging(parsed: dict[str, Any], device: str) -> Coverage:
    """Build a coverage record from a parsed `show logging` result.

    Every field is read from what the device itself reported. The header states
    its own buffer level and how many messages that buffer holds, so

        Buffer logging: level debugging, 593 messages logged

    against 200 records returned is a *statement* that 393 were not retrieved,
    not an inference from "we asked for 200 and got 200".

    `buffer_level` is the right level to read, not `trap_level`. The buffer is
    what `show logging` returns; the trap level governs what is shipped to the
    collector, and on this fabric those differ -- informational (0-6) shipped
    against debugging (0-7) buffered. Reading the trap level here would
    understate the local source's coverage by exactly the severity class B-206a
    is about.
    """

    meta = parsed.get("meta") or {}
    available = meta.get("buffer_messages_logged")
    dropped = meta.get("messages_dropped")

    notes: list[str] = []
    if meta.get("syslog_enabled") is False:
        notes.append("syslog logging is disabled on this device")
    if not meta.get("buffer_level"):
        notes.append("the source did not report its buffer level")

    return Coverage(
        device=device,
        source="device_buffer",
        query_complete=True,
        window_start=meta.get("window_start"),
        window_end=meta.get("window_end"),
        severity_available=severities_at_or_below(meta.get("buffer_level")),
        records_available=int(available) if str(available).isdigit() else None,
        records_returned=len(parsed.get("records") or ()),
        records_dropped_at_source=int(dropped) if str(dropped).isdigit() else 0,
        notes=tuple(notes),
    )


def shape_window(
    records: list[dict[str, Any]],
    *,
    subject: str | None = None,
    drop_noise: bool = True,
    coverage: Coverage | None = None,
) -> ShapedWindow:
    """Dedupe, drop attributable collector noise, optionally narrow to a subject.

    Order matters: dedupe first so a duplicated noise line is counted once as
    noise rather than many times, and narrow to the subject last so the
    "unrelated" count means what it says.

    No step here has a minimum-count threshold. An event occurring exactly once
    in a window of thousands is the case `evidence-reduction.md` §7 calls
    over-aggregation, and it survives every reduction in this module by
    construction, not by tuning.
    """

    total = len(records)

    deduped = dedupe(records)
    duplicates = total - len(deduped)

    if drop_noise:
        kept, unattributed = drop_collector_noise(deduped)
    else:
        kept, unattributed = deduped, 0
    noise = len(deduped) - len(kept)

    narrowed = filter_to_subject(kept, subject)
    unrelated = len(kept) - len(narrowed)

    if coverage is not None:
        coverage = replace(coverage, records_kept_unattributable=unattributed)

    return ShapedWindow(
        records=tuple(narrowed),
        total_in=total,
        duplicates_removed=duplicates,
        collector_noise_removed=noise,
        unrelated_removed=unrelated,
        unattributed_kept=unattributed,
        coverage=coverage,
    )


# --------------------------------------------------------------------------- #
# Temporal shape -- B-418
# --------------------------------------------------------------------------- #
#
# `evidence-reduction.md` §3.4: "Two records with identical counts can mean
# opposite things: 60 events evenly spread over an hour is a chronic
# condition; 60 events in ninety seconds is an incident." A bare `count`
# cannot tell those apart. This section adds two numbers that can, alongside
# the count -- never in place of the records themselves, for the same reason
# `shape_window` does not aggregate away the 28 individual records it
# returns: B-414 measured that at this fabric's scale a replacement
# aggregate carries no information the records do not (CLOSED-AS-MEASURED,
# OBS-127). `aggregate_by_mnemonic` is additive summary, read alongside
# `ShapedWindow.records`, not instead of them.
#
# Blocked on B-414 in the backlog, unblocked by the same measurement that
# closed it: B-414 asked whether a *general*, cross-source normalisation
# capability was worth building today and measured that it was not. That
# answer is about the general capability, not about this narrower one --
# rate and burst shape are a `Counter` plus two small scans over a sorted
# list, not the template-normalisation machinery B-414 was scoped to.

_MONTH_NUMBER: dict[str, int] = {
    name: index
    for index, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        start=1,
    )
}

#: `Aug 16 07:41:54.688 UTC` -- the device's own embedded timestamp, exactly
#: as `template_parsers._LOG_ENTRY` captures it and exactly as :func:`dedupe`
#: above already keys on it. Deliberately permissive on the trailing token
#: (`UTC` here, but never validated) since that field is provenance, not part
#: of what this function measures.
_DEVICE_TIMESTAMP = re.compile(
    r"^(?P<mon>[A-Za-z]{3})\s+(?P<day>\d{1,2})\s+"
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})\.(?P<ms>\d{3})\s+\S+$"
)

#: One occurrence's parsed device timestamp, with no year -- `(month, day,
#: hour, minute, second, millisecond)`.
DeviceTimestamp = tuple[int, int, int, int, int, int]


def parse_device_timestamp(text: str) -> DeviceTimestamp | None:
    """A device timestamp string, or ``None`` when it does not parse.

    Deliberately not a `datetime`: the device's own string carries no year
    (`template_parsers.py`'s own comment explains why the field is kept
    verbatim rather than reformatted -- IOS-XR does not print one), and this
    module only ever needs an *interval* between two timestamps from the same
    window, which a year never answers anyway (:func:`seconds_between`).

    ``None`` covers both a malformed line and the empty string
    `logs_loki._record_from_line` uses for a Loki line its own regex could
    not parse. An unparseable timestamp cannot be placed in a chronology, so
    every caller here counts it rather than silently dropping it --
    :attr:`MnemonicAggregate.untimed` and `log_episodes.EpisodeSet.
    records_unordered` are where that count surfaces.
    """

    match = _DEVICE_TIMESTAMP.match(text.strip())
    if not match or match["mon"] not in _MONTH_NUMBER:
        return None
    return (
        _MONTH_NUMBER[match["mon"]],
        int(match["day"]),
        int(match["hour"]),
        int(match["minute"]),
        int(match["second"]),
        int(match["ms"]),
    )


def seconds_between(earlier: DeviceTimestamp, later: DeviceTimestamp) -> float:
    """Elapsed seconds from ``earlier`` to ``later``.

    Anchored on an arbitrary fixed leap year purely as a `datetime` scaffold
    for calendar arithmetic (month lengths, leap days) -- absolute calendar
    time is never asked for here, only the interval between two device
    timestamps read out of one window, which is a relative question a
    missing year never affects except at one boundary: a window spanning
    31 December into 1 January would otherwise read as a large *negative*
    gap, since "Jan" sorts before "Dec" in any one anchor year. When ``later``
    would land before ``earlier`` under the shared anchor, it is re-anchored
    one year forward instead of being returned as a negative interval -- the
    one defence this function has against that case, sufficient for a
    bounded device buffer whose true span is normally hours, not months.
    """

    anchor = 2000  # a leap year, so 29 Feb is always a valid anchor date too
    lo = datetime(anchor, earlier[0], earlier[1], earlier[2], earlier[3], earlier[4], earlier[5] * 1000)
    hi = datetime(anchor, later[0], later[1], later[2], later[3], later[4], later[5] * 1000)
    if hi < lo:
        hi = hi.replace(year=anchor + 1)
    return (hi - lo).total_seconds()


#: The fixed averaging window `max_rate_1m` names. A declared constant, not a
#: derived protocol timer -- this is the log-volume analogue of the interface
#: error-counter check already being a rate over a fixed window rather than a
#: raw total (the same analogy `evidence-reduction.md` §3.4 and the B-418
#: backlog row both draw), and is a different question from `log_episodes`'
#: cross-protocol proximity bound, which *is* derived from protocol timers.
#: This window asks "how many occurrences of the SAME mnemonic land within
#: any given minute", never "is event A close enough in time to be part of
#: event B's causal chain" -- that question belongs to `log_episodes.py` and
#: is answered from a different, protocol-timer-derived table.
_RATE_WINDOW_SECONDS = 60.0


def _max_rate_1m(offsets: list[float]) -> int:
    """Peak count of occurrences within any rolling 60-second window.

    Two-pointer scan over ``offsets`` (already sorted ascending, seconds
    relative to the group's first occurrence): O(n), no window is
    constructed explicitly.
    """

    best = 0
    left = 0
    for right, value in enumerate(offsets):
        while value - offsets[left] > _RATE_WINDOW_SECONDS:
            left += 1
        best = max(best, right - left + 1)
    return best


def _bursts(offsets: list[float]) -> int:
    """Count of maximal runs of 2+ occurrences with each consecutive gap
    strictly under the 60-second window.

    A run of length 1 is an isolated occurrence, not a burst. A run whose
    members are each spaced exactly at the window boundary or wider is the
    "60 events evenly spread over an hour" chronic case the design note
    names, and correctly scores 0 -- `gap < window`, not `<=`, is what keeps
    steady-state repetition from reading as a burst.
    """

    if len(offsets) < 2:
        return 0
    runs = 0
    in_run = False
    for i in range(1, len(offsets)):
        if offsets[i] - offsets[i - 1] < _RATE_WINDOW_SECONDS:
            if not in_run:
                runs += 1
                in_run = True
        else:
            in_run = False
    return runs


@dataclass(frozen=True)
class MnemonicAggregate:
    """One mnemonic's temporal shape across a window. B-418.

    Read *alongside* :attr:`ShapedWindow.records`, never instead of them --
    see this section's header comment for why a replacement aggregate was
    refused at this fabric's scale (B-414).

    Every timing field is ``None``, never ``0``/``0.0``, when it was not
    measured rather than measured as zero -- the same "absence is never
    zero" discipline `coverage.Coverage` applies to a whole window, applied
    here at the per-mnemonic grain. A mnemonic seen once has no interval to
    compute a rate over; ``rate_per_hour: 0.0`` would assert "this never
    happens", which one observation cannot support. :attr:`untimed` is the
    honest account of occurrences excluded from every timing field because
    their own device timestamp did not parse -- they still count towards
    :attr:`count`, because they did happen, but they cannot be dated.
    """

    mnemonic: str
    #: Every occurrence, timed or not.
    count: int
    #: Verbatim device timestamp of the earliest *timed* occurrence, or ``""``.
    first_seen: str
    #: Verbatim device timestamp of the latest *timed* occurrence, or ``""``.
    last_seen: str
    #: One verbatim sample -- the most recent occurrence's `text`. The
    #: receipt an engineer checks and the grounding check can cite
    #: (`evidence-reduction.md` §8), never a summary invented from the group.
    sample: str
    #: Seconds from `first_seen` to `last_seen`. ``None`` when fewer than two
    #: occurrences were timed -- there is no interval to report, not a
    #: zero-length one.
    window_seconds: float | None
    #: ``count / (window_seconds / 3600)``. ``None`` whenever `window_seconds`
    #: is ``None`` or ``0`` -- a single instant has no rate.
    rate_per_hour: float | None
    #: Peak occurrences of this mnemonic within any rolling 60-second window.
    #: ``None`` only when nothing was timed at all.
    max_rate_1m: int | None
    #: Count of distinct flurries (see :func:`_bursts`). ``None`` only when
    #: nothing was timed at all; ``0`` is a real, measured "no bursts".
    bursts: int | None
    #: Occurrences whose own device timestamp did not parse -- excluded from
    #: every field above, and reported rather than silently folded in or
    #: dropped.
    untimed: int = 0


def aggregate_by_mnemonic(records: list[dict[str, Any]]) -> tuple[MnemonicAggregate, ...]:
    """Per-mnemonic count, rate and burst shape over ``records``. B-418.

    Takes any record list in this module's own shape -- typically
    `ShapedWindow.records`, already denoised -- and neither filters nor
    reorders anything; it only summarises what it is given. The returned
    tuple is ordered by first appearance in ``records``, so it is stable and
    reflects the window rather than an alphabetisation choice, and it holds
    exactly one entry per distinct mnemonic present -- no minimum-count
    threshold, matching :func:`shape_window`'s own rule that a singleton is
    never the one thing a reduction drops.

    Grouped on the mnemonic alone, not `(mnemonic, normalised body)` --
    `evidence-reduction.md` §3.3 already establishes the mnemonic is the
    template key on this fabric, and B-414 measured a finer key added
    nothing at this source's scale. A source that needs body normalisation
    too is B-414's still-open general case, not a reason to complicate this
    narrower one.
    """

    order: list[str] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        mnemonic = record.get("mnemonic", "")
        if mnemonic not in grouped:
            grouped[mnemonic] = []
            order.append(mnemonic)
        grouped[mnemonic].append(record)

    aggregates: list[MnemonicAggregate] = []
    for mnemonic in order:
        group = grouped[mnemonic]
        timed: list[tuple[DeviceTimestamp, dict[str, Any]]] = []
        untimed = 0
        for record in group:
            parsed = parse_device_timestamp(record.get("timestamp", ""))
            if parsed is None:
                untimed += 1
            else:
                timed.append((parsed, record))
        timed.sort(key=lambda pair: pair[0])

        if not timed:
            aggregates.append(
                MnemonicAggregate(
                    mnemonic=mnemonic,
                    count=len(group),
                    first_seen="",
                    last_seen="",
                    sample=group[-1].get("text", ""),
                    window_seconds=None,
                    rate_per_hour=None,
                    max_rate_1m=None,
                    bursts=None,
                    untimed=untimed,
                )
            )
            continue

        anchor = timed[0][0]
        offsets = [seconds_between(anchor, parsed) for parsed, _ in timed]
        # `None`, not `0.0`, for exactly one timed occurrence -- there is no
        # interval to report. Two-or-more occurrences sharing one millisecond
        # timestamp are a real, *measured* zero-length window and keep the
        # `0.0` they compute -- the distinction is "not measured" vs.
        # "measured, and it was zero", not "small vs. smaller".
        window_seconds = (offsets[-1] - offsets[0]) if len(offsets) > 1 else None
        rate_per_hour = (
            len(timed) / (window_seconds / 3600.0)
            if window_seconds is not None and window_seconds > 0
            else None
        )

        aggregates.append(
            MnemonicAggregate(
                mnemonic=mnemonic,
                count=len(group),
                first_seen=timed[0][1].get("timestamp", ""),
                last_seen=timed[-1][1].get("timestamp", ""),
                sample=timed[-1][1].get("text", ""),
                window_seconds=window_seconds,
                rate_per_hour=rate_per_hour,
                max_rate_1m=_max_rate_1m(offsets),
                bursts=_bursts(offsets),
                untimed=untimed,
            )
        )
    return tuple(aggregates)
