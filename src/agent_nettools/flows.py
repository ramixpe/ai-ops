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

#: The seven object types, per D5. Four are implemented (`FLOWS`, below).
#: The other three -- `device_health`, `l3vpn_service`, `topology` -- were
#: each investigated and refused (B-108, B-110, B-111), not merely left
#: unbuilt: `REFUSED_OBJECT_TYPES` carries a distinct reason for each. As of
#: B-110/B-111 there is no third state left in this registry -- every
#: declared object type is now either built or refused with evidence; see
#: the block comments above `FLOWS`, below, for each refusal's reasoning.
OBJECT_TYPES: tuple[str, ...] = (
    "interface",
    "isis_adjacency",
    "bgp_session",
    "ldp_session",
    "l3vpn_service",
    "device_health",
    "topology",
)


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


# --- isis_adjacency (B-107) -------------------------------------------------
#
# The second flow, built serially and alone per BACKLOG.md's B-107 row, to
# prove the bgp_session pattern repeats rather than replicate an
# unvalidated assumption five times (BACKLOG.md, "After the tracks").
#
# **The subject is a local interface name**, not a neighbour system-id or a
# remote device name -- the other two shapes OBS-057 names as candidates, and
# the choice is deliberate and reversible (it touches only this Flow and
# `checks.isis_neighbor_up`; nothing else assumes it):
#
# * A **neighbour system-id** reads naturally ("my adjacency to P2") but this
#   lab's `show isis neighbors` only *displays* a hostname because IOS-XR
#   resolved it via dynamic-hostname TLV exchange -- the underlying identity is
#   the NSAP-style NET, which is not guaranteed legible or even present if that
#   feature is off. It would also have needed a **new** resolution path to
#   reach the interface a lower rung must read, because neither `isis` nor
#   `lldp` is keyed by system-id in this parser's output.
# * A **remote device name** is the same shape as system-id on this fabric
#   (they print identically here) and inherits the same fragility, plus it is
#   ambiguous on any device with more than one parallel link to the same
#   neighbour -- which system-id and interface both are not.
# * **A local interface name** is what `isis`, `lldp` and `interfaces` are all
#   *already* keyed by, needs no new resolution mechanism, and is the same
#   vocabulary `interface_exists` and the `interface` flow already use
#   (`subject_schema="<interface-name>, as the device spells it"`) -- so
#   `subject_present` is that function, unmodified. IS-IS adjacencies in this
#   fabric are point-to-point (`show isis neighbors`' SNPA column reads
#   `*PtoP*` throughout), so "the adjacency on this interface" is unambiguous:
#   exactly one neighbour, if any, can be on it.
#
# **The ladder is two rungs, not the four-or-five of bgp_session, and that is
# measured rather than a shortcut.** IS-IS's only genuine dependency this tool
# can observe is the physical link -- unlike `bgp_session`, there is no TCP
# socket or RIB lookup between "IS-IS Hello" and "the interface is up" to make
# a rung out of. LLDP looks like a natural middle rung and is not one: LLDP
# does not gate IS-IS adjacency formation (two independent protocols sharing a
# wire), so a rung asserting "IS-IS needs LLDP" would be B-437's forbidden
# shape -- a rung whose dependency claim is false. LLDP earns its place inside
# the top rung's *check* instead, as corroborating evidence from a distinct
# subsystem (see `checks.isis_neighbor_up`), which is a difference in *how a
# verdict is decided*, not a claim that one protocol depends on the other.
#
# Measured on `isis-broken` (device=PE3, subject="Gi0/0/0/0"): rung 1 broken
# (no IS-IS record for the interface, LLDP shows P2 cabled there), rung 2
# healthy (the interface itself is up/up). The walk's own `cause_not_localised`
# rule then applies -- rung 1 is the only broken rung and nothing beneath it
# explains it -- which is the honest answer this build can give: it confirms
# the fault is not physical and stops there, because the actual cause (an
# IS-IS-specific misconfiguration -- area, authentication, network type,
# metric) lives on the config axis this build does not read (B-104, noted
# against the fixture itself in B-496).

ISIS_ADJACENCY_FLOW = Flow(
    object_type="isis_adjacency",
    subject_schema="<local-interface-name>, as the device spells it (Gi0/0/0/0)",
    descent=(
        Rung(
            name="isis_adjacency",
            # `interfaces` is not just belt-and-braces here: the check's third
            # disambiguator reads it (an interface reading down explains why
            # both isis and lldp are silent), and a rung's `check` must only
            # read what its own `collect` names -- the coherence re-read
            # (`epoch._collect_one_rung`) re-collects *exactly* this tuple, not
            # the whole epoch. Measured: PE2's admin-down `Gi0/0/0/0` (`broken`
            # fixture) walked to `interface_line_down` correctly, then the
            # re-read silently dropped `interfaces` and disagreed with itself,
            # flipping this rung to `unevaluated` and the finding to
            # `temporally_incoherent` -- caught by running the CLI end to end
            # against the fixture, not by a unit test of the check alone.
            collect=(CollectStep("isis"), CollectStep("lldp"), CollectStep("interfaces")),
            check=_checks.isis_neighbor_up,
            finding="adjacency_not_up",
            device_scope=DeviceScope.LOCAL,
            subject_rule=SubjectRule.AS_IS,
        ),
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
    findings=frozenset({"adjacency_not_up", "interface_line_down"}) | UNIVERSAL_FINDINGS,
    subject_present=_checks.interface_exists,
)


# --- ldp_session (B-109) -----------------------------------------------------
#
# Third flow, built after B-107 established that the two-rung pattern
# generalises. **The subject is a local interface name**, exactly
# `isis_adjacency`'s choice and for the same reasons: `ldp` and
# `ldp_discovery` are both interface-keyed (a peer's own LDP router-id is not
# guaranteed to be legible or stable any more than a system-id is), no new
# resolution mechanism is needed, and it is the same vocabulary `interface`
# and `isis_adjacency` already use.
#
# **Two rungs, not three.** The honest-dependency question (OBS-167) was asked
# of two candidates:
#
# * **LDP discovery (Hello) gates LDP session formation** -- this is not
#   correlation, it is the protocol's own state machine: a session cannot
#   reach `Oper` without completing discovery first, the same relationship
#   `bgp_transport`'s TCP socket has to a BGP session. It earns a place in the
#   evidence `ldp_session_up` reads -- but as corroboration *inside* the top
#   rung's check, the same shape `bgp_transport` itself uses for its own
#   socket-vs-FSM read (B-432), not as a separate rung: there is no *third*,
#   independent verdict discovery could contribute once the top rung already
#   distinguishes "no session, Hello never completed" from "no session, Hello
#   completed" from "healthy". Two real distinctions, one rung.
# * **IS-IS adjacency does NOT gate LDP Hello.** Hello is link-local multicast,
#   independent of the IGP -- measured live on P2's Gi0/0/0/4 toward PE3
#   (2026-08-19): IS-IS is down there (OBS-159/B-496, still live) and LDP
#   discovery is *also* down there (`xmit` only, no reply), but Hello's own
#   mechanics do not depend on IS-IS being adjacent. Two protocols failing on
#   the same link is the LLDP-vs-isis shape exactly (OBS-167): a shared root
#   cause below what this tool reads, not one gating the other. An `isis` rung
#   here would be the refused hypothesis repeated with a different protocol
#   name.
#
# So the ladder bottoms out at `interface`, same depth and same reasoning as
# `isis_adjacency`: the physical layer is where this tool's evidence runs out
# and D3's ceiling hands to a human.

LDP_SESSION_FLOW = Flow(
    object_type="ldp_session",
    subject_schema="<local-interface-name>, as the device spells it (Gi0/0/0/0)",
    descent=(
        Rung(
            name="ldp_session",
            # `ldp_discovery` and `interfaces` are read for corroboration by
            # `ldp_session_up`, not belt-and-braces -- see the check's
            # docstring. Both are named here because the epoch coherence
            # re-read (`epoch._collect_one_rung`) re-collects exactly this
            # rung's own declared tuple, the OBS-167 lesson: a check may only
            # read what its own rung's `collect` names.
            collect=(
                CollectStep("ldp"),
                CollectStep("ldp_discovery"),
                CollectStep("interfaces"),
            ),
            check=_checks.ldp_session_up,
            finding="session_not_up",
            device_scope=DeviceScope.LOCAL,
            subject_rule=SubjectRule.AS_IS,
        ),
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
    findings=frozenset({"session_not_up", "interface_line_down"}) | UNIVERSAL_FINDINGS,
    subject_present=_checks.interface_exists,
)


# --- device_health (B-108) --------------------------------------------------
#
# Investigated, not skipped -- and refused as a flow, permanently rather than
# provisionally. `device_health` stays in OBJECT_TYPES because D5 named it as
# one of the seven candidate object types up front, but it never gets a FLOWS
# entry, and `flow_for` raises a distinct message for it below rather than the
# generic "not implemented yet" it gives `l3vpn_service`/`topology`: those two
# are waiting on judgement time, this one was given the judgement and failed
# the bar on the evidence.
#
# The backlog's own framing is "wraps the existing health.py verdicts as a
# flow, so 'is PE1 ok' has an entry point" -- and that entry point already
# exists, and is not a flow: `checks.evaluate_device`/`evaluate_fabric`,
# reachable today as `nettools health DEVICE` and as the MCP tool
# `assess_lab_device_health`. Both already answer "is PE1 ok", from the same
# evidence a flow would collect, without asserting an ordering between rules
# that were never shown to depend on each other.
#
# What a descent asserts, and what device health has instead
# --------------------------------------------------------------
# A rung ladder's contract (B-437, on `Rung` above) is "the lowest broken rung
# is the cause": each rung is a falsifiable claim that the layer above cannot
# work unless this one does. `ROLE_INVARIANT_RULES` has no such relationship
# between its members -- `isis_isolated`, `bgp_session_down`,
# `interface_admin_up_line_down` and `sr_policy_down` are independent facts
# about independent subsystems, evaluated independently and rolled up by
# worst-severity, not by dependency. Forcing a rung order onto them would
# manufacture a chain the rules were never designed to support, and every
# investigate caller who trusted "lowest broken rung" would inherit a false
# causal claim.
#
# Measured, not asserted, on `tests/fixtures/cisco_xr` (`t0`/`t1`): PE1 carries
# `interface_admin_up_line_down` (an admin-up/line-down subinterface),
# `sr_policy_down` (an SR-TE policy not operationally up) and `bgp_no_prefixes`
# simultaneously. None of the three causes either of the others -- an SR-TE
# policy's operational state does not depend on an unrelated subinterface's
# line protocol. A device_health ladder ordering these would report whichever
# sorts lowest as "the cause" of the ones above it, which the evidence does not
# support.
#
# The one case that looks like a chain and is not one, also measured: PE2
# carries both `isis_isolated` (zero adjacencies) and `bgp_session_down` (its
# session to 10.255.0.31 stuck in Idle) on every label, while every physical
# interface reads up/up -- so "interface" explains neither. If IS-IS isolation
# explains that specific BGP session, establishing it is already
# `bgp_session`'s job: its `igp_adjacency` rung is scoped to *that peer*, on
# *the subject's device* (Q-013's `DeviceScope.SUBJECT`), and gets the right
# answer today -- `nettools investigate RR1 10.255.0.12 --flow bgp_session`.
# A device-wide rung asserting the same claim would be re-deriving it at
# coarser granularity, for every BGP peer on the device at once, with no way
# to tell a peer whose failure IS-IS actually explains from one that is down
# for an unrelated reason (an MD5 mismatch, an admin shutdown) -- exactly the
# false generalisation B-437 exists to refuse.
#
# What would make it real, and why that is not buildable now
# ----------------------------------------------------------------
# A genuine device-health descent needs a layer *below* every protocol rung
# this tool has -- platform/environmental state that gates protocol state the
# way a socket gates a session (B-432's shape). `docs/design/next-level.md`
# names the candidate: `show environment` / `show redundancy` -- power,
# temperature, RP/process state, "the faults that announce themselves before a
# protocol notices". This build collects none of it: no such intent exists in
# `platforms.py`, so there is no fixture, and no captured or injected
# separating case. B-437 requires that evidence *before* a rung is added, not
# after, and there is no lab or network access in this task to go generate it.
# Adding the rung anyway would be exactly what B-437's own audit was built to
# catch: a rung given a distinct subsystem with zero exercised evidence that
# the boundary is real, which is indistinguishable from a rung that does not
# work (OBS-121's "a capability added and never exercised" finding, about this
# exact mistake made once already on `bgp_session`'s rung 2).
#
# `assess_lab_device_health`'s own docstring already draws this line: "It
# tells you *that* something is wrong, and which rule fired. If you then need
# to know *why* ..., prefer `investigate_lab_session`." device_health is the
# *that*; the flows in `FLOWS` are the *why*. Collapsing them into one flow
# would blur a distinction this codebase already ships and tests --
# `docs/build/MCP-RETEST-PROTOCOL.md`'s Q3 is the negative control "is PE1
# healthy?", and the documented correct answer is `assess_lab_device_health`,
# not the descent tool.


# --- l3vpn_service (B-110) --------------------------------------------------
#
# Investigated, not skipped -- and refused, though on a different axis than
# device_health's. Q-004 settled the naming scheme (`<pe>:<vrf>`, OBS-035) and
# `docs/build/discovery-l3vpn.md`'s live captures (2026-08-15) confirm this
# fabric runs a genuine L3VPN service: three VRFs across four PEs (CUSTA on
# PE1/PE3, CUSTB on PE2/PE4, SHARED-SVCS on PE1 only), real RT import/export
# policy, and MP-BGP VPNv4 Established with non-zero prefixes on RR1 and all
# four PEs (OBS-183, 2026-08-19 -- the reason `bgp_vpnv4` shipped as an intent
# at all). The service is real. The flow is refused anyway, because this
# build cannot observe it at the granularity its own settled subject names.
#
# No VRF-scoped collection surface exists, and this agent cannot create one
# ------------------------------------------------------------------------
# `platforms.PLATFORM_INTENTS["cisco_xr"]` and
# `templates.PLATFORM_TEMPLATES["cisco_xr"]` were read in full rather than
# assumed. Neither contains a VRF-qualified command of any kind -- no `show
# vrf`, no `show route vrf <vrf> <prefix>`, no `show bgp vpnv4 unicast vrf
# <vrf> ...`, no `show ip interface vrf <vrf> brief`. `templates.py`'s `route`
# template renders `show route {prefix}` with no VRF qualifier at all, so it
# reads the *default* routing table -- never a VRF's own. The one VPNv4-shaped
# intent that does exist, `bgp_vpnv4` (`show bgp vpnv4 unicast summary`), is
# device-wide: one row per iBGP peer, the same session and FSM `bgp_session`'s
# own top rung already reads under a different AFI. `platforms.py`'s own
# comment beside it says so directly -- "a second rung reading this intent
# would not be a new dependency hypothesis (OBS-167) -- it would be the same
# Established/not-Established fact under a different AFI, which is
# corroboration, not a gate" -- and that reasoning applies exactly as hard to
# a rung inside a *different* flow's ladder as it does inside `bgp_session`'s
# own. Confirmed against `tests/fixtures/`: the only VPNv4-shaped capture on
# any device is `show-bgp-vpnv4-unicast-summary.txt`, and its content is the
# same aggregate per-neighbour session view for every VRF the box carries --
# it cannot tell CUSTA's traffic from CUSTB's.
#
# `platforms.py` and `templates.py` are also outside this agent's ownership
# this wave -- a different agent owns them, concurrently -- so even setting
# the evidence above aside, there is no file this agent may edit to add a
# VRF-scoped command. B-437 requires the evidence (a live-verified command
# plus a captured broken-VRF fixture) to exist *before* a rung is written, not
# after, and there was no live-lab access in this session either, so nothing
# could be captured to close the gap even were the file open.
#
# The subject cannot even be checked for existence
# --------------------------------------------------
# `subject_present` (B-459) asks the *device*, not a document, whether the
# named object exists -- and there is no command that lists a PE's VRFs at
# all. `<pe>:<vrf>`'s existence could only be answered from
# `discovery-l3vpn.md`'s static table, which would make it a second,
# unverified source of the same fact a device's own evidence should answer --
# exactly the failure `graph.py`'s docstring warns about, and one this fabric
# already paid for once (OBS-103/B-435): an authored source of topology
# disagreeing with the evidence, for a reason that turned out to be
# resolvable only because the evidence was read directly instead of trusted
# by proxy.
#
# What IS observable does not discriminate by VRF, and that is not a minor gap
# ------------------------------------------------------------------------------
# The CE-facing physical interface (`GigabitEthernet0/0/0/2` on every PE, per
# `discovery-l3vpn.md` §2) is fully observable today with zero new commands --
# but its up/down state is exactly what the existing `interface` flow already
# reports, under a name that carries no VRF at all. A ladder built only from
# what is collectible would produce the **identical** verdict for `PE1:CUSTA`
# and `PE1:SHARED-SVCS`, because nothing collected distinguishes them: the
# ladder cannot see the thing its own subject names.
#
# And the fabric's own documented failure mode would be invisible to it.
# `discovery-l3vpn.md` §1 calls SHARED-SVCS "a service whose reachability
# depends on RT policy rather than on the protocol stack" and says outright
# that "the `bgp_session` descent's ladder ... would find every rung healthy
# and still not explain a leak failure, because the fault would live in RT
# import/export." Every rung this build could construct today (VPNv4 session
# state, a physical interface) is protocol-stack evidence -- the exact kind
# that document names as blind to this fabric's actual L3VPN fault. A ladder
# that reports `all_layers_healthy` on a genuinely broken RT-leak service is
# not a weak flow -- it is `checks.py`'s own nightmare case, "silent
# degradation behind a green flag," shipped as this flow's headline behaviour
# rather than caught as an edge case.
#
# Contingent, not permanent. Unlike device_health's structural mismatch
# (aggregation vs. descent, true regardless of what evidence exists),
# l3vpn_service's refusal is against *this build's current collection
# surface*. Revisit condition: a VRF-scoped command exists, verified live and
# captured with a broken-VRF fixture (per this file's `Flow` docstring and
# B-437's separating-case requirement), and the RT-policy blind spot above is
# either closed by a config-axis read or explicitly accepted as this flow's
# known ceiling rather than discovered by an operator the hard way.


# --- topology (B-111) -------------------------------------------------------
#
# Investigated, not skipped -- and refused, on D5's own test rather than on a
# collection-surface gap. The backlog row's stated blocker ("the fabric's
# LLDP data is self-contradictory, so this one must report disagreement
# rather than assert links") is stale: OBS-103/B-435 found the apparent
# inconsistency was a hostname-resolution artefact -- P1 ran the configured
# hostname `LEAF05_DHCP_SERVER` at capture time, so both ends of the LLDP
# exchange were telling the truth and the mismatch was between LLDP's
# device-reported names and the inventory's labels, corrected structurally by
# `topology.hostname_map`/`resolve_device`. That correction removes the
# *original* reason this item waited; it does not supply a dependency
# hypothesis to build a ladder from. The deeper question is whether
# "topology" is an object with a lowest-broken-rung at all, and every framing
# tried says no.
#
# D5's test: "is this an object, or a symptom of one?" `design-thinking.md`
# itself answers this before a ladder is even sketched -- its own D5 table
# lists `get_topology(scope)` under "Relationships, multi-device flows": a
# query that returns a graph, not a protocol with a health FSM. There is no
# "topology session" that is Established or not, Up or not, the way every
# other flow's top rung reads one. "Topology" names the *shape* of the
# network, and a shape is queried, not diagnosed.
#
# Three concrete framings were tried against that test, not assumed away:
#
# 1. **Per-link** ("does this interface's discovered neighbour match
#    reality"). This is `isis_adjacency`'s existing two-rung ladder,
#    unchanged. LLDP is already read there, inside `checks.isis_neighbor_up`,
#    as corroboration for exactly this reason -- and LLDP was *refused* as a
#    standalone rung by B-107/OBS-167 because it does not gate IS-IS adjacency
#    formation. A `topology` flow asserting "the fabric needs LLDP-vs-IS-IS
#    agreement" over the same evidence would be that refused hypothesis,
#    repeated under a new flow's name rather than corrected.
# 2. **Fabric-wide** (LLDP disagreements, neighbours not in the inventory,
#    zero-adjacency devices). This is an aggregation over independent
#    per-link facts -- one link's LLDP/IS-IS agreement says nothing about any
#    other link's -- which is B-108's shape exactly: ordering independent
#    facts into rungs manufactures a causal claim the evidence does not
#    support. And it is already served, at the *right* granularity (the whole
#    fabric, not one subject), by modules this agent does not own and must
#    not duplicate: `topology.build_anomaly_report` (`nettools
#    learn-topology`'s three anomaly classes), `audit.py`'s cross-device
#    consistency rules (`nettools audit`, B-477 -- "the fabric judged against
#    itself"), and `graph.py`'s pure LLDP/IS-IS graph projection -- a
#    library-level module, not yet its own CLI/MCP surface, but exactly the
#    shape `design-thinking.md`'s own D5 table names for this question
#    (`get_topology(scope)`) -- which deliberately keeps the two protocols'
#    edges apart rather than merging them into one "link is up" fact (see its
#    own docstring, and `test_isis_broken_pe3_p2_is_lldp_only`).
# 3. **Device-pair reachability** ("can device A reach device B"). This
#    re-derives rungs `bgp_session` already owns -- `route_present`,
#    `isis_adjacency` (device-wide), `interface_state` (PATH-scoped,
#    `EACH_PATH_INTERFACE`/`ANY_HEALTHY`) -- without adding a genuine
#    top-layer object above them. `route_present` already *is* the complete
#    answer to "is there a path"; a `topology` flow reading the same intents
#    to ask the same question under a new name adds a label, not a dependency
#    hypothesis, and fails B-437 point 1 (a stated assertion that the layer
#    above cannot work unless this one does) for lack of a layer above to
#    state it about.
#
# Contingent on the same terms as l3vpn_service's refusal, not permanent: if a
# future protocol genuinely gated on cross-device topology agreement in a way
# none of `isis_adjacency`/`bgp_session`/`ldp_session` already reads, that
# would be a new rung on one of those ladders (framing 1's territory), or
# grounds to revisit this refusal with a real separating case -- not evidence
# that "topology" itself was the missing object.

FLOWS: Mapping[str, Flow] = {
    "bgp_session": BGP_SESSION_FLOW,
    "interface": INTERFACE_FLOW,
    "isis_adjacency": ISIS_ADJACENCY_FLOW,
    "ldp_session": LDP_SESSION_FLOW,
}

#: Set once, here, rather than recomputed at every `flow_for("device_health")`
#: call -- the message is long because the reasoning above earns the length,
#: not because it needs to be built fresh each time.
_DEVICE_HEALTH_REFUSAL = (
    "'device_health' is deliberately not a flow (B-108: investigated and "
    "refused, not merely unbuilt -- see the comment block above FLOWS in "
    "flows.py for the full reasoning). Device health is an aggregation over "
    "independent per-protocol signals (IS-IS, BGP, interfaces, SR-TE), not a "
    "dependency descent, so it has no 'lowest broken rung' to report. Use "
    "`nettools health DEVICE` (`checks.evaluate_device`/`evaluate_fabric`) or "
    "the MCP tool `assess_lab_device_health` instead -- both already answer "
    "'is DEVICE ok' from the same evidence this flow would have collected."
)

#: Same pattern as `_DEVICE_HEALTH_REFUSAL` -- see the `l3vpn_service (B-110)`
#: comment block above `FLOWS` for the full reasoning this summarises.
_L3VPN_SERVICE_REFUSAL = (
    "'l3vpn_service' is deliberately not a flow (B-110: investigated and "
    "refused, not merely unbuilt -- see the comment block above FLOWS in "
    "flows.py for the full reasoning). The naming scheme is settled "
    "(`<pe>:<vrf>`, Q-004/OBS-035) and this fabric runs a real L3VPN service "
    "-- three VRFs, real RT-leak policy, see docs/build/discovery-l3vpn.md -- "
    "but this build has no VRF-scoped collection surface at all: no `show "
    "vrf`, no VRF-qualified route or interface command, and the one VPNv4 "
    "intent that exists (`bgp_vpnv4`) is device-wide, already read by "
    "`bgp_session`'s own top rung under a different AFI (OBS-183). Without a "
    "VRF-scoped command, neither the subject's own existence nor any rung "
    "beneath it can be told apart per VRF, and even the protocol-stack "
    "evidence this build could collect would report a fabric-documented "
    "RT-policy failure (SHARED-SVCS) as healthy. Read "
    "docs/build/discovery-l3vpn.md for what this fabric actually carries; "
    "there is currently no tool surface that investigates a specific VRF's "
    "service."
)

#: Same pattern again -- see the `topology (B-111)` comment block above
#: `FLOWS` for the full reasoning this summarises.
_TOPOLOGY_REFUSAL = (
    "'topology' is deliberately not a flow (B-111: investigated and refused, "
    "not merely unbuilt -- see the comment block above FLOWS in flows.py for "
    "the full reasoning). 'Topology' names the shape of the network, not a "
    "protocol with a lowest broken rung to descend: a per-link framing "
    "duplicates `isis_adjacency`'s existing ladder (LLDP corroborates there "
    "already, and was refused as its own rung by B-107/OBS-167); a "
    "fabric-wide framing is an aggregation over independent per-link facts "
    "(B-108's shape again), already served by `nettools audit` and "
    "`nettools learn-topology`'s anomaly report; and a device-pair "
    "reachability framing re-derives rungs `bgp_session` already owns "
    "(`route_present`, `isis_adjacency`, `interface_state`) without a new "
    "dependency above them. Use `nettools audit` for fabric consistency, "
    "`nettools learn-topology` for the LLDP/IS-IS anomaly report, or the "
    "`bgp_session`/`isis_adjacency`/`ldp_session` flows for a specific "
    "protocol's dependency chain."
)


#: Object types that are deliberately REFUSED rather than merely unbuilt,
#: mapped to the reason. Public and enumerable so a *surface* (the CLI, the MCP
#: server) can offer the name and answer with the reasoning, instead of
#: rejecting it as an unknown word. That distinction is the whole point of a
#: refusal: `argparse`'s "invalid choice" tells an operator the name is wrong,
#: when the truth is that the name is right and the answer is "use this other
#: thing" (measured 2026-08-19 -- the refusal existed in `flow_for` and the CLI
#: could not reach it, so the operator saw the generic error. OBS-186/OBS-187).
#:
#: As of B-110/B-111 this dict's keys plus `FLOWS`' keys cover every entry in
#: `OBJECT_TYPES` -- there is no third, "pending" object type left in the
#: registry (`test_every_declared_object_type_is_built_or_refused` pins it).
REFUSED_OBJECT_TYPES: dict[str, str] = {
    "device_health": _DEVICE_HEALTH_REFUSAL,
    "l3vpn_service": _L3VPN_SERVICE_REFUSAL,
    "topology": _TOPOLOGY_REFUSAL,
}


def flow_for(object_type: str) -> Flow:
    """Return one flow, or raise ``NotImplementedError`` naming the task.

    A declared-but-unimplemented object type raises rather than returning
    ``None``, so a caller cannot quietly treat "no flow" as "nothing wrong".
    Three of the seven declared types are distinct cases of this: not
    unimplemented, but refused -- see ``REFUSED_OBJECT_TYPES``. As of
    B-110/B-111 every declared object type not in ``FLOWS`` is in
    ``REFUSED_OBJECT_TYPES``, so the ``flow is None`` branch below is
    unreachable today; it is kept as a defensive fallback for an eighth
    object type declared before it is designed, the same "declared, not yet
    decided" state ``l3vpn_service`` and ``topology`` held until this round.
    """

    if object_type not in OBJECT_TYPES:
        # Lead with what is USABLE. A caller (or a retrying model) shown the
        # full OBJECT_TYPES vocabulary will try one of the unbuildable ones
        # next and hit another exception -- two errors where one suffices
        # (operator walkthrough 2026-08-18, stumble 9). The implemented set
        # answers the actual question.
        implemented = sorted(name for name, flow in FLOWS.items() if flow is not None)
        raise KeyError(
            f"unknown object type {object_type!r}; implemented flows are "
            f"{implemented} (refused, not unbuilt: {sorted(REFUSED_OBJECT_TYPES)})"
        )
    if object_type in REFUSED_OBJECT_TYPES:
        raise NotImplementedError(REFUSED_OBJECT_TYPES[object_type])
    flow = FLOWS.get(object_type)
    if flow is None:
        raise NotImplementedError(
            f"the {object_type!r} flow is declared in OBJECT_TYPES but has no "
            "FLOWS entry and no REFUSED_OBJECT_TYPES entry -- it is pending "
            "design, not yet built and not yet refused"
        )
    return flow
