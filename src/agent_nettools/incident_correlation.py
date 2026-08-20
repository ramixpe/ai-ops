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

The ledger integration gap (B-486), and what W3a closed
-------------------------------------------------------------
:func:`from_ledger_diagnoses` adapts ``ledger.diagnoses()``-shaped rows (read
as plain dicts -- this module imports nothing from ``ledger.py``, so it has
no dependency on that module's format or ownership). Before this change, the
ledger recorded a diagnosis's cause as ``{"rung", "device", "reason"}`` and
never carried the cause's ``subject`` or the investigation's ``run_id`` --
`descent.DescentResult.cause` is a `RungOutcome` whose `CheckResult.subject`
(the peer, the interface) was simply dropped before it reached the ledger,
and nothing on a diagnosis row named which investigation run produced it. A
one-line addition to ``ledger.py`` (a new ``run_id`` parameter on
``record_diagnosis``) plus a one-line addition to whatever builds the
``cause`` dict passed in (adding ``"subject": cause.result.subject`` beside
the existing ``"rung"``/``"device"``/``"reason"``) closes both -- see
``ledger.py``'s own module docstring for the ``run_id`` half, and this
change's accompanying report for the exact call ``cli.py`` needs once it is
free to make it (``cli.py`` is owned by another track this session).

This module now *reads* both fields (``cause_subject``/``run_id`` on
:class:`DiagnosisRef`), but the two ledgers -- rows written before and after
this change -- coexist forever in one append-only file, so both fields are
still frequently absent and :func:`correlate_by_cause` still treats an
absence as unknown, never as a wildcard, for exactly the reason it always
has: two diagnoses on the same device and the same rung, one or both missing
``cause_subject`` or ``run_id``, are **not** merged, because doing so risks
merging two genuinely different faults (say, two different interfaces both
tripping ``interface_state`` on the same box) into one incident -- exactly
the silent hidden-second-incident failure this module refuses to produce.
``run_id`` joins ``cause_subject`` as a second required-to-merge field for a
reason beyond deduplication safety: an :class:`Incident` this module produces
is only actionable if a human reading it can walk each member back to the
concrete investigation that produced it (the ticket, the evidence) -- a
diagnosis with no recorded ``run_id`` cannot be traced that way, so this
module declines to vouch for its membership in a same-cause incident rather
than silently presenting an untraceable claim as equally solid. Every
exclusion is counted, by reason, on :class:`CorrelationResult` -- see
:func:`correlate_by_cause`'s own docstring -- rather than the excluded rows
simply not appearing anywhere, which is what "counted, never silently
treated as non-matching" means in practice. Subject- and device-based
clustering (bases 2 and 3) are unaffected by any of this: both read fields
the ledger already records at the diagnosis's own top level, present on
every row regardless of when it was written.
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

    ``run_id`` identifies the investigation *run* that produced this
    diagnosis (``ticket.Ticket.run_id``, B-486/W3a) -- distinct from ``id``,
    which is this ref's own identity (a ledger entry id, or whatever a
    non-ledger caller supplies) and carries no meaning about which run
    produced it. ``None`` means unrecorded, never "no run" -- see the module
    docstring's "ledger integration gap" section.
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
    run_id: str | None = None


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
) -> tuple[list[Incident], list[DiagnosisRef], list[DiagnosisRef]]:
    """Group diagnoses whose descents bottom out on the identical broken rung.

    Returns ``(incidents, excluded_for_missing_subject,
    excluded_for_missing_run_id)``. A diagnosis with no cause at all
    (``cause_rung is None``) never appears in any of the three lists -- it
    simply has nothing to correlate on this basis. A diagnosis *with* a cause
    but an unrecorded ``cause_subject`` and/or ``run_id`` is excluded rather
    than being grouped by ``(device, rung)`` alone -- see the module
    docstring's "ledger integration gap" section for why neither is ever
    treated as a wildcard, and why ``run_id`` is required for this basis at
    all. A diagnosis missing *both* appears in *both* excluded lists -- each
    list answers "how many rows lack this one fact", not "how many rows were
    excluded for exactly this reason and no other".
    """

    groups: dict[tuple[str, str, str], list[DiagnosisRef]] = {}
    excluded_for_missing_subject: list[DiagnosisRef] = []
    excluded_for_missing_run_id: list[DiagnosisRef] = []

    for d in diagnoses:
        if d.cause_rung is None or d.cause_device is None:
            continue
        excludable = False
        if d.cause_subject is None:
            # Cannot tell "this rung genuinely has no subject" (a
            # device-scoped check, e.g. one with no interface/peer to name)
            # apart from "the subject was not recorded" (today's ledger
            # gap for rows written before W3a). A false merge here silently
            # hides a second incident, which is the one outcome this module
            # refuses to risk -- so an unknown subject excludes the
            # diagnosis rather than matching everything else with the same
            # (device, rung).
            excluded_for_missing_subject.append(d)
            excludable = True
        if d.run_id is None:
            # Same refusal, for a different reason: an incident this module
            # asserts must be traceable back to the concrete investigation
            # that produced each member (the ticket, the evidence) -- a
            # diagnosis with no run_id cannot be, so it is excluded here
            # rather than silently vouched for at the same confidence as a
            # traceable one. See the module docstring.
            excluded_for_missing_run_id.append(d)
            excludable = True
        if excludable:
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
    return incidents, excluded_for_missing_subject, excluded_for_missing_run_id


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
    """What :func:`correlate` found, kept in the three tiers described above.

    ``excluded_for_missing_subject``/``excluded_for_missing_run_id`` are the
    full :class:`DiagnosisRef` lists (a diagnosis missing both appears in
    both -- see :func:`correlate_by_cause`'s docstring); ``counts`` reduces
    each to the single integer a summary view needs (e.g. ``rows_without_subject``),
    named so a caller building a report never has to re-derive
    ``len(excluded_for_missing_subject)`` itself and risk two call sites
    disagreeing about what the number means.
    """

    incidents: tuple[Incident, ...] = field(default_factory=tuple)
    clusters: tuple[Incident, ...] = field(default_factory=tuple)
    excluded_for_missing_subject: tuple[DiagnosisRef, ...] = field(default_factory=tuple)
    excluded_for_missing_run_id: tuple[DiagnosisRef, ...] = field(default_factory=tuple)
    excluded_bases: tuple[str, ...] = EXCLUDED_CORRELATIONS

    @property
    def counts(self) -> dict[str, int]:
        """``{"rows_without_subject": N, "rows_without_run_id": M}`` -- always
        present, even at zero, so a caller can log/report the exclusion count
        without first checking whether anything was excluded at all."""

        return {
            "rows_without_subject": len(self.excluded_for_missing_subject),
            "rows_without_run_id": len(self.excluded_for_missing_run_id),
        }


def correlate(diagnoses: Sequence[DiagnosisRef]) -> CorrelationResult:
    """Run every defensible basis and return the tiered result.

    ``incidents`` is the only field asserting shared root cause. ``clusters``
    (subject- and device-based) is for grouping/triage and must be presented
    with its own ``reason``, never silently collapsed the way an incident is.
    """

    incidents, excluded_subject, excluded_run_id = correlate_by_cause(diagnoses)
    clusters = tuple(correlate_by_subject(diagnoses)) + tuple(correlate_by_device(diagnoses))
    return CorrelationResult(
        incidents=tuple(incidents),
        clusters=clusters,
        excluded_for_missing_subject=tuple(excluded_subject),
        excluded_for_missing_run_id=tuple(excluded_run_id),
    )


def from_ledger_diagnoses(entries: Iterable[dict[str, Any]]) -> list[DiagnosisRef]:
    """Adapt ``ledger.diagnoses()``-shaped rows into :class:`DiagnosisRef`.

    Reads the dict shape only -- no import of ``ledger.py``, so this module
    has no dependency on it (see the module docstring). ``cause.get("subject")``
    and the row's own ``run_id`` are both ``None`` for any row written before
    W3a (``ledger.py``'s ``record_diagnosis`` gained ``run_id``, and
    whichever caller builds ``cause`` gained ``cause["subject"]``) --
    :func:`correlate_by_cause` already treats both safely (excluded, never
    wildcarded, and counted) -- see the module docstring's "ledger
    integration gap".
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
                run_id=e.get("run_id"),
            )
        )
    return refs
