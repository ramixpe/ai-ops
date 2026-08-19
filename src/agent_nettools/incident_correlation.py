"""Cross-alert correlation across concurrent ``investigate`` runs. B-486.

Five pages from one root cause should collapse to one -- but collapsing is a
**causal claim**. Saying "these five alerts are one incident" asserts a
relationship between them, and this build refuses exactly that kind of
reasoning everywhere else it has come up:

* **B-437** -- a rung in a descent must be a real dependency hypothesis, not
  a thing that merely happens to sit near another thing.
* **OBS-167** -- IS-IS and LDP going down on the same interface at the same
  moment is a *shared root cause* (something beneath both), not evidence that
  one caused the other; ``ldp_session_up``'s own docstring calls this out by
  name (see ``checks.py``).
* **B-108** -- an aggregation over independent per-protocol signals
  (``device_health``) is not a dependency descent, and was refused as a flow
  for exactly that reason; ``nettools health DEVICE`` exists instead of
  pretending a rollup is a diagnosis.

So this module correlates on **structure it can defend**, never on
co-occurrence. Three bases, in strictly descending order of how strong a
claim they license -- read this before adding a fourth:

1. **Same descent cause** (:func:`correlate_by_cause`) -- two investigations
   whose descents bottom out on the *identical broken rung* (the same
   device, the same rung name, the same subject). By ``descent.py``'s own
   definition, the lowest broken rung **is** the root cause, so two
   descents landing on the same one are, by this build's own vocabulary,
   one incident. This is the only basis strong enough to produce an
   :class:`Incident` (a claim of shared cause).
2. **Same subject** (:func:`correlate_by_subject`) -- two investigations
   that targeted the identical named object (the same BGP peer address, the
   same interface). Defensible as "these are about the same thing" -- worth
   showing together so a NOC does not get paged twice about one peer from
   two different flows -- but **not** a claim that they share a cause,
   because a subject can genuinely have two unrelated problems at once.
3. **Same device** (:func:`correlate_by_device`) -- weaker still: two
   investigations whose *origin* device is the same. Useful for triage
   ("here is everything currently flagged on PE2") and nothing more; a
   device having two unrelated faults on the same day is ordinary, not
   evidence of one thing.

Bases 2 and 3 return :class:`Incident` objects too (the type is the same),
but grouped into :attr:`CorrelationResult.clusters` rather than
:attr:`CorrelationResult.incidents`, and every one of them carries a
``reason`` that states explicitly that no shared cause is being claimed. A
caller collapsing pages must read which bucket a group came from before
deciding whether "collapse to one" is honest for that group -- collapsing a
cluster the way an incident collapses (dropping the other members' distinct
detail) would reintroduce the exact danger this module exists to avoid.

What is refused, and why
---------------------------
:data:`EXCLUDED_CORRELATIONS` names what this module will not do. The most
important entry is arrival-time proximity ("within 30 seconds of each
other") -- it is not a structural relationship, it merges unrelated faults on
a busy day, and its failure mode is silent: a second, real, unrelated
incident hides inside the merged one, indistinguishable from the first
except to someone who goes looking. A heuristic that is right most of the
time is a worse outcome here than a narrow rule that is right always and
silent about the rest -- so this module is narrow, and says so.

The ledger integration gap
-------------------------------
:func:`from_ledger_diagnoses` adapts ``ledger.diagnoses()``-shaped rows (read
as plain dicts -- this module imports nothing from ``ledger.py``, so it has
no dependency on that module's format or ownership). Today's ledger records
a diagnosis's cause as ``{"rung", "device", "reason"}`` (see ``cli.py``'s
``_record_diagnosis_in_ledger``) and **does not carry the cause's
``subject``** -- `descent.DescentResult.cause` is a `RungOutcome` whose
`CheckResult.subject` (the peer, the interface) is simply dropped before it
reaches the ledger. :func:`correlate_by_cause` treats a missing subject as
*unknown*, never as a wildcard: two diagnoses on the same device and the same
rung, both with subject unknown, are **not** merged, because doing so risks
merging two genuinely different faults (say, two different interfaces both
tripping ``interface_state`` on the same box) into one incident -- exactly
the silent hidden-second-incident failure this module refuses to produce.
Concretely, this means cause-based correlation will rarely fire against
today's ledger until it is extended to record ``cause.subject`` -- a one-line
addition to ``cli.py``, offered in the report accompanying this change rather
than made here (``cli.py``/``ledger.py`` are owned by other tracks this
session). Subject- and device-based correlation are unaffected: both read
fields the ledger already records at the diagnosis's own top level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

__all__ = [
    "BASIS_SAME_CAUSE",
    "BASIS_SAME_DEVICE",
    "BASIS_SAME_SUBJECT",
    "EXCLUDED_CORRELATIONS",
    "CorrelationResult",
    "DiagnosisRef",
    "Incident",
    "correlate",
    "correlate_by_cause",
    "correlate_by_device",
    "correlate_by_subject",
    "from_ledger_diagnoses",
]

#: The only basis strong enough to assert a shared root cause.
BASIS_SAME_CAUSE = "same_cause"
#: Defensible groupings that make no causal claim -- see the module docstring.
BASIS_SAME_SUBJECT = "same_subject"
BASIS_SAME_DEVICE = "same_device"

#: What this module deliberately does not do, and why -- see the module
#: docstring's "What is refused" section. Kept as data, not just prose, so a
#: caller (or a test) can print it, and so a review can see at a glance that
#: nothing in this module computes a timestamp delta anywhere.
EXCLUDED_CORRELATIONS: tuple[str, ...] = (
    "arrival-time proximity (e.g. \"within 30 seconds of each other\") -- "
    "co-occurrence is not a causal relationship, and this build already "
    "rejects that reasoning elsewhere (B-437, OBS-167, B-108); it would "
    "merge unrelated incidents on a busy day, silently, which is the one "
    "failure mode worse than under-correlating",
    "co-occurrence with no shared device, subject, or cause -- two alerts "
    "with nothing structurally in common are not correlated merely because "
    "both happened",
    "shared flow name alone (e.g. two bgp_session investigations) -- the "
    "same KIND of check ran twice is not evidence of a relationship "
    "between the two runs",
    "shared finding string alone (e.g. two 'interface_line_down' findings "
    "on different devices) -- the same LABEL is not the same fault",
    "severity or trustworthy alone -- both describe one diagnosis's own "
    "confidence, not a relationship to any other diagnosis",
)


@dataclass(frozen=True)
class DiagnosisRef:
    """One investigation's outcome, as much as this module needs to correlate it.

    Deliberately not ``ledger``'s diagnosis dict and not
    ``descent.DescentResult`` -- this module takes a caller-constructed,
    minimal shape instead of importing either, so it has no dependency on the
    ledger's on-disk format or the descent's internal types and can be fed
    from any of them (a ``ledger.diagnoses()`` row via
    :func:`from_ledger_diagnoses`, a live ``DescentResult``, a ticket) without
    this module needing to know which.

    ``cause_rung``/``cause_device``/``cause_subject`` are ``None`` together
    when nothing broke (an ``all_layers_healthy`` or ``undetermined`` run);
    such a diagnosis participates in subject/device clustering but never in
    cause-based correlation, which needs an actual broken rung to point at.
    """

    id: str
    device: str
    subject: str
    flow: str
    finding: str
    cause_rung: str | None = None
    cause_device: str | None = None
    cause_subject: str | None = None
    recorded_at: str | None = None


@dataclass(frozen=True)
class Incident:
    """One group of diagnoses, and the exact structural fact that grouped them.

    ``basis`` names which of the three tiers produced this group --
    :data:`BASIS_SAME_CAUSE` is the only one asserting shared causality; the
    other two are asserted only as "these share this one fact", explicitly
    not as "these are one fault". Read ``reason`` before treating a group as
    collapsible.
    """

    basis: str
    key: tuple[Any, ...]
    members: tuple[DiagnosisRef, ...]
    reason: str


def correlate_by_cause(
    diagnoses: Sequence[DiagnosisRef],
) -> tuple[list[Incident], list[DiagnosisRef]]:
    """Group diagnoses whose descents bottom out on the identical broken rung.

    Returns ``(incidents, excluded_for_missing_subject)``. A diagnosis with
    no cause at all (``cause_rung is None``) never appears in either list --
    it simply has nothing to correlate on this basis. A diagnosis *with* a
    cause but an unrecorded subject appears in ``excluded_for_missing_subject``
    rather than being grouped by ``(device, rung)`` alone -- see the module
    docstring's "ledger integration gap" section for why an unknown subject
    is never treated as a wildcard.
    """

    groups: dict[tuple[str, str, str], list[DiagnosisRef]] = {}
    excluded: list[DiagnosisRef] = []

    for d in diagnoses:
        if d.cause_rung is None or d.cause_device is None:
            continue
        if d.cause_subject is None:
            # Cannot tell "this rung genuinely has no subject" (a
            # device-scoped check, e.g. one with no interface/peer to name)
            # apart from "the subject was not recorded" (today's ledger
            # gap). A false merge here silently hides a second incident,
            # which is the one outcome this module refuses to risk -- so an
            # unknown subject excludes the diagnosis rather than matching
            # everything else with the same (device, rung).
            excluded.append(d)
            continue
        key = (d.cause_device, d.cause_rung, d.cause_subject)
        groups.setdefault(key, []).append(d)

    incidents = [
        Incident(
            basis=BASIS_SAME_CAUSE,
            key=key,
            members=tuple(members),
            reason=(
                f"{len(members)} investigations bottom out on the same broken rung "
                f"{key[1]!r} on {key[0]!r} ({key[2]}) -- by descent.py's own "
                "definition the lowest broken rung is the root cause, so two "
                "descents landing on the same one are one incident"
            ),
        )
        for key, members in groups.items()
        if len(members) >= 2
    ]
    return incidents, excluded


def correlate_by_subject(diagnoses: Sequence[DiagnosisRef]) -> list[Incident]:
    """Group diagnoses that targeted the identical named subject.

    Makes no claim of shared cause -- see the module docstring's basis 2.
    """

    groups: dict[str, list[DiagnosisRef]] = {}
    for d in diagnoses:
        groups.setdefault(d.subject, []).append(d)

    return [
        Incident(
            basis=BASIS_SAME_SUBJECT,
            key=(subject,),
            members=tuple(members),
            reason=(
                f"{len(members)} investigations target the same subject "
                f"{subject!r} -- grouped for visibility; this does not assert "
                "they share a root cause"
            ),
        )
        for subject, members in groups.items()
        if len(members) >= 2
    ]


def correlate_by_device(diagnoses: Sequence[DiagnosisRef]) -> list[Incident]:
    """Group diagnoses whose origin device is the same.

    Makes no claim of shared cause -- see the module docstring's basis 3, the
    weakest of the three.
    """

    groups: dict[str, list[DiagnosisRef]] = {}
    for d in diagnoses:
        groups.setdefault(d.device, []).append(d)

    return [
        Incident(
            basis=BASIS_SAME_DEVICE,
            key=(device,),
            members=tuple(members),
            reason=(
                f"{len(members)} investigations originate from the same device "
                f"{device!r} -- grouped for triage only; a device can have more "
                "than one unrelated fault at once, so this does not assert a "
                "shared root cause"
            ),
        )
        for device, members in groups.items()
        if len(members) >= 2
    ]


@dataclass(frozen=True)
class CorrelationResult:
    """What :func:`correlate` found, kept in the three tiers described above."""

    incidents: tuple[Incident, ...] = field(default_factory=tuple)
    clusters: tuple[Incident, ...] = field(default_factory=tuple)
    excluded_for_missing_subject: tuple[DiagnosisRef, ...] = field(default_factory=tuple)
    excluded_bases: tuple[str, ...] = EXCLUDED_CORRELATIONS


def correlate(diagnoses: Sequence[DiagnosisRef]) -> CorrelationResult:
    """Run every defensible basis and return the tiered result.

    ``incidents`` is the only field asserting shared root cause. ``clusters``
    (subject- and device-based) is for grouping/triage and must be presented
    with its own ``reason``, never silently collapsed the way an incident is.
    """

    incidents, excluded = correlate_by_cause(diagnoses)
    clusters = tuple(correlate_by_subject(diagnoses)) + tuple(correlate_by_device(diagnoses))
    return CorrelationResult(
        incidents=tuple(incidents),
        clusters=clusters,
        excluded_for_missing_subject=tuple(excluded),
    )


def from_ledger_diagnoses(entries: Iterable[dict[str, Any]]) -> list[DiagnosisRef]:
    """Adapt ``ledger.diagnoses()``-shaped rows into :class:`DiagnosisRef`.

    Reads the dict shape only -- no import of ``ledger.py``, so this module
    has no dependency on it (see the module docstring). ``cause.get(
    "subject")`` is always ``None`` against today's ledger schema, which
    :func:`correlate_by_cause` already treats safely (excluded, never
    wildcarded) -- see the module docstring's "ledger integration gap".
    """

    refs = []
    for e in entries:
        cause = e.get("cause") or {}
        refs.append(
            DiagnosisRef(
                id=str(e.get("id")),
                device=str(e.get("device")),
                subject=str(e.get("subject")),
                flow=str(e.get("flow")),
                finding=str(e.get("finding")),
                cause_rung=cause.get("rung"),
                cause_device=cause.get("device"),
                cause_subject=cause.get("subject"),
                recorded_at=e.get("recorded_at"),
            )
        )
    return refs
