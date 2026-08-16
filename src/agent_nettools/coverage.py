"""What an evidence source was actually able to tell us.

`evidence-reduction.md` §7. This is the `unevaluated` discipline applied to
evidence *sources* rather than to checks.

The asymmetry this closes
--------------------------
Grounding enforces citation for claims of **presence**: an observation must
name the evidence key it was read from. It enforced nothing for claims of
**absence**, so

    "no correlating events in window"

passed with nothing behind it — on a source already measured to drop severity 5
and 6, which is to say on a source that cannot support the claim at all.

That is the same asymmetry `check_chain_coverage` exists to close, arriving on
a different axis: **a check that inspects only what is present cannot see what
was omitted.**

Why this is not paranoia
-------------------------
Measured on this fabric. Querying the log platform for PE2's window returns two
real, correctly-timestamped severity-3 link events — *from the previous day's
restore* — and nothing from the isolation being investigated. Not an empty
result. A plausible, internally consistent, non-empty answer about a different
incident.

An empty result is honest and visibly incomplete. A partial result is neither.
A severity filter that removes the consequences of an event while retaining
superficially similar events from elsewhere in the buffer does not degrade a
timeline; it fabricates one, and nothing downstream can detect that.

Coverage is built by code
--------------------------
Never by a model, and never asserted by the caller. Every field here is read
from what the device itself reported — the `show logging` header states its own
buffer level and message count — or counted during shaping. A coverage record a
caller could set by hand would be a claim, not a measurement.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "IOSXR_SEVERITY_LEVELS",
    "ALL_SEVERITIES",
    "Coverage",
    "severities_at_or_below",
]

#: IOS-XR logging level names, and the highest numeric severity each admits.
#: `Buffer logging: level debugging` therefore means the buffer holds 0-7 and
#: has no severity gap; `level errors` would mean 5 and 6 are absent, which is
#: exactly the shape of B-206a downstream.
IOSXR_SEVERITY_LEVELS: dict[str, int] = {
    "emergencies": 0,
    "alerts": 1,
    "critical": 2,
    "errors": 3,
    "warnings": 4,
    "notifications": 5,
    "informational": 6,
    "debugging": 7,
}

ALL_SEVERITIES: tuple[int, ...] = tuple(range(8))


def severities_at_or_below(level: str | None) -> tuple[int, ...]:
    """Severities a source configured at ``level`` will carry.

    An unrecognised or missing level returns the empty tuple rather than
    guessing at completeness — not knowing what a source carries is a gap, and
    the one thing it must never resolve to is "everything".
    """

    ceiling = IOSXR_SEVERITY_LEVELS.get((level or "").strip().lower())
    if ceiling is None:
        return ()
    return tuple(range(ceiling + 1))


@dataclass(frozen=True)
class Coverage:
    """What one evidence read did and did not cover.

    Travels with the evidence it describes, so a claim and its scope cannot be
    separated. :meth:`gaps` is the operative part: an absence claim is only a
    negative finding over coverage with no gaps.
    """

    device: str
    source: str
    query_complete: bool = True
    #: Device-clock bounds of what was actually retrieved.
    window_start: str | None = None
    window_end: str | None = None
    #: Severities the source is configured to carry, read from the source.
    severity_available: tuple[int, ...] = ALL_SEVERITIES
    #: How many records the source holds, where it says so.
    records_available: int | None = None
    records_returned: int = 0
    #: Records the source itself reports discarding before we asked.
    records_dropped_at_source: int = 0
    #: Records a noise rule declined to attribute, and therefore kept.
    records_kept_unattributable: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def severity_missing(self) -> tuple[int, ...]:
        return tuple(s for s in ALL_SEVERITIES if s not in self.severity_available)

    @property
    def truncated(self) -> bool:
        """The source held more than it returned.

        Read from the source's own count where it publishes one. `show logging`
        does: its header states how many messages the buffer holds, so "200 of
        593" is a statement rather than an inference.
        """

        if self.records_available is None:
            return False
        return self.records_returned < self.records_available

    def gaps(self) -> tuple[str, ...]:
        """Every reason this coverage cannot support a claim of absence.

        Empty means an absence claim over this read is a real negative. Anything
        else means the honest answer is "not in the available coverage", which
        is an `unevaluated`, not a `no`.
        """

        reasons: list[str] = []
        if not self.query_complete:
            reasons.append("the query did not complete")
        if self.severity_missing:
            reasons.append(
                "severities " + ",".join(str(s) for s in self.severity_missing)
                + " are not carried by this source"
            )
        if self.truncated:
            reasons.append(
                f"{self.records_returned} of {self.records_available} available "
                f"records were retrieved; the rest of the window was not read"
            )
        if self.records_dropped_at_source:
            reasons.append(
                f"the source reports dropping {self.records_dropped_at_source} records"
            )
        return tuple(reasons) + self.notes

    @property
    def complete(self) -> bool:
        return not self.gaps()

    def describe(self) -> str:
        window = (
            f" {self.window_start} to {self.window_end}"
            if self.window_start and self.window_end
            else ""
        )
        head = (
            f"{self.source} on {self.device}{window}: "
            f"{self.records_returned} records"
        )
        if self.complete:
            return head + ", coverage complete"
        return head + ", coverage incomplete -- " + "; ".join(self.gaps())

    def as_dict(self) -> dict:
        """The record that travels in the evidence envelope."""

        return {
            "device": self.device,
            "source": self.source,
            "window": {"start": self.window_start, "end": self.window_end},
            "query_complete": self.query_complete,
            "severity_available": list(self.severity_available),
            "severity_missing": list(self.severity_missing),
            "records_available": self.records_available,
            "records_returned": self.records_returned,
            "records_dropped_at_source": self.records_dropped_at_source,
            "records_kept_unattributable": self.records_kept_unattributable,
            "complete": self.complete,
            "gaps": list(self.gaps()),
        }
