"""The interface taxonomy (B-431).

One declared table, replacing three copies of `name.startswith("Gi")` — two
identical and one subtly different. **A filter defined three times is three
filters**, and the first divergence would be a descent that collects one member
set and aggregates over another.
"""

from __future__ import annotations

import pytest

from agent_nettools import descent, fixtures, flows, investigation
from agent_nettools.checks import UNEVALUATED
from agent_nettools.interface_kind import (
    InterfaceKind,
    classify,
    is_physical_member,
    physical_members,
)

#: Every interface name this fabric actually produces, and what it is.
REAL_NAMES = [
    ("Gi0/0/0/0", InterfaceKind.PHYSICAL),
    ("Gi0/0/0/2.300", InterfaceKind.SUBINTERFACE),
    ("BV200", InterfaceKind.VIRTUAL),
    ("Lo0", InterfaceKind.VIRTUAL),
    ("Lo100", InterfaceKind.VIRTUAL),
    ("Nu0", InterfaceKind.VIRTUAL),
    ("Mg0/RP0/CPU0/0", InterfaceKind.MANAGEMENT),
    ("srte_c_10_ep", InterfaceKind.VIRTUAL),
]

#: Names this fabric cannot produce, which is exactly why they are here. The
#: replaced rule excluded every one of them silently.
UNSEEN_NAMES = [
    ("TenGigE0/0/0/0", InterfaceKind.PHYSICAL),
    ("Te0/0/0/0", InterfaceKind.PHYSICAL),
    ("HundredGigE0/0/0/1", InterfaceKind.PHYSICAL),
    ("FourHundredGigE0/0/0/1", InterfaceKind.PHYSICAL),
    ("Bundle-Ether1", InterfaceKind.AGGREGATE),
    ("BE1", InterfaceKind.AGGREGATE),
    ("tunnel-te10", InterfaceKind.VIRTUAL),
    ("MgmtEth0/RP0/CPU0/0", InterfaceKind.MANAGEMENT),
]


@pytest.mark.parametrize(("name", "kind"), REAL_NAMES, ids=[n for n, _ in REAL_NAMES])
def test_every_name_this_fabric_produces_is_classified(name, kind):
    assert classify(name) is kind


@pytest.mark.parametrize(("name", "kind"), UNSEEN_NAMES, ids=[n for n, _ in UNSEEN_NAMES])
def test_names_this_fabric_cannot_produce_are_classified_too(name, kind):
    """The defect, in one test.

    The replaced rule was `startswith("Gi")`, which is right here **by
    coincidence of naming**. Every name in this list would have been silently
    excluded from the rung that matters most.
    """

    assert classify(name) is kind


def test_a_ten_gig_port_is_a_member_and_used_to_not_be():
    assert is_physical_member("TenGigE0/0/0/0")
    assert not "TenGigE0/0/0/0".startswith("Gi"), "the rule this replaced"


def test_a_bundle_is_real_and_is_not_a_member():
    """`EACH_PHYSICAL_INTERFACE` wants the ports. A bundle's health is a
    different question from its members', and answering the second while
    claiming the first would be a category error rather than a filter bug."""

    assert classify("Bundle-Ether1") is InterfaceKind.AGGREGATE
    assert not is_physical_member("Bundle-Ether1")


def test_an_unrecognised_name_is_unknown_and_surfaced_not_dropped():
    """`UNKNOWN` is the load-bearing member.

    Defaulting an unrecognised name to non-physical is the original defect
    wearing a new coat: it excludes silently. `physical_members` returns the
    unclassified names so a caller can say what it did not recognise.
    """

    assert classify("Frobnicator0/1") is InterfaceKind.UNKNOWN
    assert not is_physical_member("Frobnicator0/1")

    members, unclassified = physical_members(["Gi0/0/0/0", "Frobnicator0/1", "Lo0"])
    assert members == ["Gi0/0/0/0"]
    assert unclassified == ["Frobnicator0/1"], "not silently dropped"


@pytest.mark.parametrize("name", ["", "   ", None])
def test_a_missing_name_is_unknown_rather_than_an_exception(name):
    assert classify(name) is InterfaceKind.UNKNOWN


def test_an_empty_member_set_is_unevaluated_not_a_crash_and_not_healthy():
    """B-431's second half.

    `_aggregate([])` used to raise `IndexError` from `results[0].subject` --
    uncaught by the CLI, a traceback in the emit path. And the tempting fix is
    worse: `all([])` is `True`, so an empty `ALL_HEALTHY` set would report a
    member set nobody looked at as fine. Nothing was read, so `unevaluated`.
    """

    rung = next(r for r in flows.flow_for("bgp_session").descent if r.name == "interface")

    result = descent._aggregate(rung, [])

    assert result.status == UNEVALUATED
    assert "no members" in (result.reason or "")


def test_no_module_on_the_descent_path_keeps_its_own_copy():
    """The point of the item. Two copies agreeing today by accident of nobody
    having edited one is not the same as one definition.

    `epoch` joined the list at B-436, which is when the *runner* stopped naming
    `physical_members` at all: the fan-out moved into `epoch.template_calls`, so
    `investigation` now reaches the taxonomy through the shared helper instead
    of applying it itself. That is the consolidation going further, not
    unravelling -- the member set and the collection that feeds it are now one
    definition rather than two that agreed.

    **The source grep for `physical_members` was dropped here deliberately, and
    replaced rather than deleted.** It was a proxy for "uses the shared rule",
    and a proxy stops being one the moment a module reaches the rule
    transitively. The property it stood for is asserted directly by
    `test_collection_and_aggregation_agree_on_the_member_set` below, on
    behaviour instead of on text. What is kept here is the half a grep can
    genuinely check: that nobody has grown a private copy.
    """

    import inspect

    from agent_nettools import epoch

    for module in (descent, investigation, epoch):
        source = inspect.getsource(module)
        assert 'startswith("Gi")' not in source, f"{module.__name__} still has its own copy"


def test_collection_and_aggregation_agree_on_the_member_set():
    """What the grep was standing in for, measured instead of matched.

    The failure B-431 exists to prevent is a descent that **collects** one
    member set and **aggregates over** another: every interface would be read,
    the rung would be evaluated over a different list, and the verdict would be
    plausible and wrong.

    B-456 made this sharper rather than obsolete. The rung now has **two**
    member sets -- path-scoped when a reverse route resolves, every physical
    port when it does not -- and collection has to cover both, because it cannot
    know which the walk will pick. So this asserts the union collected is
    exactly the union the two resolvers produce, on real evidence.
    """

    from agent_nettools import epoch

    evidence = dict(fixtures.load_fixture_evidence("PE2", label="broken"))
    rung = next(r for r in flows.flow_for("bgp_session").descent if r.name == "interface")
    step = next(s for s in rung.collect if s.is_template and s.fill is None)
    assert step.name == "interface", "the fan-out step, not the reverse-route step"

    collected = [
        key.split(":", 1)[1]
        for key, _ in epoch.template_calls(step, "10.255.0.12", evidence)
    ]
    physical = descent._physical_interfaces(evidence)
    path, _ = descent.path_interfaces(evidence, "10.255.0.31/32")

    assert collected, "the fixture must actually produce members, or this is vacuous"
    assert set(collected) == set(physical) | set(path), (
        "collection must cover BOTH member sets the walk can choose between: "
        f"collected={collected} physical={physical} path={path}"
    )


def test_both_member_sets_are_collected_because_the_walk_picks_later():
    """The anti-vacuity companion, and it has teeth here.

    On the `broken` label the reverse route is gone, so the path set is empty
    and the union equals the physical set -- which would let a collector that
    ignored the path set entirely pass the test above. The healthy label is
    where the two differ, and it is where a regression would show.
    """

    from agent_nettools import epoch

    # Built through the epoch, not `load_fixture_evidence`: the latter loads
    # intents only, and the reverse route is a *template*. A test that used it
    # would see an empty path set and pass for the wrong reason.
    flow = flows.flow_for("bgp_session")
    built = epoch.collect_epoch(
        flow, "RR1", "10.255.0.12", resolver=lambda _s: "PE2",
        sender=fixtures.fixture_sender(label="healthy"),
        origin_prefix="10.255.0.31/32",
    )
    evidence = built.for_device("PE2")
    rung = next(r for r in flow.descent if r.name == "interface")
    step = next(s for s in rung.collect if s.is_template and s.fill is None)

    physical = descent._physical_interfaces(evidence)
    path, _ = descent.path_interfaces(evidence, "10.255.0.31/32")

    assert path and physical, "both sets must be non-empty here"
    assert set(path) < set(physical), (
        "the point of path scoping is that it is a strict subset -- "
        f"path={path} physical={physical}"
    )

    collected = {
        key.split(":", 1)[1]
        for key, _ in epoch.template_calls(step, "10.255.0.12", evidence)
    }
    assert collected == set(physical) | set(path), (
        "the union is collected, so the walk can pick either"
    )

def test_the_capture_manifest_differs_deliberately_and_visibly():
    """`fixtures.py` wants the ports **and** `Lo0` -- a loopback's address is
    what a route resolves to. That is a real difference from the descent's
    member set, and it is now written against the shared predicate so it reads
    as a deliberate exception rather than a third rule."""

    import inspect

    source = inspect.getsource(fixtures)
    assert 'startswith("Gi")' not in source
    assert "is_physical_member(name) or name ==" in source


# --------------------------------------------------------------------------- #
# F3 (release-1.0 cleanup): interface_kind._EXPANSIONS and
# grounding._IFACE_EXPANSIONS were two independently-edited copies of the
# same abbreviation table that had drifted in both directions -- this table
# had Twe/FH that grounding's lacked, grounding's had Mgmt/Null/Nu/BVI/BV/
# tunnel-ip/ti/tunnel-te/tt that this table lacked. grounding now reads
# `expansions_lower()` instead of keeping its own dict; this test imports
# both call sites and proves they can never silently diverge again.
# --------------------------------------------------------------------------- #


def test_grounding_reads_the_same_expansion_table_and_cannot_drift():
    from agent_nettools import grounding, interface_kind

    assert grounding._IFACE_EXPANSIONS == interface_kind.expansions_lower()
    # Not merely equal by coincidence at import time -- literally the same
    # data, sourced from the one declared table each time it is asked for.
    assert grounding._IFACE_EXPANSIONS == {
        short.lower(): long.lower() for short, long in interface_kind._EXPANSIONS
    }


def test_expansions_lower_carries_every_abbreviation_both_tables_used_to_have():
    """The union, named explicitly so a future edit that drops one of these
    by accident fails loudly rather than silently narrowing coverage again."""

    from agent_nettools import interface_kind

    lower = interface_kind.expansions_lower()
    # Present only in interface_kind._EXPANSIONS before F3 (25G/400G):
    assert lower["twe"] == "twentyfivegige"
    assert lower["fh"] == "fourhundredgige"
    # Present only in grounding._IFACE_EXPANSIONS before F3:
    assert lower["mgmt"] == "mgmteth"
    assert lower["null"] == "null"
    assert lower["nu"] == "null"
    assert lower["bvi"] == "bvi"
    assert lower["bv"] == "bvi"
    assert lower["tunnel-ip"] == "tunnel-ip"
    assert lower["ti"] == "tunnel-ip"
    assert lower["tunnel-te"] == "tunnel-te"
    assert lower["tt"] == "tunnel-te"
    # Present in both before F3:
    assert lower["gi"] == "gigabitethernet"
    assert lower["te"] == "tengige"
    assert lower["fo"] == "fortygige"
    assert lower["hu"] == "hundredgige"
    assert lower["lo"] == "loopback"
    assert lower["mg"] == "mgmteth"
    assert lower["be"] == "bundle-ether"


def test_canonical_now_expands_bv_and_nu_which_this_fabric_actually_produces():
    """`BV200`/`Nu0` are in `REAL_NAMES` above -- this fabric produces both.
    Before F3, `interface_kind._EXPANSIONS` had no entry for either, so
    `canonical("BV200")`/`canonical("Nu0")` returned the abbreviation
    unchanged: a real `show interfaces brief` reading of a BVI or Null
    interface could never be matched against a `show route`/long-form
    reading of the same port. The union fixes this for interface_kind's own
    device-text canonicalisation, not only for grounding's free-text one."""

    from agent_nettools.interface_kind import canonical, same_interface

    assert canonical("BV200") == "BVI200"
    assert canonical("Nu0") == "Null0"
    assert same_interface("BV200", "BVI200")
    assert same_interface("Nu0", "Null0")
