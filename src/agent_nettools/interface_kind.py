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
    "canonical",
    "interface_scoped_flows",
    "same_interface",
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


#: Short form -> long form, for the abbreviations IOS-XR prints in
#: ``show interfaces brief`` but not in ``show route``. Declared rather than
#: derived by regex, the same discipline as the prefix tables above: the set of
#: abbreviations a platform uses is a fact about the platform, not a pattern.
_EXPANSIONS: tuple[tuple[str, str], ...] = (
    ("GigabitEthernet", "GigabitEthernet"),
    ("TenGigE", "TenGigE"),
    ("TwentyFiveGigE", "TwentyFiveGigE"),
    ("FortyGigE", "FortyGigE"),
    ("HundredGigE", "HundredGigE"),
    ("FourHundredGigE", "FourHundredGigE"),
    ("Gi", "GigabitEthernet"),
    ("Te", "TenGigE"),
    ("Twe", "TwentyFiveGigE"),
    ("Fo", "FortyGigE"),
    ("Hu", "HundredGigE"),
    ("FH", "FourHundredGigE"),
    ("BE", "Bundle-Ether"),
    ("Bundle-Ether", "Bundle-Ether"),
    ("Lo", "Loopback"),
    ("Loopback", "Loopback"),
    ("Mg", "MgmtEth"),
    ("MgmtEth", "MgmtEth"),
)


def canonical(name: str) -> str:
    """One spelling for one interface, so two sources can be compared.

    ``show route`` prints ``GigabitEthernet0/0/0/0``; ``show interfaces brief``
    prints ``Gi0/0/0/0``. They are the same port and no string comparison says
    so.

    **This canonicalises *within* one device and is not a key across devices.**
    On this fabric RR1 and PE2 both have a ``Gi0/0/0/0``, so comparing
    canonicalised names from two devices produces a confident false match --
    which is the defect B-456's naive form would have shipped (OBS-117). Any
    caller joining two sources must establish they describe the *same device*
    before using this.
    """

    name = (name or "").strip()
    if not name:
        return ""
    for short, long in _EXPANSIONS:
        if name.startswith(short):
            rest = name[len(short):]
            # Only an abbreviation if what follows is not another letter --
            # `Ten` must not be read as `Te` + `n`.
            if not rest or not rest[0].isalpha():
                return long + rest
    return name


def same_interface(left: str, right: str) -> bool:
    """Whether two names, **from the same device**, denote one interface."""

    return bool(left) and canonical(left) == canonical(right)


def interface_scoped_flows() -> frozenset[str]:
    """Which `flows.FLOWS` object types take an interface name as their subject.

    **Derived from `flows.FLOWS` itself, never hand-maintained here.** Built
    for the B-519 audit (OBS-202/OBS-193's "canonicalise at the door" review)
    to answer, from code rather than from memory, "which flows' subject is an
    interface name" -- `interface`, `isis_adjacency` and `ldp_session`, never
    `bgp_session`. A second hand-copied literal tuple of flow names would be
    exactly the failure mode this module's own docstring already warns about
    ("a filter defined three times is three filters, agreeing by accident of
    nobody having edited one") -- so instead of declaring the set, this reads
    it off `Flow.subject_present`, which is already the flow's own
    declaration of what kind of thing its subject is
    (`_checks.interface_exists` for `interface`/`isis_adjacency`/
    `ldp_session`; `_checks.bgp_peer_exists` for `bgp_session`).

    **Not a call to make elsewhere canonicalise.** `tests/test_
    interface_canonicalization.py` uses this to prove, for each of these
    three flows, that `subject`'s SPELLING is deliberately left alone at the
    `investigate_lab_session`/`_cmd_investigate` entry points -- unlike a
    Prometheus label lookup, `subject` here is rendered `SubjectRule.AS_IS`
    into a device command IOS-XR accepts in either spelling, and separately
    matched by `checks.interface_exists` through `same_interface`, which
    already tolerates either spelling on its own. See that test module's
    docstring for the full reasoning this function exists to support.

    Imports `flows`/`checks` locally rather than at module scope: this module
    sits below both in the layer stack (`checks.py` and `flows.py` both import
    `interface_kind`, not the reverse), and a top-of-file import would invert
    that -- see CLAUDE.md's layer-stack note. A local import inside a function
    body carries no such risk: by the time this is called, both modules are
    already fully loaded.
    """

    from . import flows as _flows
    from .checks import interface_exists as _interface_exists

    return frozenset(
        name
        for name, flow in _flows.FLOWS.items()
        if flow.subject_present is _interface_exists
    )
