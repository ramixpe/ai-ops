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
    plausible and wrong. The two sides live in different modules now
    (`epoch.template_calls` collects; `descent._physical_interfaces` enumerates
    what the rung is checked over), which is precisely why this is worth
    asserting on real evidence rather than on the presence of an identifier.
    """

    from agent_nettools import epoch

    interfaces = fixtures.load_fixture_evidence("PE2", label="broken")
    step = next(
        s
        for r in flows.flow_for("bgp_session").descent
        if r.name == "interface"
        for s in r.collect
        if s.is_template
    )

    collected = [
        key.split(":", 1)[1]
        for key, _ in epoch.template_calls(step, "10.255.0.12", dict(interfaces))
    ]
    aggregated = descent._physical_interfaces(dict(interfaces))

    assert collected, "the fixture must actually produce members, or this is vacuous"
    assert collected == aggregated, (
        "the epoch collects one member set and the descent aggregates over "
        f"another: collected={collected} aggregated={aggregated}"
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
