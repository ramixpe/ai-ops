"""One observation window for one investigation — collect once, reuse, re-read.

Item 3 of the peer review's order of work; design in
`docs/design/evidence-epoch.md`, approved before this file was written.

What was wrong
--------------
Every rung called ``collect_evidence()`` afresh. For the ``bgp_session`` ladder
that is **five** collections across **two** devices — RR1 read three times, PE2
twice, for data that does not change between rungs. About 54% of the commands
were redundant.

The waste is not the defect; it is the *mechanism* of the defect. Each
re-collection observes a **different instant**, and the walker treats the
resulting five verdicts as one state. All three reviewers converged on the same
scenario:

.. code-block:: text

    t0    original BGP outage observed          <- rung 1 reads this
    t50   original fault recovers
    t105  an unrelated physical interface fails
    t115  the interface rung reads that new failure

Every citation resolves. The chain is deterministic. Grounding passes. The
report describes a state that never existed. Nothing in the build could catch
it, because nothing recorded *when* anything was read.

Skew does not establish coherence — the re-read does
-----------------------------------------------------
The reviewer rule is *"a causal finding may be asserted only when the
observations have a bounded, recorded skew and the symptom and proposed cause
remain stable across the observation interval."* Read as two thresholds it would
be theatre, and it is worth being precise about why.

**An interface can change state in under a second.** There is therefore no
non-zero skew that is provably safe: a 30-second bound and a 3-second bound
differ in how *likely* they are to hide a transition, not in whether they can.
Passing a run because its skew came in under a threshold would assert a
guarantee no threshold can supply.

So the work is split between the two halves:

* the **re-read** is the check — read the symptom and the proposed cause again
  at the end and compare the verdicts;
* the **bound** says what that two-point re-read is *worth*. Two agreeing reads
  across 20 seconds are strong evidence nothing moved. The same two reads across
  200 seconds are two samples from a window in which anything could have
  happened.

The bound does not certify the interval. It calibrates the only instrument that
says anything about the interval at all.

The skew is recorded when it passes, too
-----------------------------------------
A bound that only speaks when violated says nothing about how close we routinely
run. If real epochs land at 25s against a 30s bound, that is a finding — the
tool is one slow device away from refusing every answer and nobody would know
until it started. :class:`Coherence` therefore carries ``skew_seconds`` and the
bound on every outcome, the same discipline as ``coverage.gaps()`` and
``unaccounted_lines``: report the margin, not only the breach. A threshold only
ever observed at the moment it fails has no distribution behind it, and a limit
with no distribution can only be revised on argument.

What this does not fix
-----------------------
Stated here so the guarantee is not read as larger than it is.

* **The reads are not atomic** and nothing available over a CLI can make them
  so. The skew is bounded and recorded, not eliminated.
* **A fault flapping faster than the re-read interval still produces a
  coherent-looking vector.** Two agreeing samples do not prove the interval
  between them was quiet.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from . import flows
from .checks import CheckResult
from .descent import RungOutcome, evaluate_rung, resolve_devices
from .interface_kind import physical_members
from .network_tools import (
    collect_evidence,
    run_intent,
    run_templates_split,
)

__all__ = [
    "COHERENT",
    "DEFAULT_SKEW_BOUND_SECONDS",
    "FABRIC_MOVED",
    "UNVERIFIED",
    "WINDOW_LIMITED",
    "Coherence",
    "EvidenceEpoch",
    "Observation",
    "Reread",
    "check_coherence",
    "collect_epoch",
    "template_calls",
    "validate_prewalk_collection",
]

#: Default ceiling on an epoch's skew, in seconds.
#:
#: Derived rather than picked: it is the **fastest** protocol timer the
#: ``bgp_session`` ladder depends on (IS-IS hold, 30s default; BGP hold is 180s).
#: The fastest is the right one, because it is the shortest interval in which a
#: state change could complete unobserved between the first read and the last.
#:
#: It is a number with a reason attached, which is the difference between a
#: bound and a guess — and it is still a calibration, not a proof. See the module
#: docstring.
DEFAULT_SKEW_BOUND_SECONDS = 30.0


# --------------------------------------------------------------------------- #
# The prewalk precondition
# --------------------------------------------------------------------------- #

#: How a template's parameter is filled, declared rather than inferred.
#:
#: Enumerating these is the point. The code this replaced ended in an ``else``
#: that filled *any* unrecognised parameter with the raw subject — correct for
#: ``address`` and silently wrong for the first parameter that is not an
#: address, e.g. a VRF name. Same discipline as `template_parsers.IgnoreRule`,
#: `log_window.NoiseRule` and `interface_kind`: a declared table a reviewer can
#: read, and a hard failure on anything not in it.
SUBJECT_PARAMETERS = frozenset({"address"})
#: Filled with ``<subject>/32`` — a host route for the peer's loopback.
PREFIX_PARAMETERS = frozenset({"prefix"})
#: Fans out over the physical interfaces the device itself reported.
FANOUT_PARAMETERS = frozenset({"interface"})

_RESOLVABLE = SUBJECT_PARAMETERS | PREFIX_PARAMETERS | FANOUT_PARAMETERS


def validate_prewalk_collection(flow: flows.Flow) -> None:
    """Raise unless every collect step is resolvable before the walk begins.

    The precondition an epoch rests on, stated on :class:`flows.Flow` where a
    flow author meets it and enforced here.

    A single epoch is only sound because nothing a rung collects depends on what
    an *earlier rung concluded* — the identity of every command is fixed by the
    subject and the device. No flow violates that today and ``SubjectRule``
    offers no way to express it, so this is a tripwire for the future rather
    than a live guard. It is still worth having: the failure it prevents is an
    epoch that quietly collects the wrong object, which reads as a healthy
    answer about something nobody asked about.
    """

    for rung in flow.descent:
        for step in rung.collect:
            if not step.is_template:
                continue
            if step.parameter in _RESOLVABLE:
                continue
            raise ValueError(
                f"flow {flow.object_type!r} rung {rung.name!r} collects template "
                f"{step.name!r} with parameter {step.parameter!r}, which the epoch "
                f"builder cannot resolve before the walk. Known parameters: "
                f"{sorted(_RESOLVABLE)}. Teach `epoch.py` how to fill it, or the "
                f"rung must not be collected in a single pass."
            )


# --------------------------------------------------------------------------- #
# The data
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Observation:
    """One command's result, and when it was read.

    ``started``/``completed`` are **monotonic** — immune to an NTP step, which a
    wall clock is not, and this is the one place in the build that does
    arithmetic on time. The device's own wall-clock stamp is not lost: it is
    still inside ``envelope["timestamp"]``, untouched.

    Every intent collected in one session shares one ``started``/``completed``
    pair, because they really were one read.
    """

    key: str
    device: str
    started: float
    completed: float
    envelope: dict

    @property
    def duration(self) -> float:
        return self.completed - self.started


@dataclass(frozen=True)
class EvidenceEpoch:
    """Everything read for one investigation, and the window it was read in."""

    observations: tuple[Observation, ...] = ()
    opened: float = 0.0
    closed: float = 0.0
    bound_seconds: float = DEFAULT_SKEW_BOUND_SECONDS

    @property
    def skew_seconds(self) -> float:
        """How wide the observation window was.

        Not a measure of coherence — see the module docstring. It is the size of
        the interval in which an unobserved transition could hide.
        """

        return self.closed - self.opened

    @property
    def within_bound(self) -> bool:
        return self.skew_seconds <= self.bound_seconds

    def for_device(self, device: str) -> dict[str, Any]:
        """This device's evidence, in **exactly** the shape ``checks.py`` reads.

        The whole contract turns on this method returning today's dict. No check
        changed for the epoch, no evidence key changed, and that is deliberate:
        it keeps this a change to *when* evidence is gathered rather than to what
        a check sees. Every pre-existing test passing untouched is the evidence
        that the shape held.
        """

        return {o.key: o.envelope for o in self.observations if o.device == device}

    @property
    def devices(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(o.device for o in self.observations))

    def as_dict(self) -> dict:
        return {
            "skew_seconds": round(self.skew_seconds, 3),
            "bound_seconds": self.bound_seconds,
            "within_bound": self.within_bound,
            "devices": list(self.devices),
            "observations": len(self.observations),
        }


@dataclass(frozen=True)
class Reread:
    """One rung, read twice: at collection, and again at the end."""

    rung: str
    device: str
    before: str
    after: str
    detail: str | None = None

    @property
    def agrees(self) -> bool:
        return self.before == self.after

    def as_dict(self) -> dict:
        return {
            "rung": self.rung, "device": self.device,
            "before": self.before, "after": self.after,
            "agrees": self.agrees, "detail": self.detail,
        }


#: The observations bracket a stable window inside the bound. Nothing to say.
COHERENT = "coherent"
#: The re-read **disagreed**: the fabric moved while the ladder was being walked.
#: The observations do not describe one state, so no finding can be asserted
#: from them. Refusal.
FABRIC_MOVED = "fabric_moved"
#: The window was wider than the bound and **every re-read agreed**. The
#: observations bracket a stable interval; what is missing is confidence about
#: the middle of it. Qualification, not refusal — see :class:`Coherence`.
WINDOW_LIMITED = "window_limited"
#: No re-read was performed at all, so stability was never checked. Refusal, for
#: the same reason ``_aggregate`` refuses to call an empty member set healthy.
UNVERIFIED = "unverified"


@dataclass(frozen=True)
class Coherence:
    """Whether this epoch's observations support one present-tense claim.

    Two failures, not one (B-454, found by round 5)
    ------------------------------------------------
    The first version collapsed *the fabric moved* and *collection was slow*
    into a single refusal, and round 5 measured what that costs. Probe 09 was
    incoherent because rung 1 flipped between the walk and the re-read — the
    real signal. Probes 08, 10, 11 and 99 were incoherent because collection
    took 34–38 s against a 30 s bound, **with every re-read agreeing**. Same
    finding, same exit code, and an operator could not tell them apart.

    The cost was concrete: probes 10, 11 and 99 saw a settled, fully converged
    broken fabric whose lowest broken rung was `igp_adjacency`. **The correct
    answer was `igp_isolated` and the bound threw it away**, four minutes after
    the fabric had stopped changing.

    So they are separated, and the separation follows a pattern this build
    already had and had not applied here. `investigation.COVERAGE_LIMITED` keeps
    a correlation and labels it, on the reasoning that *a real answer at the
    wrong strength* is worth more than no answer. A width breach is that shape:

    ================  =========================================================
    :data:`FABRIC_MOVED`   a re-read disagreed. **Refuse** — the observations do
                           not describe one state, and any finding built from
                           them is about a fabric that never existed
    :data:`WINDOW_LIMITED` the window was wide and every re-read agreed.
                           **Keep the finding, carry the caveat** — two agreeing
                           reads *bracket* a stable interval; the uncertainty is
                           about the middle, not about the endpoints
    ================  =========================================================

    The asymmetry is the point. Disagreement is positive evidence that the
    premise of a causal claim is false. Width is only absence of evidence about
    an interval whose endpoints both looked the same, and absence of evidence is
    exactly what this layer refuses to convert into a verdict anywhere else.
    """

    skew_seconds: float
    bound_seconds: float
    rereads: tuple[Reread, ...] = ()

    @property
    def within_bound(self) -> bool:
        return self.skew_seconds <= self.bound_seconds

    @property
    def stable(self) -> bool:
        """Did every re-read agree with what the epoch recorded?

        **An empty re-read set is not stable.** Nothing was checked, so nothing
        is known — the same rule as ``unevaluated`` everywhere else in this
        layer, and the same rule as ``_aggregate`` refusing to call an empty
        member set healthy. ``all([])`` being ``True`` is exactly the trap.
        """

        return bool(self.rereads) and all(r.agrees for r in self.rereads)

    @property
    def status(self) -> str:
        """Which of the four outcomes this is. The load-bearing property."""

        if not self.rereads:
            return UNVERIFIED
        if not all(r.agrees for r in self.rereads):
            return FABRIC_MOVED
        return COHERENT if self.within_bound else WINDOW_LIMITED

    @property
    def refuses(self) -> bool:
        """Whether this outcome forbids a finding, as opposed to qualifying one."""

        return self.status in (FABRIC_MOVED, UNVERIFIED)

    @property
    def ok(self) -> bool:
        return self.status == COHERENT

    @property
    def caveat(self) -> str | None:
        """The qualification a :data:`WINDOW_LIMITED` result must carry.

        A field rather than something a renderer is trusted to add, for the same
        reason `InvestigationResult.caveat` is: a qualified answer printed
        without its qualification is just an answer.

        Explicitly **not** a qualification of the fabric's state. Both reads
        agreed; what is unestablished is the interval between them.
        """

        if self.status != WINDOW_LIMITED:
            return None
        agreed = ", ".join(r.rung for r in self.rereads)
        return (
            f"The finding stands. It was read over {self.skew_seconds:.1f}s, wider "
            f"than the {self.bound_seconds:.1f}s bound, and {agreed} were unchanged at "
            f"both ends of that window. Read it as 'true at the start and at the end', "
            f"not as 'true throughout' — nothing observed the middle."
        )

    @property
    def detail(self) -> str:
        """Why, in one line, whatever the outcome.

        Populated on a pass as well as a failure — see §2.3a of the design.
        """

        margin = f"skew {self.skew_seconds:.1f}s of {self.bound_seconds:.0f}s allowed"
        if not self.rereads:
            return f"{margin}; nothing was re-read, so stability is unknown"
        moved = [r for r in self.rereads if not r.agrees]
        if moved:
            changed = "; ".join(f"{r.rung} on {r.device}: {r.before} -> {r.after}" for r in moved)
            return f"{margin}; state changed during the walk: {changed}"
        agreed = ", ".join(r.rung for r in self.rereads)
        if not self.within_bound:
            return (
                f"{margin} — EXCEEDED; {agreed} agreed at both ends, so the finding "
                f"stands qualified: the window brackets a stable interval, and nothing "
                f"observed its middle"
            )
        return f"{margin}; {agreed} unchanged on re-read"

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "ok": self.ok,
            "refuses": self.refuses,
            "caveat": self.caveat,
            "skew_seconds": round(self.skew_seconds, 3),
            "bound_seconds": self.bound_seconds,
            "within_bound": self.within_bound,
            "stable": self.stable,
            "detail": self.detail,
            "rereads": [r.as_dict() for r in self.rereads],
        }


# --------------------------------------------------------------------------- #
# Collection
# --------------------------------------------------------------------------- #


def template_calls(
    step: flows.CollectStep, subject: str, evidence: dict[str, Any]
) -> Iterator[tuple[str, dict[str, str]]]:
    """``(evidence_key, kwargs)`` for every call one template step needs.

    One definition, used by both the epoch build and the re-read. Writing the
    fan-out rule twice is what B-431 was about: a filter defined twice is two
    filters that agree until someone edits one.
    """

    if step.parameter in PREFIX_PARAMETERS:
        key = f"{subject}/32"
        yield f"{step.name}:{key}", {"prefix": key}
        return

    if step.parameter in FANOUT_PARAMETERS:
        parsed = (evidence.get("interfaces") or {}).get("data", {}).get("parsed") or {}
        # The same declared taxonomy the descent aggregates over. Collecting one
        # member set and aggregating over another is what three copies of this
        # rule made possible (B-431).
        members, _ = physical_members(
            [r.get("interface", "") for r in parsed.get("records", [])]
        )
        for name in members:
            yield f"{step.name}:{name}", {"interface": name}
        return

    if step.parameter in SUBJECT_PARAMETERS:
        yield f"{step.name}:{subject}", {step.parameter: subject}
        return

    raise ValueError(  # pragma: no cover -- validate_prewalk_collection runs first
        f"template step {step.name!r} has unresolvable parameter {step.parameter!r}"
    )


def _plan(
    flow: flows.Flow,
    device: str,
    subject: str,
    resolver: Callable[[str], str | Sequence[str]] | None,
) -> dict[str, list[flows.CollectStep]]:
    """``{device: [template steps it needs]}``, in first-touched order.

    Every device the flow will touch, resolved before anything is collected.
    That resolution is the same :func:`descent.resolve_devices` the walk uses —
    if the two disagreed, the epoch would collect one device and the walk would
    evaluate another.
    """

    plan: dict[str, list[flows.CollectStep]] = {}
    for rung in flow.descent:
        for target in resolve_devices(rung, device, subject, resolver):
            steps = plan.setdefault(target, [])
            steps.extend(s for s in rung.collect if s.is_template and s not in steps)
    return plan


def collect_epoch(
    flow: flows.Flow,
    device: str,
    subject: str,
    *,
    resolver: Callable[[str], str | Sequence[str]] | None = None,
    sender=None,
    bound_seconds: float = DEFAULT_SKEW_BOUND_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> EvidenceEpoch:
    """Collect everything this flow needs, once per device.

    One ``collect_evidence`` session per device carries the intents; templates
    follow on the same device. The intent bundle stays one session deliberately —
    IOS-XR rate-limits repeated logins, which is why ``collect_evidence`` exists
    at all, and splitting it to fetch only the three intents this flow reads
    would trade 4 wasted commands for 2 extra logins.
    """

    validate_prewalk_collection(flow)

    observations: list[Observation] = []
    opened = clock()

    for target, steps in _plan(flow, device, subject, resolver).items():
        started = clock()
        evidence = dict(collect_evidence(target, sender=sender))
        completed = clock()
        observations.extend(
            Observation(key, target, started, completed, envelope)
            for key, envelope in evidence.items()
        )

        # Every template for this device in **one** session, not one login each.
        # Measured before this was batched: 7 logins per epoch, 5 of them to run
        # a single command, ~10s per login, a 61s window against a 30s bound --
        # so the tool refused to answer about a healthy fabric (OBS-109). Skew is
        # dominated by login count, and the design said "one pass per device, in
        # a single session" before the first version failed to do it.
        keys: list[str] = []
        manifest: list[tuple[str, dict[str, str]]] = []
        for step in steps:
            for key, kwargs in template_calls(step, subject, evidence):
                keys.append(key)
                manifest.append((step.name, kwargs))

        if manifest:
            begun = clock()
            envelopes = run_templates_split(target, manifest, sender=sender)
            ended = clock()
            observations.extend(
                Observation(key, target, begun, ended, envelope)
                for key, envelope in zip(keys, envelopes, strict=True)
            )

    return EvidenceEpoch(
        observations=tuple(observations),
        opened=opened,
        closed=clock(),
        bound_seconds=bound_seconds,
    )


# --------------------------------------------------------------------------- #
# The re-read
# --------------------------------------------------------------------------- #


def _collect_one_rung(device: str, rung: flows.Rung, subject: str, *, sender=None) -> dict:
    """Only what this rung needs — not a whole evidence collection.

    The re-read's cost has to stay small or it becomes the thing it is checking:
    a second wide observation window, with its own skew, appended to the first.
    So this runs the rung's own intents and templates and nothing else.
    """

    evidence: dict[str, Any] = {
        step.name: run_intent(device, step.name, sender=sender)
        for step in rung.collect
        if not step.is_template
    }

    keys: list[str] = []
    manifest: list[tuple[str, dict[str, str]]] = []
    for step in rung.collect:
        if not step.is_template:
            continue
        for key, kwargs in template_calls(step, subject, evidence):
            keys.append(key)
            manifest.append((step.name, kwargs))

    # One session for the batch, for the same reason as the epoch build: the
    # interface rung fans out over every physical member, and a login each would
    # make the re-read cost more than the walk it is checking.
    if manifest:
        for key, envelope in zip(
            keys, run_templates_split(device, manifest, sender=sender), strict=True
        ):
            evidence[key] = envelope

    return evidence


def _rung_named(flow: flows.Flow, name: str) -> flows.Rung | None:
    return next((r for r in flow.descent if r.name == name), None)


def check_coherence(
    flow: flows.Flow,
    outcomes: Sequence[RungOutcome],
    epoch: EvidenceEpoch,
    subject: str,
    *,
    device: str,
    resolver: Callable[[str], str | Sequence[str]] | None = None,
    sender=None,
) -> Coherence:
    """Re-read the symptom and the proposed cause, and compare.

    **Two rungs, not five.** The symptom is rung 1, the reason the investigation
    was called at all; the proposed cause is the lowest broken rung. Those are
    the two the reviewers' scenario moves — the symptom recovering, or the cause
    appearing late — and they are the two the report's causal claim is built
    from. Re-reading every rung would double the descent's cost and re-introduce
    the skew problem inside the re-read itself.

    A re-read that *fails* (device unreachable, command errored) does not agree
    with anything, so it makes the epoch incoherent rather than silently
    dropping out. Stability is a positive claim and it needs a positive
    observation.
    """

    coherence = Coherence(epoch.skew_seconds, epoch.bound_seconds)
    if not outcomes:
        return coherence

    broken = [o for o in outcomes if o.status == "broken"]
    targets = [outcomes[0]]
    if broken and broken[-1].rung != outcomes[0].rung:
        targets.append(broken[-1])

    rereads: list[Reread] = []
    for outcome in targets:
        rung = _rung_named(flow, outcome.rung)
        if rung is None:  # pragma: no cover -- outcomes come from flow.descent
            continue
        try:
            devices = resolve_devices(rung, device, subject, resolver)
            fresh = {
                d: _collect_one_rung(d, rung, subject, sender=sender) for d in devices
            }
            result: CheckResult = evaluate_rung(rung, devices, subject, fresh.__getitem__)
            after, detail = result.status, result.reason
        except Exception as exc:  # noqa: BLE001 -- the SSH-boundary idiom
            # Not "assume unchanged". A re-read we could not perform is a
            # stability claim we cannot make, and a sentinel that can never
            # equal `before` is how that stays true without a special case.
            after, detail = "reread_failed", str(exc)

        rereads.append(Reread(outcome.rung, outcome.device, outcome.status, after, detail))

    return Coherence(epoch.skew_seconds, epoch.bound_seconds, tuple(rereads))
