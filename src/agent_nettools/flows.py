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


@dataclass(frozen=True)
class Rung:
    """One layer of a descent.

    ``aggregation`` is required when ``device_scope`` is ``PATH`` and
    meaningless otherwise; that is enforced in ``__post_init__`` rather than
    left to a convention, because a `PATH` rung with no declared aggregation
    would silently pick one.
    """

    name: str
    collect: tuple[CollectStep, ...]
    check: Callable[..., CheckResult]
    finding: str
    device_scope: DeviceScope = DeviceScope.LOCAL
    aggregation: Aggregation | None = None

    def __post_init__(self) -> None:
        if self.device_scope is DeviceScope.PATH and self.aggregation is None:
            raise ValueError(
                f"rung {self.name!r} has PATH scope, which resolves to a set of "
                "devices, so it must declare an aggregation "
                "(ALL_HEALTHY or ANY_HEALTHY)"
            )
        if self.device_scope is not DeviceScope.PATH and self.aggregation is not None:
            raise ValueError(
                f"rung {self.name!r} declares an aggregation but its scope is "
                f"{self.device_scope.value}, which resolves to exactly one device"
            )


@dataclass(frozen=True)
class Flow:
    """An investigation scoped to one object type."""

    object_type: str
    subject_schema: str
    descent: tuple[Rung, ...]
    findings: frozenset[str]

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

UNIVERSAL_FINDINGS = frozenset({ALL_LAYERS_HEALTHY, UNDETERMINED, CAUSE_NOT_LOCALISED})


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
        ),
    ),
    findings=frozenset({"interface_line_down"}) | UNIVERSAL_FINDINGS,
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
        ),
        Rung(
            name="igp_adjacency",
            collect=(CollectStep("isis"),),
            check=_checks.isis_adjacency,
            finding="igp_isolated",
            device_scope=DeviceScope.SUBJECT,
        ),
        Rung(
            name="interface",
            collect=(
                CollectStep("interfaces"),
                CollectStep("interface", parameter="interface", is_template=True),
            ),
            check=_checks.interface_state,
            finding="interface_line_down",
            device_scope=DeviceScope.SUBJECT,
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
        raise KeyError(
            f"unknown object type {object_type!r}; known types are {list(OBJECT_TYPES)}"
        )
    flow = FLOWS.get(object_type)
    if flow is None:
        raise NotImplementedError(
            f"the {object_type!r} flow is declared but not implemented "
            "(MVP-0 implements bgp_session and interface only; the rest are "
            "backlog items B-107 to B-111)"
        )
    return flow
