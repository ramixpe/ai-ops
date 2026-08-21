"""Event episodes -- deterministic sequence construction. B-416.

Mechanism, not rendering
-------------------------
OBS-078 upgraded this from presentation to mechanism: two independent faults
rarely land in the same instant, so **a second configuration commit in the
window, unexplained by the localised cause**, is the timeline signature of a
masked second fault. The rung tables of a one-fault and a two-fault incident
are byte-identical (D6, `chaos-harness.md` §7) -- the descent cannot see this,
so the timeline is one of only two places the signal can come from, and this
module is what turns "a pile of records" into "a timeline a masked second
fault would show up in".

An **episode** is a deterministically constructed, time-bounded sequence of
events on one device, ordered by the device's own clock. Independent
aggregation (`log_window.aggregate_by_mnemonic`, B-418) destroys exactly the
thing an episode preserves: six events aggregated separately become six
records with `count: 1`, all true, with the causal ordering gone. The two
reductions answer different questions -- aggregation answers *how often*,
episodes answer *in what order* -- and a report wants both, not one instead
of the other.

The log-side mirror of the dependency descent
------------------------------------------------
`evidence-reduction.md` §6 states the correspondence this fabric measures
rather than merely illustrates. The descent walks *down* the protocol stack
asking "is this layer healthy"; an episode is that same stack observed
propagating *upward* in time:

    bgp_session broken        <- ROUTING-BGP-5-ADJCHANGE
    transport broken          <- (no log event -- state only)
    route_to_peer broken      <- (no log event -- state only)
    igp_adjacency broken      <- ROUTING-ISIS-5-ADJCHANGE
    interface broken (cause)  <- PKT_INFRA-LINK-5-CHANGED

Two independent views of one dependency graph, one from state and one from
history, agreeing on which layer is the cause -- corroboration neither axis
produces alone. **The correspondence is deliberately partial**: two of the
five rungs have no log event at all, which is why an episode is evidence
*for* a descent and never a substitute for one. Nothing in this module
asserts otherwise, and nothing here claims coverage of a rung it did not see
a record for.

The measured design constraint this module exists to honour
----------------------------------------------------------------
In the captured `broken` window (`tests/fixtures/cisco_xr/PE2/broken/
show-logging-last-200.txt`), the gap between the interface event and the BGP
event is **~153.4 seconds** (rounds to the 154s the design doc and
`mnemonics.yaml`'s own `ROUTING-ISIS-5-ADJCHANGE` entry cite: "On this
fabric BGP follows within its hold timer when the route is lost -- measured
154s in round 1") -- BGP's hold timer expiring, not processing delay. A proximity threshold of "a few
seconds" -- any human intuition about "at the same time" -- splits that
sequence into two episodes and severs exactly the link this module exists to
preserve. So the bound used to join two adjacent events must be **derived
from a declared protocol timer**, never guessed, and the bound actually used
for each join is recorded on the :class:`Episode` it produced -- see
:data:`PROTOCOL_HOLD_TIMERS` and :class:`EpisodeJoin`.

Per-pair, not one global bound
--------------------------------
The bound is computed **per adjacent pair**, not once for the whole window.
A single global "largest timer anywhere in this window" bound would also be
enough to bridge the measured 154s gap (a BGP event is present, so the
global bound would be 180s), but it would just as readily bridge a large gap
between two *unrelated* IS-IS-only events elsewhere in the same window,
merely because a BGP event happens to sit somewhere else in it -- widening
the wrong join for a reason that has nothing to do with either event. A
per-pair bound -- the larger of the two adjacent events' own declared
protocol timers, defaulting to the smallest declared timer in the table when
neither event names a known protocol -- reaches exactly as far as the
measured chain needs (the config-commit -> BGP-ADJCHANGE join uses BGP's
180s) without reaching further than the pair in front of it justifies.

What this module does not do
-------------------------------
* **No topology resolution.** `subject` on an :class:`Episode` is a
  best-effort display hint (the first interface name any member event
  happens to mention), never a resolved topology object -- that is B-417,
  a separate, un-coupled backlog item.
* **No cross-device correlation.** One call is scoped to one device's
  records, matching `log_window.shape_window`'s own per-device scope and the
  measured six-event episode itself, which is entirely on PE2. Correlating
  episodes *across* devices needs clock-skew handling first (B-415, blocked
  on B-206) -- ordering two devices' events without it is exactly the
  silently-wrong-ordering failure `evidence-reduction.md` §7 describes for a
  different axis.
* **No wiring into `investigation.py`, `descent.py` or the correlate
  prompt.** Per the campaign's own rule 4: track B builds episodes for
  correlation quality on their own merit; whether the descent (B-428,
  already shipped) ever consumes them is a separate, later decision made by
  whoever owns that file. Coupling the two here would put two agents in one
  contract, which is the exact failure that rule exists to prevent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .coverage import Coverage
from .log_window import DeviceTimestamp, parse_device_timestamp, seconds_between

__all__ = [
    "PROTOCOL_HOLD_TIMERS",
    "Episode",
    "EpisodeJoin",
    "EpisodeSet",
    "ProtocolTimer",
    "build_episodes",
]


@dataclass(frozen=True)
class ProtocolTimer:
    """One declared, cited reason a protocol's own detection window is what
    it is. The same discipline as `log_window.NoiseRule` and
    `template_parsers.IgnoreRule`: a number with no citation is an intuition
    wearing a constant's clothing."""

    protocol: str
    #: Exact value of a record's `facility` field this timer applies to --
    #: `template_parsers.py`'s own `mnemonic.rsplit("-", 2)` split, e.g.
    #: `"ROUTING-BGP"` from `ROUTING-BGP-5-ADJCHANGE`.
    facility: str
    hold_seconds: int
    citation: str


#: **Only the two protocols the B-416 backlog row names explicitly, each with
#: a measured or documented citation.** Adding a third protocol here means
#: adding a citation for it, not copying a vendor default -- this build's own
#: discipline is that a specification is not a measurement (six design
#: decisions in this project were corrected by measurement against captured
#: reality and none by review, `MVP0-REVIEW.md` §3). LDP is deliberately
#: absent: no LDP adjacency-change mnemonic has been observed in this
#: fabric's captured fixtures, so there is nothing to measure a timer
#: against yet, and inventing one from a vendor default would be exactly the
#: mistake this rule exists to prevent. An LDP-facility record therefore
#: falls back to the smallest declared timer below (see `_pair_bound`) until
#: a real one is measured.
PROTOCOL_HOLD_TIMERS: tuple[ProtocolTimer, ...] = (
    ProtocolTimer(
        protocol="bgp",
        facility="ROUTING-BGP",
        hold_seconds=180,
        citation=(
            "BGP hold timer -- configured 180s on all 28 sessions measured "
            "fabric-wide (metrics_prometheus.py's peer_holdtime measurement, "
            "2026-08-19: 'the CONFIGURED hold timer (180s on every one of "
            "this fabric's 28 sessions, never varying)'). The 154s "
            "interface-to-BGP gap this timer exists to bridge "
            "(mnemonics.yaml's ROUTING-ISIS-5-ADJCHANGE severity_note: "
            "'On this fabric BGP follows within its hold timer when the "
            "route is lost -- measured 154s in round 1') is inside it with "
            "~26s to spare."
        ),
    ),
    ProtocolTimer(
        protocol="isis",
        facility="ROUTING-ISIS",
        hold_seconds=30,
        citation=(
            "IS-IS hold time -- three missed hellos at the default 10s "
            "hello interval (mnemonics.yaml's ROUTING-ISIS-5-ADJCHANGE "
            "entry: 'hold time expired (~30s of lost hellos)')."
        ),
    ),
)

_TIMER_BY_FACILITY: dict[str, ProtocolTimer] = {t.facility: t for t in PROTOCOL_HOLD_TIMERS}


def _timer_for(record: dict[str, Any]) -> ProtocolTimer | None:
    return _TIMER_BY_FACILITY.get(record.get("facility"))


def _pair_bound(prev_record: dict[str, Any], next_record: dict[str, Any]) -> tuple[int, str]:
    """The proximity bound for one adjacent pair, and why.

    The larger of the two events' own declared protocol timers -- the
    downstream event's detection window is usually the one that matters (an
    IS-IS event followed by a BGP one needs BGP's 180s, not IS-IS's 30s),
    but taking the max of *both* sides also covers the symmetric case
    without needing to know which side is "downstream" from mnemonic alone.
    Falls back to the smallest declared timer in :data:`PROTOCOL_HOLD_TIMERS`
    -- never an invented number -- when neither event names a protocol this
    table knows about.
    """

    candidates = [t for t in (_timer_for(prev_record), _timer_for(next_record)) if t is not None]
    if not candidates:
        fallback = min(PROTOCOL_HOLD_TIMERS, key=lambda t: t.hold_seconds)
        return fallback.hold_seconds, (
            f"neither event named a protocol with a declared hold timer; "
            f"used the smallest declared timer ({fallback.protocol}, "
            f"{fallback.hold_seconds}s) as a floor rather than an invented value"
        )
    chosen = max(candidates, key=lambda t: t.hold_seconds)
    return chosen.hold_seconds, f"{chosen.hold_seconds}s ({chosen.protocol} hold timer) -- {chosen.citation}"


#: A best-effort display hint only -- see the module docstring's "what this
#: module does not do". Mirrors `event_routing._INTERFACE`'s charset for
#: consistency, but is intentionally not imported from there: that pattern is
#: part of a routing decision's validated-subject contract, and this is a
#: display convenience with no such guarantee.
_INTERFACE_MENTION = re.compile(r"Interface\s+([A-Za-z][A-Za-z0-9_./-]{0,62}),")


def _subject_hint(records: tuple[dict[str, Any], ...]) -> str | None:
    for record in records:
        match = _INTERFACE_MENTION.search(record.get("text", ""))
        if match:
            return match.group(1)
    return None


@dataclass(frozen=True)
class EpisodeJoin:
    """Why two adjacent records were placed in the same episode.

    One of these per internal gap in an :class:`Episode` -- `len(joins) ==
    len(records) - 1`. Kept on the episode itself (rather than only on the
    module-level table) so a reader or a future grounding check can audit
    the actual decision without recomputing it: "the bound used must be
    recorded", per the B-416 backlog row, means recorded in the output, not
    just derivable from it.
    """

    gap_seconds: float
    bound_seconds: int
    reason: str


@dataclass(frozen=True)
class Episode:
    """One time-bounded, ordered sequence of events on one device."""

    episode_id: str
    device: str
    #: Best-effort display hint -- see the module docstring. Never a resolved
    #: topology object; may be ``None``.
    subject: str | None
    #: Verbatim device timestamp of the first and last member record.
    start: str
    end: str
    #: Chronological member records, unmodified from the caller's input.
    records: tuple[dict[str, Any], ...]
    #: `len(joins) == len(records) - 1`; empty for a singleton episode.
    joins: tuple[EpisodeJoin, ...] = field(default_factory=tuple)

    @property
    def mnemonics(self) -> tuple[str, ...]:
        return tuple(r.get("mnemonic", "") for r in self.records)

    @property
    def is_singleton(self) -> bool:
        return len(self.records) == 1


@dataclass(frozen=True)
class EpisodeSet:
    """The result of one :func:`build_episodes` call, with an honest account
    of what could and could not be placed in a chronology.

    :attr:`coverage` is a straight passthrough of whatever the caller's
    `log_window.ShapedWindow.coverage` was -- ``None`` there already means
    "not measured" (`ShapedWindow`'s own docstring), and this module adds no
    second, competing way to say the same thing. Reading `episodes == ()` as
    "measured, found none" is only sound when :attr:`coverage` is not
    ``None`` *and* :attr:`records_considered` is not ``0`` for a reason other
    than an empty source read -- exactly the same discipline
    `check_absence_coverage` already applies to a whole window, at the
    episode grain.
    """

    episodes: tuple[Episode, ...]
    device: str
    #: `len(records)` as passed in -- distinguishes "0 episodes because 0
    #: records were even shaped" from "0 despite N shaped records" (every
    #: one was an isolated singleton episode more than its own bound apart,
    #: a real and different finding).
    records_considered: int
    #: Records whose own device timestamp did not parse, and which
    #: therefore could not be placed in any episode. Reported rather than
    #: silently dropped -- an `EpisodeSet` that quietly excluded these would
    #: read as complete when it is not.
    records_unordered: int
    coverage: Coverage | None = None

    @property
    def evaluated(self) -> bool:
        """Whether this window was actually measured (see the class
        docstring). ``False`` means "not evaluated"; only ``True`` licenses
        reading an empty :attr:`episodes` as "evaluated, found none"."""

        return self.coverage is not None

    def summary(self) -> str:
        singles = sum(1 for e in self.episodes if e.is_singleton)
        return (
            f"{len(self.episodes)} episode(s) over {self.records_considered} "
            f"record(s) on {self.device} ({singles} singleton, "
            f"{self.records_unordered} unordered -- timestamp did not parse)"
        )


def _finish_episode(
    members: list[tuple[DeviceTimestamp, dict[str, Any]]],
    joins: list[EpisodeJoin],
    device: str,
    index: int,
) -> Episode:
    records = tuple(record for _, record in members)
    return Episode(
        episode_id=f"EP-{index:04d}",
        device=device,
        subject=_subject_hint(records),
        start=records[0].get("timestamp", ""),
        end=records[-1].get("timestamp", ""),
        records=records,
        joins=tuple(joins),
    )


def build_episodes(
    records: list[dict[str, Any]],
    *,
    device: str,
    coverage: Coverage | None = None,
) -> EpisodeSet:
    """Group ``records`` (one device's shaped window) into chronological
    episodes.

    ``records`` is typically `log_window.ShapedWindow.records` for one
    device -- already denoised, never re-filtered here. Records are sorted
    by the device's own embedded timestamp (never by input order, never by
    an ingest time -- `evidence-reduction.md` §8's timestamp discipline);
    two adjacent records join the same episode when the gap between them is
    within the pair's own protocol-timer-derived bound
    (:func:`_pair_bound`), and a new episode starts otherwise. No
    minimum-count threshold: an isolated event is a real, singleton episode,
    not something this function discards -- the same rule
    `log_window.shape_window` already applies to a lone occurrence among
    thousands.

    A record whose own device timestamp does not parse cannot be placed in
    any episode; it is counted (`EpisodeSet.records_unordered`), never
    silently skipped.
    """

    timed: list[tuple[DeviceTimestamp, dict[str, Any]]] = []
    unordered = 0
    for record in records:
        parsed = parse_device_timestamp(record.get("timestamp", ""))
        if parsed is None:
            unordered += 1
        else:
            timed.append((parsed, record))
    timed.sort(key=lambda pair: pair[0])

    episodes: list[Episode] = []
    current: list[tuple[DeviceTimestamp, dict[str, Any]]] = []
    current_joins: list[EpisodeJoin] = []
    for parsed, record in timed:
        if current:
            prev_parsed, prev_record = current[-1]
            gap = seconds_between(prev_parsed, parsed)
            bound, reason = _pair_bound(prev_record, record)
            if gap <= bound:
                current_joins.append(
                    EpisodeJoin(gap_seconds=gap, bound_seconds=bound, reason=reason)
                )
            else:
                episodes.append(
                    _finish_episode(current, current_joins, device, len(episodes) + 1)
                )
                current = []
                current_joins = []
        current.append((parsed, record))
    if current:
        episodes.append(_finish_episode(current, current_joins, device, len(episodes) + 1))

    return EpisodeSet(
        episodes=tuple(episodes),
        device=device,
        records_considered=len(records),
        records_unordered=unordered,
        coverage=coverage,
    )
