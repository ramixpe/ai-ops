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
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "COLLECTOR_NOISE",
    "COLLECTOR_SOURCES",
    "Attribution",
    "NoiseRule",
    "ShapedWindow",
    "dedupe",
    "drop_collector_noise",
    "filter_to_subject",
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


def shape_window(
    records: list[dict[str, Any]],
    *,
    subject: str | None = None,
    drop_noise: bool = True,
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

    return ShapedWindow(
        records=tuple(narrowed),
        total_in=total,
        duplicates_removed=duplicates,
        collector_noise_removed=noise,
        unrelated_removed=unrelated,
        unattributed_kept=unattributed,
    )
