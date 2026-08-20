"""The deterministic walker — descend a flow's ladder and localise the cause.

**No model is involved anywhere in this module.** That is the claim the whole
investigation layer rests on: finding the lowest broken protocol layer is
parse-and-compare, not judgement. If this file ever needs a model call,
something above it has been designed wrong.

The walk
--------
Corrected 2026-08-16 after the original rule was measured against the `broken`
label and found to be a defect in the plan (Q-017, OBS-055):

=============  ===================================================
Rung verdict   What the walk does
=============  ===================================================
``broken``     record the finding and **continue descending**
``healthy``    **continue descending**
``unevaluated``  **stop** — nothing below an unread rung is trustworthy
=============  ===================================================

**The result is the LOWEST broken rung.** Higher broken rungs are not
discarded; they become the *causal chain*, the evidence that this cause
explains the observed symptom.

Two things about this are easy to get wrong, and the plan got both wrong:

*A healthy rung does not prove the rungs below it are fine.* A rung is only
ever checked against one device's view. Measured on this fabric: RR1's own
IS-IS was healthy while PE2 — the far end of the session under investigation —
had no adjacencies at all.

*Stopping at the first broken rung finds the **highest** broken layer, not the
lowest.* When a link is shut, everything above it breaks too. Stop at the first
and the report is "BGP is not established" — where the investigation started.
Walk to the bottom and it becomes "the interface is admin-down, which isolated
IS-IS, which removed the route, which blocked transport, which is why BGP is
Idle". The first restates the alert; the second is an RCA.

Why ``unevaluated`` still stops
--------------------------------
It is the one verdict that says *we do not know*. Continuing past it would mean
descending on an assumption, and any deeper finding would inherit that
assumption silently — the failure this project has now met four times over
(OBS-006, OBS-043, OBS-044, and the vacuous first draft of the agreement test).
An unread rung ends the walk with ``undetermined`` and the reason recorded.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .checks import BROKEN, HEALTHY, UNEVALUATED, CheckResult
from .flows import (
    ALL_LAYERS_HEALTHY,
    CAUSE_NOT_LOCALISED,
    NO_FAULT_ON_PATH,
    TEMPORALLY_INCOHERENT,
    UNDETERMINED,
    Aggregation,
    DeviceScope,
    Flow,
    Rung,
    SubjectRule,
)
from .interface_kind import physical_members, same_interface

if TYPE_CHECKING:  # `epoch` imports this module, so the dependency is one-way
    from .epoch import Coherence

#: Module logger. No handler is installed here, so this is silent unless the
#: application configures logging -- the module stays free of I/O by default.
LOGGER = logging.getLogger(__name__)

__all__ = [
    "DescentResult",
    "path_interfaces",
    "RungOutcome",
    "evaluate_rung",
    "resolve_devices",
    "run_descent",
]


@dataclass(frozen=True)
class RungOutcome:
    """One rung's result, and which device it was actually evaluated against."""

    rung: str
    device: str
    result: CheckResult

    @property
    def status(self) -> str:
        return self.result.status


@dataclass(frozen=True)
class DescentResult:
    """What a descent found, and the evidence for it."""

    flow: str
    device: str
    subject: str
    finding: str
    outcomes: tuple[RungOutcome, ...] = field(default_factory=tuple)
    evidence_keys: tuple[str, ...] = field(default_factory=tuple)
    reason: str | None = None

    #: When the evidence was read and whether it held still while it was being
    #: read. ``None`` when no coherence check ran -- an injected collector, or a
    #: caller that has not opted in. Reviewer A's note was that the per-command
    #: timestamp existed and was not preserved here; this is where it lands.
    coherence: Coherence | None = None

    @property
    def cause(self) -> RungOutcome | None:
        """The lowest broken rung — the root cause, or ``None`` if nothing broke."""

        broken = [o for o in self.outcomes if o.status == BROKEN]
        return broken[-1] if broken else None

    @property
    def causal_chain(self) -> tuple[RungOutcome, ...]:
        """The broken rungs *above* the cause, top-down.

        These are what turn a finding into an explanation: each one is a
        consequence of the cause, and together they are the evidence that this
        cause accounts for the symptom that started the investigation.
        """

        broken = [o for o in self.outcomes if o.status == BROKEN]
        return tuple(broken[:-1]) if len(broken) > 1 else ()

    @property
    def rung_path(self) -> tuple[str, ...]:
        return tuple(o.rung for o in self.outcomes)


def resolve_devices(
    rung: Rung,
    local_device: str,
    subject: str,
    resolver: Callable[[str], str | Sequence[str]] | None,
) -> list[str]:
    """Which device(s) this rung's check runs against.

    ``LOCAL`` needs no resolver. ``SUBJECT`` and ``PATH`` do, and a missing one
    is an error rather than a silent fallback to the local device -- falling
    back would produce exactly the wrong-device reading that Q-013 exists to
    prevent, and it would look like a healthy result.
    """

    if rung.device_scope is DeviceScope.LOCAL:
        return [local_device]

    if resolver is None:
        raise ValueError(
            f"rung {rung.name!r} has {rung.device_scope.value} scope but no resolver "
            "was supplied; refusing to fall back to the local device"
        )

    resolved = resolver(subject)
    if isinstance(resolved, str):
        return [resolved]
    devices = list(resolved)
    if not devices:
        raise ValueError(f"resolver returned no devices for subject {subject!r}")
    return devices


def _physical_interfaces(evidence: dict[str, Any]) -> list[str]:
    """Physical interfaces the device itself reported.

    Enumerated from observed evidence rather than guessed -- D7's rule that
    candidates are computed by code from what was actually seen, never named by
    a model. Subinterfaces are excluded; see `SubjectRule.EACH_PHYSICAL_INTERFACE`
    for the measurement behind that.
    """

    section = evidence.get("interfaces")
    if not isinstance(section, dict):
        return []
    parsed = section.get("data", {}).get("parsed") or {}
    # One declared taxonomy, not a name prefix -- see `interface_kind`. The
    # unclassified list is deliberately not discarded: silently excluding a name
    # nobody recognised is the defect this replaced (B-431, OBS-092).
    members, unclassified = physical_members(
        [record.get("interface", "") for record in parsed.get("records", [])]
    )
    if unclassified:
        LOGGER.warning(
            "interface names not classified by interface_kind, excluded from the "
            "member set: %s", ", ".join(unclassified),
        )
    return members


def path_interfaces(
    evidence: dict[str, Any], origin_prefix: str | None
) -> tuple[list[str], list[str]]:
    """``(members, unresolved)`` — the interfaces this device's path actually uses.

    Read from the route the device holds back toward ``origin_prefix``: every
    installed path names its outgoing interface, so the set is *observed* rather
    than inferred, which is D7's rule.

    **Two joins, and both are places this could go wrong.**

    The route prints ``GigabitEthernet0/0/0/0`` and ``show interfaces brief``
    prints ``Gi0/0/0/0``, so names are matched through
    `interface_kind.canonical` and the **device's own spelling** is returned —
    the check falls back to the bulk table by exact name, and handing it the
    route's spelling would silently miss.

    An interface the route names and the device's own interface list does not
    contain comes back in ``unresolved`` rather than being dropped. That is the
    same refusal `physical_members` makes: silently excluding a name nobody
    recognised is the defect `interface_kind` exists for.

    **This canonicalises within one device only.** The route must have been read
    on the same device as the interface list; comparing across devices produces
    a confident false match on a uniformly-named fabric (OBS-117).
    """

    if not origin_prefix:
        return [], []

    section = evidence.get(f"route:{origin_prefix}")
    if not isinstance(section, dict):
        return [], []
    parsed = (section.get("data") or {}).get("parsed") or {}
    if not (parsed.get("meta") or {}).get("found"):
        return [], []

    named = [r.get("interface", "") for r in parsed.get("records") or [] if r.get("interface")]

    own: list[str] = []
    bulk = (evidence.get("interfaces") or {}).get("data", {}).get("parsed") or {}
    if isinstance(bulk, dict):
        own = [r.get("interface", "") for r in bulk.get("records") or [] if r.get("interface")]

    members: list[str] = []
    unresolved: list[str] = []
    for route_name in named:
        match = next((o for o in own if same_interface(o, route_name)), None)
        if match is None:
            unresolved.append(route_name)
        elif match not in members:
            members.append(match)
    return members, unresolved


def _rung_subjects(
    rung: Rung, subject: str, evidence: dict[str, Any], origin_prefix: str | None = None
) -> tuple[list[str | None], Aggregation | None]:
    """``(subjects, aggregation override)`` for one rung.

    The override is ``None`` for every rule whose aggregation is fully declared
    on the :class:`Rung`. ``EACH_PATH_INTERFACE`` is the exception, and it is
    deliberate: it resolves to **two different member sets asking two different
    questions**, and the rule for combining them differs with the question. One
    aggregation declared on the rung would be right for one case and wrong for
    the other.
    """

    if rung.subject_rule is SubjectRule.AS_IS:
        return [subject], None
    if rung.subject_rule is SubjectRule.HOST_PREFIX:
        return [f"{subject}/32"], None
    if rung.subject_rule is SubjectRule.DEVICE_WIDE:
        return [None], None
    if rung.subject_rule is SubjectRule.EACH_PHYSICAL_INTERFACE:
        return list(_physical_interfaces(evidence)), None
    if rung.subject_rule is SubjectRule.EACH_PATH_INTERFACE:
        members, unresolved = path_interfaces(evidence, origin_prefix)
        if unresolved:
            LOGGER.warning(
                "route names interfaces absent from this device's own interface "
                "list, excluded from the path member set: %s", ", ".join(unresolved),
            )
        if members:
            # A path exists. The question is *does it survive*, and one healthy
            # member answers yes -- a primary down behind a healthy backup is a
            # degradation, not a broken path.
            return list(members), Aggregation.ANY_HEALTHY

        # **No reverse route, so no path -- and nothing to scope to.**
        #
        # Not a fallback to "check everything because the route was unreadable".
        # It is a *different question* becoming the right one. With a route back
        # to the origin, the question is which of this device's interfaces the
        # path uses, and a down port elsewhere is irrelevant (round 4). With no
        # route, the device is unreachable and the question is *why* -- for
        # which every physical interface is a candidate explanation.
        #
        # The aggregation flips with it, and that is the half a first
        # implementation missed. Measured on the `broken` label: `ANY_HEALTHY`
        # over PE2's three ports lets the one that is up outvote the two shut
        # uplinks, and the rung reports **healthy** on a completely isolated
        # device. "Does a path survive" and "is any port down" are not the same
        # question and cannot share a combining rule.
        #
        # The switch is on **observed evidence** -- did the reverse route
        # resolve -- never on an earlier rung's verdict, so `Flow`'s prewalk
        # precondition holds and both member sets are collectable up front.
        return list(_physical_interfaces(evidence)), Aggregation.ALL_HEALTHY
    raise ValueError(f"unhandled subject rule {rung.subject_rule!r}")


def _aggregate(
    rung: Rung, results: list[CheckResult], aggregation: Aggregation | None = None
) -> CheckResult:
    """Combine per-device verdicts for a scope that resolved to a set.

    ``unevaluated`` dominates in both aggregations: if one member could not be
    read, the set's verdict is not known either. That is the same rule as
    everywhere else -- absence is not health.
    """

    if not results:
        # No members resolved. Previously an `IndexError` from `results[0]`,
        # uncaught by the CLI and surfacing as a traceback (B-431). It is not a
        # crash and it is emphatically not a vacuous `healthy` -- `all([])` is
        # True, which would report a set nobody looked at as fine. Nothing was
        # read, so the verdict is `unevaluated`, per the module rule.
        return CheckResult(
            UNEVALUATED,
            reason=(
                f"rung {rung.name!r} resolved to no members; nothing was read, so "
                f"nothing is known about it"
            ),
        )

    if len(results) == 1:
        return results[0]

    if any(r.status == UNEVALUATED for r in results):
        unread = [r for r in results if r.status == UNEVALUATED]
        return CheckResult(
            UNEVALUATED,
            reason=f"{len(unread)} of {len(results)} members could not be read: "
            + "; ".join(filter(None, (r.reason for r in unread))),
            subject=results[0].subject,
            evidence_keys=tuple(k for r in results for k in r.evidence_keys),
        )

    keys = tuple(k for r in results for k in r.evidence_keys)
    healthy_count = sum(1 for r in results if r.status == HEALTHY)

    if (aggregation or rung.aggregation) is Aggregation.ANY_HEALTHY:
        status = HEALTHY if healthy_count else BROKEN
        reason = f"{healthy_count} of {len(results)} members healthy (any suffices)"
    else:
        status = HEALTHY if healthy_count == len(results) else BROKEN
        reason = f"{healthy_count} of {len(results)} members healthy (all required)"

    return CheckResult(status, reason=reason, subject=results[0].subject, evidence_keys=keys)


def evaluate_rung(
    rung: Rung,
    devices: Sequence[str],
    subject: str,
    evidence_for: Callable[[str], dict[str, Any]],
    origin_prefix: str | None = None,
) -> CheckResult:
    """One rung's verdict over its device set, aggregated.

    Extracted from :func:`run_descent` so the epoch's re-read evaluates a rung
    by *calling the walker's own logic* rather than reproducing it. Two copies
    of the fan-out-and-aggregate rule would agree until someone edited one, and
    then a re-read would compare a verdict against a differently-computed
    version of itself — which would read as instability in the fabric rather
    than as a bug here (B-431's lesson, applied before it could happen again).
    """

    per_device: list[CheckResult] = []
    aggregation: Aggregation | None = None
    for target in devices:
        evidence = evidence_for(target)
        rung_subjects, override = _rung_subjects(rung, subject, evidence, origin_prefix)
        aggregation = override or aggregation
        if not rung_subjects:
            # A fan-out that found no objects to check. Not healthy -- we
            # verified nothing -- and not broken either.
            per_device.append(
                CheckResult(
                    UNEVALUATED,
                    reason=f"no objects to check on {target} for rung {rung.name!r}",
                    subject=subject,
                )
            )
            continue
        for rung_subject in rung_subjects:
            per_device.append(rung.check(evidence, rung_subject))

    return _aggregate(rung, per_device, aggregation)


def run_descent(
    flow: Flow,
    device: str,
    subject: str,
    *,
    collector: Callable[[str, Rung, str], dict[str, Any]],
    resolver: Callable[[str], str | Sequence[str]] | None = None,
    coherence: Callable[[list[RungOutcome]], Coherence] | None = None,
    origin_prefix: str | None = None,
) -> DescentResult:
    """Walk one flow's ladder and return the lowest broken rung as the cause.

    ``collector(device, rung, subject) -> evidence`` gathers what a rung needs.
    It is injected so a descent runs against ``fixtures.load_fixture_evidence``
    with no lab access -- which is what makes the acceptance test possible
    offline, with no API key.

    ``resolver(subject) -> device | [device]`` maps a subject to the device(s)
    a non-``LOCAL`` rung is about. **It is the only inventory read in the
    descent path**, and it is arithmetic over inventory rather than an
    inference -- `10.255.0.12` is PE2 because the inventory says so, not
    because the numbers look alike.

    ``coherence(outcomes) -> Coherence`` re-reads the symptom and the proposed
    cause once the walk is done, and is consulted by :func:`_finding_for`. It is
    a **callable** rather than a value because it needs the walk's outcomes to
    know which rung the cause is, and the finding still has to be decided in one
    place. Omit it and the walk behaves exactly as it did before the epoch
    contract existed -- which is what every test injecting a ``collector``
    relies on.
    """

    outcomes: list[RungOutcome] = []
    evidence_keys: list[str] = []
    stopped_reason: str | None = None

    for rung in flow.descent:
        try:
            devices = resolve_devices(rung, device, subject, resolver)
        except ValueError as exc:
            outcome = RungOutcome(rung.name, device, CheckResult(UNEVALUATED, reason=str(exc)))
            outcomes.append(outcome)
            stopped_reason = str(exc)
            break

        result = evaluate_rung(
            rung, devices, subject,  # noqa: E128
            # `rung=rung` binds the loop variable. The lambda is consumed
            # inside this iteration, so late binding could not bite today --
            # but a future `evaluate_rung` that deferred the call would
            # silently evaluate every rung against the last one's evidence.
            lambda target, rung=rung: collector(target, rung, subject),
            origin_prefix=origin_prefix,
        )
        outcomes.append(RungOutcome(rung.name, ", ".join(devices), result))
        evidence_keys.extend(result.evidence_keys)

        if result.status == UNEVALUATED:
            # Stop. Nothing below a rung we could not read is trustworthy, and
            # a deeper finding would silently inherit the assumption.
            stopped_reason = result.reason
            break

        # broken -> keep descending. The lowest broken rung is the cause; this
        # one may only be a consequence of something further down.

    verdict = coherence(outcomes) if coherence is not None else None
    finding = _finding_for(flow, outcomes, verdict)

    return DescentResult(
        flow=flow.object_type,
        device=device,
        subject=subject,
        finding=finding,
        outcomes=tuple(outcomes),
        evidence_keys=tuple(dict.fromkeys(evidence_keys)),
        reason=stopped_reason,
        coherence=verdict,
    )


def _finding_for(
    flow: Flow,
    outcomes: list[RungOutcome],
    coherence: Coherence | None = None,
) -> str:
    """Turn the walk into one terminal finding."""

    if any(o.status == UNEVALUATED for o in outcomes):
        # `undetermined` outranks incoherence deliberately. Both are exit 2 --
        # "no trustworthy answer" -- so nothing is lost by ordering them, and
        # `undetermined` carries *which rung could not be read*, which is the
        # more actionable of the two. Replacing it would trade a specific reason
        # for a general one.
        return UNDETERMINED

    if coherence is not None and coherence.refuses:
        # `refuses`, not `not ok` -- B-454. Only a re-read *disagreement* (or no
        # re-read at all) forbids a finding: that is positive evidence that the
        # observations describe two states rather than one. A window merely
        # wider than the bound, with both ends agreeing, *qualifies* the finding
        # instead and travels as `Coherence.caveat`.
        #
        # Measured cost of not separating them: round 5 probes 10/11/99 saw a
        # settled broken fabric whose correct finding was `igp_isolated`, and the
        # bound replaced it with a refusal four minutes after the fabric stopped
        # changing (OBS-109).
        return TEMPORALLY_INCOHERENT

    broken = [o for o in outcomes if o.status == BROKEN]
    if not broken:
        return ALL_LAYERS_HEALTHY

    # B-428. Rung 1 is the symptom the investigation was called about. If it is
    # healthy there is no symptom, so nothing below it can be a cause -- the
    # broken rungs beneath are real, and simply not on the dependency path
    # between this device and this subject.
    #
    # Without this the walk reports its lowest broken rung as a cause on a
    # session that is up. Measured live at round 4 (OBS-094): one uplink shut on
    # a redundant device, IGP reconverged, BGP Established and carrying traffic,
    # reported as `interface_line_down` with `trustworthy: true` and exit 1.
    #
    # Only reached when something *is* broken -- the all-healthy case returns
    # above -- so this fires exactly on "broken, but not on the path".
    if outcomes[0].status == HEALTHY:
        return NO_FAULT_ON_PATH

    lowest = broken[-1]
    lowest_index = outcomes.index(lowest)

    # `cause_not_localised`: the only broken rung is the top one -- the symptom
    # itself -- and every rung beneath it is healthy. The descent confirmed
    # what it was asked about and found nothing underneath to explain it.
    # Reporting the top rung's finding here would restate the alert and dress
    # it up as a diagnosis; saying so plainly is the honest answer, and it is
    # not a failure. Deeper rungs keep their own findings, because "no route
    # while the IGP and interface are healthy" *is* localised -- to routing.
    if lowest_index == 0 and len(outcomes) > 1:
        return CAUSE_NOT_LOCALISED

    rung = flow.descent[lowest_index]
    return rung.finding
