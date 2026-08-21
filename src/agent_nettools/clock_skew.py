"""Cross-device clock-skew detection. B-415.

`evidence-reduction.md` §4/§8: multi-device correlation orders events by
**the device's own timestamp**, never ingest time -- correlating against
ingest time "produces a timeline that is confidently wrong and that nothing
downstream detects." That rule is right, and it has a silent failure mode of
its own: if two devices' clocks disagree, ordering by each one's own,
internally-consistent timestamp produces a merged timeline that is *also*
internally consistent and *also* wrong, with nothing downstream able to tell.
§12 names this failure mode 3, "Clock skew," and until now it was filed
**untestable** for an honest reason -- every window this layer read was
single-device, so there was no second clock to disagree with the first.

There is now a second clock. All nine devices report to Loki
(`logs_loki.py`), and every record Loki returns carries two independent
timestamps: the device's own embedded clock (`timestamp`, inside the message
body, syslog-ng never touches it) and Loki's own ingest time
(`ingest_timestamp_ns`, stamped by syslog-ng's `timestamp("current")` at
arrival -- see `logs_loki._record_from_line`'s own comment). Comparing a
device's *own* timestamp against that shared reference, one device at a time,
and then comparing the *devices* against each other through it, is what turns
"is there a second clock" into "do the clocks agree" -- which is what this
module answers.

**Measured before this was buildable, not assumed.** `docs/build/BACKLOG.md`
B-415, measured 2026-08-20 against this lab: comparing each device's own
embedded timestamp against Loki ingest across 600 entries, the **per-device
medians span 1.0s** (-5.0s to -4.0s), with individual per-line samples
ranging -11s to 0s -- pipeline jitter, not clock disagreement, which is
exactly why this module compares **per-device medians**, never raw samples
(see "Why medians, not samples" below). A second, independent spot-check
during this build (live, 2026-08-21, 24h/9-device pull) landed in the same
family: the four best-sampled devices (PE1/PE2/PE3/RR1, 29-143 records each)
agreed to within 0.7s; the three sparsest (P1/P4/PE4, 7-8 records each in
24h) swung the *naive* all-device span out to 8.4s purely from small-sample
noise on a bimodal per-line delivery-delay distribution (most lines arrive
within a second of their device timestamp; a minority arrive ~7-9s late,
plausibly a syslog-ng flush interval -- see :attr:`DeviceClockOffset.
comparable_records`'s own docstring). Neither number is a device clock
disagreeing with another; both are the same pipeline jitter B-415's own
backlog row already named ("individual samples -11s to 0s"). That spot-check
is not re-run by the test suite (design constraint: no live Loki required to
test -- see "Testing" below) and is not a claim this module verifies on every
run; it is why the module reports `comparable_records` per device rather than
a single fabric-wide number, so a reader can see when a span is riding on a
handful of samples before trusting it.

Why medians, not samples
-------------------------
A single log line's ingest delay is not a clock reading -- it is transit time
plus whatever the syslog-ng pipeline did with it before writing it to Loki
(batching, retries, queueing). The measured -11s-to-0s per-line spread above
is that jitter, not nine disagreeing clocks. A **device's** clock offset is
the thing that should be stable across many lines from that device, so this
module reduces each device's whole comparable sample to one number --
`statistics.median`, robust to the occasional badly-delayed line a mean would
not be -- before ever comparing one device to another. Comparing raw samples
directly (device A's line at 07:00:01 against device B's line at 07:00:04)
would conflate "these two lines took different paths through the collector"
with "these two clocks disagree," which is exactly the confusion the median
step exists to remove.

Relationship to `epoch.py`'s `Coherence` (B-436) -- related, not duplicated
------------------------------------------------------------------------------
`epoch.py` already has a careful, named distinction between **skew** and
**coherence**, and this module deliberately does not reinvent it, extend it,
or borrow its four-way vocabulary (`COHERENT`/`FABRIC_MOVED`/
`WINDOW_LIMITED`/`UNVERIFIED`). The two modules answer different questions
about a different kind of clock:

* `epoch.EvidenceEpoch.skew_seconds` is `closed - opened` on **our own**
  monotonic clock (`time.monotonic()`) -- how wide the window was in which
  *this tool's* collection ran, regardless of what any device's clock says.
  It answers "how long were we not looking," and `epoch.Coherence` answers
  "did a re-read prove nothing moved during that window" -- the skew there
  is a precondition on trusting a re-read, never itself the check (see that
  module's own docstring, "Skew does not establish coherence").
* This module's skew is a property of **the devices' own clocks**, compared
  to each other through a shared external reference (Loki's ingest time),
  independent of when or how fast *we* collected anything. It answers "can
  two devices' own timestamps be compared to order events between them at
  all" -- a precondition on correlation, not on a re-read.

Put differently: `epoch.py` asks whether *our observation* of one device (or
several, gathered in one pass) still describes one instant by the time we
finished looking. This module asks whether *the devices' own notion of when
"now" is* agrees closely enough that comparing their timestamps means
anything. A descent could be perfectly `coherent` (nothing moved during
collection) while correlating two of its devices' *log* timelines by
timestamp is still unsound, if those two devices' clocks disagree -- the two
findings are independent and a caller needing both must consult both. Naming
follows from that: `Coherence`/`coherent`/`caveat` stay `epoch.py`'s; this
module uses its own vocabulary below (`ClockSkewFinding`,
`not_evaluated`/`within_bound`/`skew_detected`) rather than overloading
those names for a different question.

Absence is never zero (design constraint, and the repeated defect class here)
--------------------------------------------------------------------------------
"No skew detected" is the answer this module will give most of the time on a
healthy fabric, which is exactly the condition under which a detector that
silently abstains from everything is hardest to catch. Two levels of
"nothing was measured" are kept distinct from "measured, and it was zero"
throughout:

* **Per device** (:class:`DeviceClockOffset`): `median_offset_seconds` is
  `None` when `comparable_records` is 0 -- no record on that device had both
  a parseable device timestamp and a parseable ingest timestamp -- never a
  `0.0` that would read as "this device's clock exactly matches the
  reference." A device with exactly one comparable record has a real,
  measured `0.0` if and only if that one offset actually computed to zero.
* **Fabric-wide** (:class:`ClockSkewFinding`): `span_seconds` is `None`
  whenever fewer than two devices were evaluated -- the retired blocker's own
  condition, "no two clocks to disagree" -- never a `0.0` that would read as
  "every device agrees." `status` is `not_evaluated` in that case, a third
  value never collapsed into `within_bound`.

This is the same discipline `coverage.Coverage`, `log_window.MnemonicAggregate`
and `epoch.Coherence` already apply to their own domains, restated once more
here because it is this codebase's most-repeated defect class, not because
this module found a new way to need it.

What this module does not do
-------------------------------
* **It never reorders or corrects a timestamp.** There is no function here
  that returns records in a new order or with an adjusted clock value --
  only offsets, medians and a finding. A caller who wants "one ordered
  timeline across N devices" (`evidence-reduction.md` §4's own phrase) still
  orders by each record's own device timestamp, exactly as before; this
  module's only job is to say whether that ordering can be trusted, not to
  fix it if it cannot. "Report the disagreement, do not correct it."
* **It does not decide what to do about a detected skew.** `skew_detected`
  is a finding, not a refusal wired into any descent, check or exit code --
  this module is not called from `checks.py`, `investigation.py` or
  `cli.py` (all out of scope for this change) and carries no opinion about
  what a caller should do with the finding beyond reporting it honestly.
* **It does not know which device produced which Loki record.** Loki's own
  records carry `host` (a syslog-ng-rewritten, `.sota-xrd`-suffixed label)
  and `source_ip` (the management IP), never a bare inventory device name --
  resolving either back to a device is `inventory_model.find_device`'s job,
  already solved by `logs_loki._DeviceSlot` in the other direction (name ->
  IP). Every function here takes records **already grouped by device name**,
  the same "the caller has already scoped this" contract
  `log_window.shape_window` uses for a single device, generalised to a
  `{device: records}` mapping.

Testing
-------
Pure functions over already-fetched records, the same "no I/O, no device
access, no model" contract `log_window.py`'s own module docstring states for
itself -- there is no `sender=`/`fetcher=` seam here because there is nothing
to fetch. Every test builds synthetic, Loki-shaped record dicts inline
(`timestamp`, `ingest_timestamp_ns`, matching `logs_loki._record_from_line`'s
own field names), the same convention `tests/test_logs_loki.py` already uses
rather than a committed fixture file -- so the whole suite runs with no
network and no reachable Loki, per this ticket's own constraint. A real,
reachable Loki (`http://172.20.250.103:3100` in this lab) was used once,
by hand, to sanity-check the algorithm against real data before any test was
written (the two spot-check numbers cited above); it is not a dependency of
anything committed here.
"""

from __future__ import annotations

import os
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ._env import _float_env
from .log_window import parse_device_timestamp

__all__ = [
    "CLOCK_SKEW_BOUND_SECONDS_ENV",
    "DEFAULT_CLOCK_SKEW_BOUND_SECONDS",
    "BOUND_SOURCE_CALLER_OVERRIDE",
    "BOUND_SOURCE_ENV_OVERRIDE",
    "BOUND_SOURCE_POLICY_DEFAULT",
    "NOT_EVALUATED",
    "WITHIN_BOUND",
    "SKEW_DETECTED",
    "DeviceClockOffset",
    "ClockSkewFinding",
    "assess_clock_skew",
    "device_offset_seconds",
    "summarize_device_offsets",
]

# --------------------------------------------------------------------------- #
# The bound -- a declared policy default, not a measured failure threshold
# --------------------------------------------------------------------------- #

CLOCK_SKEW_BOUND_SECONDS_ENV = "NETTOOLS_CLOCK_SKEW_BOUND_SECONDS"

#: **Policy default. Not measured.** Declared locally, the same
#: "not measured, because there is no failure threshold to discover... only
#: a bound to choose" honesty `admission.py`'s `DEFAULT_MAX_ACTIVE_PROBES_*`
#: already uses for exactly this situation (see that module's docstring,
#: "Active-probe budgeting") -- this repository's stated preference over an
#: invented-but-unlabelled constant.
#:
#: What was available to derive this from, and why none of it was used as a
#: derivation: `epoch.DEFAULT_SKEW_BOUND_SECONDS` (30s) is derived from a
#: protocol timer, but it answers a different question -- how long *our own*
#: observation window may run before a re-read is needed to trust it --
#: never how much two *devices'* clocks may disagree, and reusing its number
#: here would look like a derivation this module never did. The only real
#: numbers available are the two measurements the module docstring cites
#: above: 1.0s (600-sample probe, per-device medians only) and, from a
#: well-sampled subset of a second spot-check, 0.7s -- both **descriptions of
#: today's healthy fabric**, not failure thresholds; using either as the
#: bound would mean today's own reading passes only because it defines the
#: bound, which proves nothing about tomorrow's reading.
#:
#: So this is a plain policy choice, informed but not derived: large enough
#: that the small-sample noise the same spot-check measured (up to 8.4s,
#: driven entirely by three single-digit-sample devices, see the module
#: docstring) does not by itself read as a finding, small enough to still
#: catch the failure modes the backlog row names -- clock drift, a collector
#: backlog, or a dropped batch -- at the scale (minutes, not seconds) those
#: actually operate on. Configurable via
#: :data:`CLOCK_SKEW_BOUND_SECONDS_ENV`; a caller with real incident data
#: should revise this number on evidence, which is exactly why it is a named
#: constant and an env var rather than a literal buried in a comparison.
DEFAULT_CLOCK_SKEW_BOUND_SECONDS = 15.0

#: Where :func:`assess_clock_skew`'s bound actually came from -- carried on
#: every :class:`ClockSkewFinding` so a reader never has to guess whether a
#: `within_bound` result was close because of a policy default nobody has
#: looked at, or because an operator already tuned it. Same discipline as
#: `epoch.Coherence` recording its bound alongside its skew on every outcome,
#: not only on a breach (`evidence-epoch.md` §2.3a).
BOUND_SOURCE_POLICY_DEFAULT = "policy default (not measured)"
BOUND_SOURCE_ENV_OVERRIDE = f"env override ({CLOCK_SKEW_BOUND_SECONDS_ENV})"
BOUND_SOURCE_CALLER_OVERRIDE = "caller-supplied override"


def _resolve_bound(bound_seconds: float | None) -> tuple[float, str]:
    """`(bound, provenance)` -- caller override, then env, then the default.

    Deliberately does not delegate the "was the env var actually set" check
    to `_float_env` alone: that function's own contract is "unset, blank, or
    unparseable all fall back to `default`," which is exactly right for
    *resolving a value* and cannot, by itself, tell "the operator set 15 and
    it happens to equal the default" apart from "nothing was set." Reading
    `os.environ` once here, before calling `_float_env` for the actual
    parse, is what lets `bound_source` be honest either way -- the parse
    itself still goes through the one shared implementation
    (`_env._float_env`), never a second copy of its inf/nan/blank handling.
    """

    if bound_seconds is not None:
        return float(bound_seconds), BOUND_SOURCE_CALLER_OVERRIDE
    raw = os.environ.get(CLOCK_SKEW_BOUND_SECONDS_ENV, "").strip()
    if raw:
        return (
            _float_env(CLOCK_SKEW_BOUND_SECONDS_ENV, DEFAULT_CLOCK_SKEW_BOUND_SECONDS),
            BOUND_SOURCE_ENV_OVERRIDE,
        )
    return DEFAULT_CLOCK_SKEW_BOUND_SECONDS, BOUND_SOURCE_POLICY_DEFAULT


# --------------------------------------------------------------------------- #
# One record's offset from the shared reference
# --------------------------------------------------------------------------- #


def _parse_ingest_ns(value: Any) -> int | None:
    """`logs_loki._record_from_line`'s `ingest_timestamp_ns` -- a string of
    integer nanoseconds since the epoch (`_record_from_line`'s own field, fed
    by `_extract_records`'s `str(ts_ns)`). A bare `int` is also accepted, for
    a caller building synthetic records directly rather than through
    `logs_loki.py`'s own string convention. Anything else -- missing,
    `None`, empty, non-numeric, or a `bool` (which `isinstance(x, int)` would
    otherwise accept) -- is unparseable, not zero.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _device_datetime_near(
    parsed: tuple[int, int, int, int, int, int], reference: datetime
) -> datetime | None:
    """The absolute instant `parsed` (`log_window.DeviceTimestamp` -- no
    year) most plausibly names, given it must fall near `reference`.

    `log_window.seconds_between` solves the adjacent problem -- the interval
    between two device timestamps *from the same window* -- by anchoring both
    to one arbitrary shared year and re-anchoring forward across a Dec-to-Jan
    boundary. That trick does not extend to this function's question, which
    is not an interval between two no-year timestamps but an absolute instant
    compared against a real, dated reference (the ingest timestamp) -- so
    this tries the reference's own year and its immediate neighbours and
    keeps whichever candidate lands closest, which handles a device
    timestamp naming late December while the reference is early January (or
    the reverse) without assuming which side of the boundary either one is
    on. Returns `None` only if every candidate year fails to form a valid
    date (`day`/`hour`/`minute`/`second` out of range for every year tried,
    e.g. `parse_device_timestamp` having matched a numerically-shaped but
    invalid string) -- an unparseable instant, not a zero-offset one.
    """

    month, day, hour, minute, second, ms = parsed
    best: datetime | None = None
    best_delta = None
    for year in (reference.year - 1, reference.year, reference.year + 1):
        try:
            candidate = datetime(year, month, day, hour, minute, second, ms * 1000, tzinfo=timezone.utc)
        except ValueError:
            continue
        delta = abs((candidate - reference).total_seconds())
        if best_delta is None or delta < best_delta:
            best, best_delta = candidate, delta
    return best


def device_offset_seconds(device_timestamp: str, ingest_timestamp_ns: Any) -> float | None:
    """One record's `(device clock reading) - (ingest reference reading)`,
    in seconds -- or `None` when either side did not parse.

    **Sign convention**: negative means the device's own timestamp reads
    *earlier* than the moment the collector says it arrived -- the expected
    direction, since ingest can only ever happen after the device emitted
    the line. This is also the sign the measured numbers in the module
    docstring already use ("-5.0s to -4.0s"): a positive result is not
    impossible (sub-second timer rounding on a line that arrived within the
    same second it was generated can land on either side of zero) but a
    *large* positive value is exactly as informative as a large negative one
    -- both mean the device's clock and the reference disagree by that much.
    """

    parsed = parse_device_timestamp(device_timestamp)
    if parsed is None:
        return None
    ns = _parse_ingest_ns(ingest_timestamp_ns)
    if ns is None:
        return None
    ingest_dt = datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc)
    device_dt = _device_datetime_near(parsed, ingest_dt)
    if device_dt is None:
        return None
    return (device_dt - ingest_dt).total_seconds()


# --------------------------------------------------------------------------- #
# Per-device summary
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DeviceClockOffset:
    """One device's offset from the shared ingest-time reference, over
    whatever window of records it was given.

    Every timing field is `None`, never `0.0`, when nothing was measured --
    see the module docstring's "Absence is never zero" section. `min`/`max`
    ride alongside `median` (never replacing it as the point estimate --
    :func:`device_offset_seconds`'s own docstring is the argument for why a
    per-line sample is jitter, not a clock reading) so a caller can see a
    device's own internal spread: a device whose `max - min` is wide relative
    to `bound_seconds` is telling you its handful of samples straddle the
    pipeline's fast and slow paths (see the module docstring's PE4 spot-check),
    which is a reason to trust its `median` less, not evidence its clock
    itself is unstable.
    """

    device: str
    #: Every record this device supplied, whether or not it was comparable.
    total_records: int
    #: Records where both the device timestamp and the ingest timestamp
    #: parsed -- the sample size the fields below are actually computed
    #: over. **Read this before trusting a lone device's median**: a device
    #: with a handful of comparable records (this lab's P1/P4/PE4 measured
    #: 7-8 in 24h) can land its median anywhere across a pipeline's own
    #: jitter range by chance, which is a sampling problem, not a clock
    #: problem -- see the module docstring's spot-check. No minimum is
    #: enforced here (the same "no invented threshold" choice
    #: `log_window.MnemonicAggregate` makes for a singleton event); the count
    #: is reported instead of hidden behind one.
    comparable_records: int
    median_offset_seconds: float | None = None
    min_offset_seconds: float | None = None
    max_offset_seconds: float | None = None

    @property
    def evaluated(self) -> bool:
        """Whether this device contributed a real point estimate.

        `comparable_records > 0` alone would already be correct here (a
        median is always computable from at least one sample), but both are
        asserted together at every call site that matters -- see
        :func:`summarize_device_offsets` -- so this property exists as the
        single, named place that relationship is stated rather than
        re-derived ad hoc by a caller.
        """

        return self.comparable_records > 0 and self.median_offset_seconds is not None

    def as_dict(self) -> dict:
        return {
            "device": self.device,
            "total_records": self.total_records,
            "comparable_records": self.comparable_records,
            "evaluated": self.evaluated,
            "median_offset_seconds": _round_or_none(self.median_offset_seconds),
            "min_offset_seconds": _round_or_none(self.min_offset_seconds),
            "max_offset_seconds": _round_or_none(self.max_offset_seconds),
        }


def _round_or_none(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)


def summarize_device_offsets(device: str, records: Sequence[Mapping[str, Any]]) -> DeviceClockOffset:
    """One device's :class:`DeviceClockOffset` over its own record list.

    `records` is this device's own window, already scoped by the caller --
    the same "the caller has already narrowed to one device" contract
    `log_window.shape_window` uses. A record missing `timestamp` or
    `ingest_timestamp_ns` entirely (a plain device-buffer record, never
    routed through Loki) is simply not comparable, not an error -- the whole
    point of this module existing now is that Loki's records carry both
    fields and a device buffer's do not, so a device read only from its own
    buffer contributes `comparable_records=0` here by construction.
    """

    offsets: list[float] = []
    for record in records:
        offset = device_offset_seconds(record.get("timestamp", ""), record.get("ingest_timestamp_ns"))
        if offset is not None:
            offsets.append(offset)

    if not offsets:
        return DeviceClockOffset(device=device, total_records=len(records), comparable_records=0)

    return DeviceClockOffset(
        device=device,
        total_records=len(records),
        comparable_records=len(offsets),
        median_offset_seconds=statistics.median(offsets),
        min_offset_seconds=min(offsets),
        max_offset_seconds=max(offsets),
    )


# --------------------------------------------------------------------------- #
# The fabric-wide finding
# --------------------------------------------------------------------------- #

#: Fewer than two devices produced a median -- the retired blocker's own
#: condition ("no two clocks to disagree"). Not a pass; nothing was checked.
NOT_EVALUATED = "not_evaluated"
#: Two or more devices were evaluated and their medians span no more than
#: `bound_seconds`. Recorded with the real span, not just the verdict --
#: see `ClockSkewFinding.span_seconds`'s own docstring.
WITHIN_BOUND = "within_bound"
#: Two or more devices were evaluated and their medians span *more* than
#: `bound_seconds`. A finding, not a correction -- see the module docstring's
#: "What this module does not do."
SKEW_DETECTED = "skew_detected"


@dataclass(frozen=True)
class ClockSkewFinding:
    """Whether this window's devices' own clocks agree closely enough to
    order events across them by device timestamp.

    Never corrects, reorders, or drops a record -- see the module docstring.
    This is a report about whether an ordering *would* be trustworthy, built
    entirely from summaries (:class:`DeviceClockOffset`) of the records it
    was given; nothing about the input records survives into this object at
    all, by construction, which is the structural version of "report the
    disagreement, do not correct it."
    """

    per_device: tuple[DeviceClockOffset, ...] = field(default_factory=tuple)
    bound_seconds: float = DEFAULT_CLOCK_SKEW_BOUND_SECONDS
    bound_source: str = BOUND_SOURCE_POLICY_DEFAULT

    @property
    def evaluated_devices(self) -> tuple[DeviceClockOffset, ...]:
        return tuple(d for d in self.per_device if d.evaluated)

    @property
    def unevaluated_devices(self) -> tuple[str, ...]:
        """Devices this finding could not place on the shared timeline.

        Present in the input but excluded here -- never silently dropped --
        so a reader can tell "nine devices, nine agreeing clocks" apart from
        "nine devices, only four had any Loki-routed record at all," which
        `evaluated_devices` alone cannot distinguish (both would show four
        entries).
        """

        return tuple(d.device for d in self.per_device if not d.evaluated)

    @property
    def span_seconds(self) -> float | None:
        """The widest gap between any two evaluated devices' medians.

        `None` -- never `0.0` -- when fewer than two devices were evaluated;
        see the module docstring's "Absence is never zero." A genuine `0.0`
        means exactly what it says: two or more devices were evaluated and
        their medians coincided exactly, which :func:`test_a_measured_zero_
        span_is_a_real_pass_not_an_abstention` (`tests/test_clock_skew.py`)
        pins as a distinct outcome from this property being `None`.
        """

        medians = [d.median_offset_seconds for d in self.evaluated_devices]
        if len(medians) < 2:
            return None
        return max(medians) - min(medians)

    @property
    def status(self) -> str:
        span = self.span_seconds
        if span is None:
            return NOT_EVALUATED
        return SKEW_DETECTED if span > self.bound_seconds else WITHIN_BOUND

    @property
    def furthest_pair(self) -> tuple[DeviceClockOffset, DeviceClockOffset] | None:
        """The two evaluated devices whose medians disagree the most, or
        `None` when :attr:`span_seconds` is `None` -- the same fewer-than-two
        condition, restated as a property so a caller does not have to
        re-derive it from `evaluated_devices` by hand."""

        evaluated = self.evaluated_devices
        if len(evaluated) < 2:
            return None
        lo = min(evaluated, key=lambda d: d.median_offset_seconds)
        hi = max(evaluated, key=lambda d: d.median_offset_seconds)
        return (lo, hi)

    @property
    def detail(self) -> str:
        """Why, in one line, whatever the outcome -- populated on a clean
        pass as well as a breach, the same "the margin is recorded when it
        passes, too" discipline `evidence-epoch.md` §2.3a states for
        `epoch.Coherence` (a different skew, the same honesty about it)."""

        if self.status == NOT_EVALUATED:
            names = ", ".join(sorted(self.unevaluated_devices)) or "none"
            return (
                f"{len(self.evaluated_devices)} device(s) had a comparable clock "
                f"reading; at least two are required to compare clocks against "
                f"each other. Not evaluated: {names}"
            )

        lo, hi = self.furthest_pair
        line = (
            f"span {self.span_seconds:.3f}s of {self.bound_seconds:.1f}s allowed "
            f"({self.bound_source}); widest disagreement is {lo.device} "
            f"(median {lo.median_offset_seconds:+.3f}s, n={lo.comparable_records}) vs "
            f"{hi.device} (median {hi.median_offset_seconds:+.3f}s, n={hi.comparable_records})"
        )
        if self.unevaluated_devices:
            line += f"; not evaluated: {', '.join(sorted(self.unevaluated_devices))}"
        if self.status == SKEW_DETECTED:
            line += (
                " -- EXCEEDED. Consequences of trusting device-timestamp ordering "
                "across these devices anyway: clock drift, a collector backlog, or "
                "a dropped batch can each produce this signature, and this finding "
                "does not distinguish which. Not corrected -- report only."
            )
        return line

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "bound_seconds": round(self.bound_seconds, 3),
            "bound_source": self.bound_source,
            "span_seconds": _round_or_none(self.span_seconds),
            "evaluated_devices": [d.device for d in self.evaluated_devices],
            "unevaluated_devices": list(self.unevaluated_devices),
            "per_device": [d.as_dict() for d in self.per_device],
            "detail": self.detail,
        }


def assess_clock_skew(
    records_by_device: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    bound_seconds: float | None = None,
) -> ClockSkewFinding:
    """The one entry point: summarise every device, then compare them.

    `records_by_device` is `{device_name: [record, ...]}`, each record in
    `logs_loki.py`'s own shape (`timestamp`, `ingest_timestamp_ns` at
    minimum) -- see the module docstring's "What this module does not do"
    for why the device-name grouping is the caller's job, not this
    function's. `bound_seconds` overrides both the env var and the policy
    default when given explicitly; see :func:`_resolve_bound`.

    Never raises on malformed input records: an unparseable timestamp is
    excluded from that device's sample (`summarize_device_offsets`), never a
    reason to fail the whole assessment -- the same "an unattributable
    record is kept, never dropped on the balance of probability, and the
    count is reported" spirit `log_window.NoiseRule.matches` uses for a
    different kind of uncertainty.
    """

    per_device = tuple(
        summarize_device_offsets(device, records) for device, records in records_by_device.items()
    )
    resolved_bound, bound_source = _resolve_bound(bound_seconds)
    return ClockSkewFinding(per_device=per_device, bound_seconds=resolved_bound, bound_source=bound_source)
