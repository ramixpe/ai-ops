"""What kind of thing an interface name refers to.

One declared table, replacing three copies of `name.startswith("Gi")` (B-431).

Why a table and not a regex
----------------------------
The rule this replaces was `name.startswith("Gi") and "." not in name`. On this
fabric it is **correct by coincidence of naming**: every physical interface here
is a `GigabitEthernet`, so excluding `BV200` worked — not because anything knew
a bridge-group virtual interface is not a physical member, but because it does
not begin with `Gi`.

Three things followed, none of which this fabric can show (OBS-092):

* A `TenGigE`, `HundredGigE` or `Bundle-Ether` member is **silently excluded
  from the rung that matters most.**
* If that empties the member set, the aggregation had nothing to aggregate.
* The rule was written three times, in `descent.py`, `investigation.py` and
  `fixtures.py`, and the third had a *different* definition. **A filter defined
  three times is three filters**, agreeing by accident of nobody having edited
  one. The first divergence would be a descent that collects one member set and
  aggregates over another — a plausible verdict over the wrong members, and
  invisible to every check in the build.

So: a table, declared and reviewable, the same discipline as
`template_parsers.IgnoreRule` and `log_window.NoiseRule`.

`UNKNOWN` is the load-bearing member
-------------------------------------
A name matching nothing must never silently become non-physical — that is the
original defect wearing a new coat. :func:`classify` returns `UNKNOWN`, and
:func:`physical_members` returns the unclassified names alongside the members so
a caller can surface them. A rung with no members and some unclassified names is
`unevaluated`, not healthy and not a crash.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "AGGREGATE_PREFIXES",
    "MANAGEMENT_PREFIXES",
    "PHYSICAL_PREFIXES",
    "VIRTUAL_PREFIXES",
    "InterfaceKind",
    "classify",
    "is_physical_member",
    "physical_members",
]


class InterfaceKind(Enum):
    """What an interface name denotes, for the purpose of a rung's member set."""

    #: A real port that can carry the data plane. What `EACH_PHYSICAL_INTERFACE`
    #: means.
    PHYSICAL = "physical"
    #: A logical child of a physical port. Its parent is the member, not it.
    SUBINTERFACE = "subinterface"
    #: A logical aggregate of physical ports. Real, and not itself a member --
    #: its constituent ports are.
    AGGREGATE = "aggregate"
    #: Loopback, Null, BVI, SR-TE endpoint. No port behind it.
    VIRTUAL = "virtual"
    #: Out-of-band. Never on a forwarding path, so never a member.
    MANAGEMENT = "management"
    #: Matched nothing declared here. **Never treated as non-physical by
    #: default** -- surfaced instead, so an unfamiliar platform's naming is a
    #: visible gap rather than a silently empty member set.
    UNKNOWN = "unknown"


#: IOS-XR abbreviates interface names in `show interfaces brief`, so both the
#: short and long forms are declared. Ordered longest-prefix-first within each
#: group; `classify` checks them in a fixed order so `TenGigE` cannot be
#: shadowed by a shorter entry.
PHYSICAL_PREFIXES: tuple[str, ...] = (
    "GigabitEthernet", "TenGigE", "TwentyFiveGigE", "FortyGigE",
    "HundredGigE", "FourHundredGigE", "Ethernet",
    "Gi", "Te", "Twe", "Fo", "Hu", "FH",
)

#: A bundle is real and is not a member: `EACH_PHYSICAL_INTERFACE` wants the
#: ports, and a bundle's health is a different question from its members'.
AGGREGATE_PREFIXES: tuple[str, ...] = ("Bundle-Ether", "BE")

VIRTUAL_PREFIXES: tuple[str, ...] = (
    "Loopback", "Null", "BVI", "tunnel-te", "tunnel-ip", "tunnel-mte",
    "srte_c_", "Lo", "Nu", "BV",
)

MANAGEMENT_PREFIXES: tuple[str, ...] = ("MgmtEth", "Mg")


def classify(name: str) -> InterfaceKind:
    """What kind of interface ``name`` denotes.

    Subinterface first: `Gi0/0/0/2.300` is a subinterface, and testing the
    physical prefix before the dot would call it a port.
    """

    name = (name or "").strip()
    if not name:
        return InterfaceKind.UNKNOWN
    if "." in name:
        return InterfaceKind.SUBINTERFACE
    for prefixes, kind in (
        (MANAGEMENT_PREFIXES, InterfaceKind.MANAGEMENT),
        (AGGREGATE_PREFIXES, InterfaceKind.AGGREGATE),
        (VIRTUAL_PREFIXES, InterfaceKind.VIRTUAL),
        (PHYSICAL_PREFIXES, InterfaceKind.PHYSICAL),
    ):
        if any(name.startswith(prefix) for prefix in prefixes):
            return kind
    return InterfaceKind.UNKNOWN


def is_physical_member(name: str) -> bool:
    """True for a real port that belongs in an `EACH_PHYSICAL_INTERFACE` set."""

    return classify(name) is InterfaceKind.PHYSICAL


def physical_members(names: list[str]) -> tuple[list[str], list[str]]:
    """``(members, unclassified)``, in input order.

    Both halves are returned because dropping the second is the defect this
    module exists for. A caller that ignores ``unclassified`` is back to
    excluding names it does not recognise without saying so.
    """

    members = [n for n in names if is_physical_member(n)]
    unclassified = [n for n in names if classify(n) is InterfaceKind.UNKNOWN]
    return members, unclassified
