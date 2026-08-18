"""Object-indexed flow definitions — the ladders a descent walks.

A *flow* is an investigation scoped to one **object type**. There are seven,
not hundreds, because flows are indexed by object rather than by symptom (D5):
`bgp-down`, `bgp-flapping` and `bgp-wrong-prefix-count` are not three flows,
they are three terminal findings inside one. The test for any proposed flow is
"is this an object, or a symptom of one?"

This module is declarative. It holds the ladders and the registry; walking them
is `descent.py`'s job, and evaluating a rung is `checks.py`'s. Nothing here
touches a device, and nothing here calls a model.

Device scope — why a rung carries one (Q-013)
----------------------------------------------
`run_descent(flow, device, subject)` takes **one** device, but a
`bgp_session` ladder is not about one device below its top rungs. Measured on
the `broken` label for `RR1 → 10.255.0.12`:

| Rung | On RR1 (the local device) | On PE2 (the subject's device) |
|---|---|---|
| `bgp_session` | broken | — |
| `transport` | broken | — |
| `route_to_peer` | broken | — |
| `igp_adjacency` | **healthy** | **broken** |
| `interface` | *which interface?* | **broken** |

Read locally the descent finds nothing below `route_to_peer` and cannot reach
the cause. So each :class:`Rung` declares its own :class:`DeviceScope`, and the
walker resolves it. The alternative — inferring scope from the check's name —
is the kind of implicit rule that is correct until the day someone adds a rung.

**Subject resolution is arithmetic over the inventory, never an inference.**
`10.255.0.12` is PE2 because the inventory says so, exactly as CE2 attaches to
PE3 because the /31 addressing says so and *not* because the numbers look
alike (T-006, OBS-024 — this fabric punishes name-pattern matching twice out of
four). The resolver lives beside this registry, is injected into the walker,
and is the only thing in the descent path that reads the inventory.

Aggregation — why a `path` scope needs one
-------------------------------------------
A `PATH` scope resolves to a **set** of devices, and a set needs a declared
rule for combining verdicts. The right rule is not the same for every rung and
must not be left implicit in the check:

* `route_to_peer` over an ECMP set is **`ANY_HEALTHY`** — one usable path is
  enough, and demanding all of them would report a healthy fabric as broken
  every time a backup path was down.
* `interface` over the hops of a single path is **`ALL_HEALTHY`** — every hop
  has to be up for the path to carry traffic.

Declaring it on the `Rung` keeps the check a pure predicate over one object and
puts the fan-out rule where a reviewer can see it next to the ladder.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum

from . import checks as _checks
from .checks import CheckResult

__all__ = [
    "FLOWS",
    "OBJECT_TYPES",
    "Aggregation",
    "CollectStep",
    "DeviceScope",
    "Flow",
    "Rung",
    "SubjectRule",
    "flow_for",
]


class DeviceScope(Enum):
    """Which device a rung's check runs against."""

    #: The device the investigation was launched from -- the one holding the
    #: session, the RIB entry, the local socket.
    LOCAL = "local_device"

    #: The device the *subject* resolves to. `RR1 -> 10.255.0.12` makes PE2 the
    #: subject device, and PE2 is where the IGP and interface faults live.
    SUBJECT = "subject_device"

    #: Every device on the path between the two. Resolves to a set, so a rung
    #: using it must declare an `Aggregation`.
    PATH = "path"


class SubjectRule(Enum):
    """What a rung's check receives as its subject.

    A ladder crosses subject vocabularies. `bgp_session` is about a peer
    address, `route_to_peer` about a prefix, `igp_adjacency` about a whole
    device, `interface` about an interface name -- and an interface name is not
    derivable from a peer address at all. Left implicit, each rung's collector
    and its check would each guess a transformation and the two would disagree
    silently, which is exactly what happened on the walker's first run.

    Declared on the rung, for the same reason `Aggregation` is: explicit beats
    implied by the check.
    """

    #: Pass the descent subject through unchanged (a peer address).
    AS_IS = "as_is"

    #: `<subject>/32` -- a host route for the peer's loopback.
    HOST_PREFIX = "host_prefix"

    #: The rung is about the device, not an object on it; the check gets `None`.
    DEVICE_WIDE = "device_wide"

    #: Fan out over the interfaces the **path actually uses**, one check each,
    #: taken from the route this device holds back toward the investigation's
    #: origin (B-456).
    #:
    #: `EACH_PHYSICAL_INTERFACE` asks about every port on the device, which is
    #: why a down port unrelated to the session can break the rung -- round 4,
    #: and the only route by which `no_fault_on_path` is reachable at all. This
    #: asks about the ports the traffic would take.
    #:
    #: **The route must be read on the subject device, not the local one.** The
    #: local device's route names the *local* device's egress, and on a fabric
    #: with uniform naming the two sets collide by name while describing
    #: different routers (OBS-117).
    #:
    #: Pairs with `Aggregation.ANY_HEALTHY`: one usable path is a usable path.
    #: A primary down with a healthy backup is a real degradation and the
    #: three-value status vocabulary cannot say so -- it surfaces in the rung's
    #: reason as "1 of 2 members healthy" rather than being forced into
    #: `broken`.
    EACH_PATH_INTERFACE = "each_path_interface"

    #: Fan out over the device's *physical* interfaces, one check each.
    #:
    #: Physical only, and that exclusion is measured rather than tidy:
    #: PE1 and PE3 each carry a `Gi0/0/0/2.300` subinterface that is
    #: legitimately line-down on a completely healthy fabric. Including
    #: subinterfaces would make both devices report broken in the `healthy`
    #: label -- a false positive on 2 of 9 devices. A subinterface being down
    #: is a service condition; a physical link being down is a path condition,
    #: and the path is what a descent is about.
    EACH_PHYSICAL_INTERFACE = "each_physical_interface"

    @property
    def is_fanout(self) -> bool:
        return self in (
            SubjectRule.EACH_PHYSICAL_INTERFACE,
            SubjectRule.EACH_PATH_INTERFACE,
        )


class Aggregation(Enum):
    """How to combine per-device verdicts when a scope resolves to a set."""

    #: Every device must be healthy. Broken if any is broken. Use for things
    #: that must hold end to end -- every hop of a path carrying traffic.
    ALL_HEALTHY = "all_must_be_healthy"

    #: One healthy device is enough. Use where redundancy is the design: an
    #: ECMP route set is fine while any member resolves.
    ANY_HEALTHY = "any_healthy_suffices"


@dataclass(frozen=True)
class CollectStep:
    """One piece of evidence a rung needs before its check can run.

    ``name`` is an intent (``"bgp"``) or a template (``"bgp_neighbor"``).
    ``parameter`` names the template argument the subject fills, or is ``None``
    for an intent, which takes none. The evidence key the check later reads is
    built from these — see ``checks.py``'s evidence-key convention.
    """

    name: str
    parameter: str | None = None
    is_template: bool = False
    #: **How** the parameter is filled, as distinct from *which* parameter it
    #: is. `parameter` names a real template argument and is validated against
    #: the template's own signature; `fill` names the strategy the collector
    #: uses to produce a value for it.
    #:
    #: Conflating the two is a mistake worth naming, because it type-checks:
    #: a first implementation of `EACH_PATH_INTERFACE` set
    #: `parameter="origin_prefix"`, which reads naturally and is false -- the
    #: `route` template takes `prefix`, and `origin_prefix` is a statement about
    #: where the value comes from. `test_every_template_collect_step_names_a
    #: _real_parameter` caught it.
    #:
    #: ``None``       fill from the descent subject (the default)
    #: ``"origin"``   fill from the *origin* device's loopback prefix -- the
    #:                route the subject device holds back toward where the
    #:                investigation started (B-456)
    fill: str | None = None


@dataclass(frozen=True)
class Rung:
    """One layer of a descent.

    ``aggregation`` is required when ``device_scope`` is ``PATH`` and
    meaningless otherwise; that is enforced in ``__post_init__`` rather than
    left to a convention, because a `PATH` rung with no declared aggregation
    would silently pick one.

    What makes a rung a rung (B-437, reviewer A §2.2)
    --------------------------------------------------
    Rungs are easy to define by *available CLI view* — one per `show` command
    that seemed relevant — and that is not a ladder, it is a menu. A rung is a
    **falsifiable dependency hypothesis**, and four things make it one:

    1. **a dependency assertion** — "the layer above cannot work unless this
       one does", stated, not implied by ordering;
    2. **an observation from a distinct subsystem** — otherwise the rung
       re-reads its neighbour and cannot disagree with it. Rungs 1 and 2 both
       concern BGP and are distinct because rung 2 reads the **TCP socket**
       (B-432), not the FSM;
    3. **a separating case in the evidence base** — at least one captured or
       injected fault where this rung is broken and the one below it healthy,
       proving the boundary is real rather than notional;
    4. **the upper-layer signature** this rung alone should produce, so a
       descent can check forward consistency rather than only downward.

    **A rung whose observable is a *prerequisite* for the rung above it cannot be
    separated from it by a stable fault.** Check this when designing a rung, not
    when a round fails to separate it.

    Rungs 1 and 2 are the case. Rung 1 reads whether BGP is Established; rung 2
    reads whether a TCP socket to the peer is armed. **TCP up is necessary for
    Established**, so any *stable* fault breaks both or neither:

    ==========================  =========  ==================
    fault                       TCP        BGP
    ==========================  =========  ==================
    admin shutdown (round 3)    down       Idle
    MD5 mismatch                down       Idle
    route-policy denying all    up         Established
    ==========================  =========  ==================

    The only states with TCP up and BGP not Established are ``OpenSent`` and
    ``OpenConfirm``, which last seconds, plus the retry window of a peer whose
    OPEN is rejected. So the separating case exists and is **transient**.

    This explains round 3 rather than excusing it. Round 3 was meant to separate
    the boundary and produced ``BBHHH``; the reason is not that the fault was
    badly chosen but that an administrative shutdown tears down the transport,
    and **no fault that breaks what rung 2 observes can leave rung 2 healthy**.

    **Binding on every rung added from here.** `test_rungs.py` pins (3) as an
    audit over every boundary in every flow, so a new rung without separating
    evidence fails rather than being noticed later — the audit is the enforcement
    and this paragraph is the reason.

    Point (4) is deliberately not enforced yet: it is forward consistency, which
    is **Q-019**'s open question, and duplicating it here would be two items
    answering one question badly.

    The member set and the aggregation are one decision
    ----------------------------------------------------
    **A rule that changes what a rung is evaluated over must state how those
    results combine, in the same place.** They are not two settings; they are
    two halves of one question, and the combining rule is part of what the
    member set *means*:

    ========================  ==========================================
    path-scoped               ``ANY_HEALTHY`` — "does a path survive?", and
                              one healthy member answers yes
    device-wide               ``ALL_HEALTHY`` — "is any port down, explaining
                              this?", which one healthy member does not answer
    ========================  ==========================================

    Measured cost of separating them (OBS-119): `EACH_PATH_INTERFACE` was first
    implemented switching the member set to every physical interface when no
    path existed, while leaving `ANY_HEALTHY` in place from the rung's
    declaration. PE2's one healthy port then outvoted its two shut uplinks and
    the rung reported **healthy on a completely isolated device** — a confident
    wrong answer in the direction that matters, produced by changing one half of
    a decision.

    So a `SubjectRule` that resolves to more than one member set returns its
    aggregation with each of them (`descent._rung_subjects`), and
    `test_flows.py` asserts the **pairing** rather than either half — a test of
    "the aggregation is ANY_HEALTHY" and a test of "the member set is
    path-scoped" both pass in the state that produced the defect.
    """

    name: str
    collect: tuple[CollectStep, ...]
    check: Callable[..., CheckResult]
    finding: str
    device_scope: DeviceScope = DeviceScope.LOCAL
    subject_rule: SubjectRule = SubjectRule.AS_IS
    aggregation: Aggregation | None = None

    @property
    def evaluates_a_set(self) -> bool:
        """Whether this rung produces more than one verdict to combine.

        Two independent ways that happens: the scope resolves to several
        devices, or the subject rule fans out over several objects on one
        device. Either needs a declared aggregation.
        """

        return self.device_scope is DeviceScope.PATH or self.subject_rule.is_fanout

    def __post_init__(self) -> None:
        if self.evaluates_a_set and self.aggregation is None:
            raise ValueError(
                f"rung {self.name!r} evaluates over a set "
                f"(scope={self.device_scope.value}, subject={self.subject_rule.value}), "
                "so it must declare an aggregation (ALL_HEALTHY or ANY_HEALTHY)"
            )
        if not self.evaluates_a_set and self.aggregation is not None:
            raise ValueError(
                f"rung {self.name!r} declares an aggregation but evaluates exactly "
                f"one object on one device (scope={self.device_scope.value}, "
                f"subject={self.subject_rule.value})"
            )


@dataclass(frozen=True)
class Flow:
    """An investigation scoped to one object type.

    Precondition on every flow — collection must be resolvable before the walk
    ---------------------------------------------------------------------------
    **Every collect step in every rung must be resolvable from the subject and
    the device alone, before the walk begins. A rung may not collect something
    whose identity depends on what an earlier rung concluded.**

    This is stated here, on the type a flow author is writing, rather than only
    as a guard inside the collector. A guard tells you the constraint exists
    after you have designed a ladder that violates it, and tells you in a stack
    trace; a precondition tells you before. `epoch.validate_prewalk_collection`
    enforces it, as the enforcement of a stated rule rather than as the only
    place the rule appears.

    Why it matters: evidence is collected **once per device** in a single
    observation window, and the window is what makes a causal finding assertable
    at all (`epoch.py`). A rung whose collection depended on an earlier verdict
    could not be collected in that window, so the flow would silently fall back
    to reading each rung at a different instant -- the defect the epoch exists
    to remove.

    No flow violates it today, and ``SubjectRule`` offers no way to express such
    a dependency. That is a property to preserve, not a coincidence to rely on.
    """

    object_type: str
    subject_schema: str
    descent: tuple[Rung, ...]
    findings: frozenset[str]

    #: Does the subject this investigation names actually exist on the device?
    #:
    #: **B-453 pointed the other way.** B-453 checks that every identifier in a
    #: model's *output* appears in the evidence; this checks that the identifier
    #: in its *input* appears on the device, before anything is walked. Same
    #: direction of suspicion, opposite end of the pipeline.
    #:
    #: Declared per flow because "does this subject exist" is a different
    #: question per object type -- a peer address is looked for in the BGP
    #: summary, an interface name in the interface list -- and inferring it from
    #: the object type's name is the implicit rule this registry exists to
    #: avoid.
    #:
    #: ``None`` means the flow has not declared one, which is a *gap*, not a
    #: pass: `test_flows.py` requires every implemented flow to declare it.
    subject_present: Callable[..., CheckResult] | None = None

    def __post_init__(self) -> None:
        declared = {rung.finding for rung in self.descent}
        missing = declared - self.findings
        if missing:
            raise ValueError(
                f"flow {self.object_type!r} has rungs whose findings are not in its "
                f"declared finding set: {sorted(missing)}"
            )


# --------------------------------------------------------------------------- #
# Universal terminal findings
# --------------------------------------------------------------------------- #

#: Every rung walked, none broken.
ALL_LAYERS_HEALTHY = "all_layers_healthy"

#: A rung could not be read, so the walk stopped. Nothing below an unread rung
#: is trustworthy.
UNDETERMINED = "undetermined"

#: Rungs broken above, everything healthy below -- nothing beneath to explain
#: them. An honest answer, not a failure (Q-017).
CAUSE_NOT_LOCALISED = "cause_not_localised"

#: The subject does not exist on this device (B-459).
#:
#: **Not `undetermined`.** That means *a rung could not be read*, and reading it
#: is exactly what succeeded here: the device answered, and what it said is that
#: it has no such object. A caller told `undetermined` retries; a caller told
#: `subject_not_found` corrects the question.
#:
#: This is the finding that closes the argument-fabrication gap. Every
#: containment mechanism in this build operates on what a tool *returns*;
#: nothing constrained what a model *supplies*. A fabricated peer address walks
#: the whole ladder, cites real evidence keys, passes grounding, and produces a
#: fully sourced investigation of a session that does not exist -- with nothing
#: malfunctioning anywhere, because every component did its job on the input it
#: was given.
SUBJECT_NOT_FOUND = "subject_not_found"

#: Rung 1 is healthy, and something *below* it is broken. There is no symptom
#: to explain, so nothing beneath can be its cause -- the broken rungs are real
#: observations about the device and are simply not on the dependency path
#: between this device and this subject (B-428, OBS-094).
#:
#: The walk finds the lowest broken thing, which is the right rule when there is
#: something to explain and the wrong one when there is not. Measured live: one
#: uplink shut on a device with two, the IGP reconverging over the survivor, and
#: the session Established and carrying traffic throughout -- reported as
#: `interface_line_down` with exit code 1 until this finding existed.
NO_FAULT_ON_PATH = "no_fault_on_path"

#: The observations do not support one present-tense claim about the fabric.
#:
#: Either the observation window was wider than the bound, or the symptom or the
#: proposed cause changed between being read and being re-read at the end. The
#: rungs were all read successfully -- this is not `undetermined` -- and each
#: verdict was true of the instant it was taken. What is missing is any basis for
#: treating them as a description of *one* state.
#:
#: The scenario all three reviewers converged on: a BGP outage read at t0, the
#: fault recovering at t50, an unrelated interface failing at t105, and the
#: interface rung reading that new failure at t115. Every citation resolves, the
#: chain is deterministic, grounding passes, and the report describes a fabric
#: that never existed. See `epoch.py`.
TEMPORALLY_INCOHERENT = "temporally_incoherent"

UNIVERSAL_FINDINGS = frozenset(
    {
        ALL_LAYERS_HEALTHY,
        UNDETERMINED,
        CAUSE_NOT_LOCALISED,
        NO_FAULT_ON_PATH,
        TEMPORALLY_INCOHERENT,
        SUBJECT_NOT_FOUND,
    }
)


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #

#: The seven object types, per D5. Two are implemented; the rest are declared
#: so the registry's shape is fixed without pretending coverage exists.
OBJECT_TYPES: tuple[str, ...] = (
    "interface",
    "isis_adjacency",
    "bgp_session",
    "ldp_session",
    "l3vpn_service",
    "device_health",
    "topology",
)


def _not_implemented(object_type: str, task: str) -> Callable[..., CheckResult]:
    def raise_it(*_args, **_kwargs) -> CheckResult:
        raise NotImplementedError(
            f"the {object_type!r} flow is not implemented; see {task}"
        )

    return raise_it


# --- interface -------------------------------------------------------------
#
# The shortest ladder in the registry, and deliberately so: an interface has
# nothing below it that this tool can observe. The physical layer -- optics,
# cabling, the far end's port -- is exactly where the descent bottoms out and
# hands to a human (D3's ceiling). One rung is the honest depth.
#
# `checks.interface_state` already folds both questions the LLD lists (line
# state, and the error-counter rate) into one verdict with an ordered
# composition, so splitting them across two rungs here would double-report the
# same object. See OBS-052.

INTERFACE_FLOW = Flow(
    object_type="interface",
    subject_schema="<interface-name>, as the device spells it (Gi0/0/0/0)",
    descent=(
        Rung(
            name="interface",
            collect=(
                CollectStep("interfaces"),
                CollectStep("interface", parameter="interface", is_template=True),
            ),
            check=_checks.interface_state,
            finding="interface_line_down",
            device_scope=DeviceScope.LOCAL,
            subject_rule=SubjectRule.AS_IS,
        ),
    ),
    findings=frozenset({"interface_line_down"}) | UNIVERSAL_FINDINGS,
    subject_present=_checks.interface_exists,
)


# --- bgp_session (T-023) ---------------------------------------------------
#
# The ladder from the deck, with the device scope each rung actually needs.
# Rungs 1-3 are about the local device's own view of the session -- its FSM,
# its socket, its RIB. Rungs 4-5 are about the far end: measured on the
# `broken` label, RR1's own IS-IS was healthy while PE2 had no adjacencies at
# all, so checking the IGP locally finds nothing and the descent cannot reach
# the cause (Q-013, OBS-055).
#
# No rung here uses PATH scope yet. `route_to_peer` is the natural candidate --
# an ECMP set with ANY_HEALTHY aggregation -- but this fabric's route to a
# loopback resolves to one entry with a backup, not a true ECMP set, so
# declaring PATH would be modelling a topology we do not have. LOCAL is the
# honest scope until a fabric needs otherwise.

BGP_SESSION_FLOW = Flow(
    object_type="bgp_session",
    subject_schema="<peer-ipv4-address>, as `show bgp summary` lists it (10.255.0.12)",
    descent=(
        Rung(
            name="bgp_session",
            collect=(CollectStep("bgp"),),
            check=_checks.bgp_session_state,
            finding="peer_not_established",
            device_scope=DeviceScope.LOCAL,
        ),
        Rung(
            name="transport",
            collect=(CollectStep("bgp_neighbor", parameter="address", is_template=True),),
            check=_checks.bgp_transport,
            finding="transport_blocked",
            device_scope=DeviceScope.LOCAL,
        ),
        Rung(
            name="route_to_peer",
            collect=(CollectStep("route", parameter="prefix", is_template=True),),
            check=_checks.route_present,
            finding="peer_unreachable_no_route",
            device_scope=DeviceScope.LOCAL,
            subject_rule=SubjectRule.HOST_PREFIX,
        ),
        Rung(
            name="igp_adjacency",
            collect=(CollectStep("isis"),),
            check=_checks.isis_adjacency,
            finding="igp_isolated",
            device_scope=DeviceScope.SUBJECT,
            subject_rule=SubjectRule.DEVICE_WIDE,
        ),
        Rung(
            name="interface",
            collect=(
                CollectStep("interfaces"),
                # The route this device holds back toward the origin. It names
                # the ports the path uses; without it the rung has no member
                # set and is `unevaluated` rather than falling back to every
                # port -- see `SubjectRule.EACH_PATH_INTERFACE`.
                CollectStep("route", parameter="prefix", is_template=True, fill="origin"),
                CollectStep("interface", parameter="interface", is_template=True),
            ),
            check=_checks.interface_state,
            finding="interface_line_down",
            device_scope=DeviceScope.SUBJECT,
            subject_rule=SubjectRule.EACH_PATH_INTERFACE,
            aggregation=Aggregation.ANY_HEALTHY,
        ),
    ),
    findings=frozenset(
        {
            "peer_not_established",
            "transport_blocked",
            "peer_unreachable_no_route",
            "igp_isolated",
            "interface_line_down",
        }
    )
    | UNIVERSAL_FINDINGS,
    subject_present=_checks.bgp_peer_exists,
)


FLOWS: Mapping[str, Flow] = {
    "bgp_session": BGP_SESSION_FLOW,
    "interface": INTERFACE_FLOW,
}


def flow_for(object_type: str) -> Flow:
    """Return one flow, or raise ``NotImplementedError`` naming the task.

    A declared-but-unimplemented object type raises rather than returning
    ``None``, so a caller cannot quietly treat "no flow" as "nothing wrong".
    """

    if object_type not in OBJECT_TYPES:
        # Lead with what is USABLE. The full OBJECT_TYPES vocabulary lists five
        # declared-but-unimplemented flows, and a caller (or a retrying model)
        # shown that list will try one of them next and hit NotImplementedError
        # -- two errors where one suffices (operator walkthrough 2026-08-18,
        # stumble 9). The implemented set answers the actual question.
        implemented = sorted(name for name, flow in FLOWS.items() if flow is not None)
        raise KeyError(
            f"unknown object type {object_type!r}; implemented flows are "
            f"{implemented} (declared but not yet implemented: "
            f"{sorted(set(OBJECT_TYPES) - set(implemented))})"
        )
    flow = FLOWS.get(object_type)
    if flow is None:
        raise NotImplementedError(
            f"the {object_type!r} flow is declared but not implemented "
            "(MVP-0 implements bgp_session and interface only; the rest are "
            "backlog items B-107 to B-111)"
        )
    return flow
