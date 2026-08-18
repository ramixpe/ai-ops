"""Contract tests for checks.py (T-019).

The five checks themselves land at T-020. What is pinned here is the shape
every one of them must satisfy, and above all the rule the module exists to
enforce: **a check may only answer `healthy` about a field it actually read.**
"""

from __future__ import annotations

import copy
import dataclasses
import inspect
from pathlib import Path
from unittest import mock

import pytest
from helpers import set_device_environment

from agent_nettools import checks, network_tools, parsers
from agent_nettools.fixtures import fixture_sender, load_fixture_evidence

# --------------------------------------------------------------------------- #
# Purity -- no I/O, no device, no inventory
# --------------------------------------------------------------------------- #


def test_the_descent_predicates_never_touch_the_inventory_or_a_device():
    """T-019's acceptance criterion, **weakened at B-403 and stated as such.**

    It used to read: *`checks.py` imports nothing that can touch a device* --
    an assertion over the module's own import graph, which is a claim about
    what is *possible*. It could not be violated at runtime because the
    capability was not in the module.

    B-403 merged `health.py` in, and health's baseline rules need
    `inventory/lab.yaml`'s `expected:` blocks. So `checks.py` now imports
    `inventory_model` and `network_tools`, and the import-graph assertion is
    false. The operator directed the merge after that trade was stated
    (OBS-104).

    **What replaces it is a claim about what actually happens**, which is
    strictly less: each of the five rung predicates is run against real parsed
    evidence with the inventory loader, the environment and the socket module
    replaced by bombs. A predicate that grew an inventory read would fail here
    -- but only once someone wrote it, where before they could not have.

    An invariant you cannot violate became one you are told about.
    """

    import socket as _socket

    from agent_nettools import template_parsers

    def bomb(*args, **kwargs):  # pragma: no cover - it must never be reached
        raise AssertionError("a rung predicate reached for the inventory or a device")

    raw = (
        Path(__file__).resolve().parent / "fixtures" / "cisco_xr" / "RR1" / "broken"
        / "show-bgp-neighbor-10-255-0-12.txt"
    ).read_text()
    parsed, status = template_parsers.parse_template_output("cisco_xr", "bgp_neighbor", raw)
    assert status is template_parsers.PARSE_OK
    evidence = {
        "device": "RR1",
        "bgp_neighbor:10.255.0.12": {
            "status": "success",
            "data": {"parsed": parsed, "parse_status": template_parsers.PARSE_OK},
        },
    }

    with mock.patch.object(checks, "load_inventory_file", bomb), \
         mock.patch.object(_socket, "socket", bomb), \
         mock.patch.object(_socket, "create_connection", bomb), \
         mock.patch("builtins.open", bomb):
        result = checks.bgp_transport(evidence, "10.255.0.12")

    assert result.status in {checks.HEALTHY, checks.BROKEN, checks.UNEVALUATED}


def test_every_rung_predicate_runs_with_the_inventory_disabled():
    """The companion, over all six rather than one.

    Without this the test above could pass while five of the six predicates
    had quietly grown a dependency -- §0.12, applied to a guarantee that is now
    sampled rather than structural. `isis_neighbor_up` (B-107) joined the
    original five; leaving it out here would be exactly the shrinking-coverage
    failure §0.12 exists to catch.
    """

    def bomb(*args, **kwargs):  # pragma: no cover
        raise AssertionError("a rung predicate reached for the inventory")

    empty: dict = {"device": "RR1"}
    predicates = [
        lambda: checks.bgp_session_state(empty, "10.255.0.12"),
        lambda: checks.bgp_transport(empty, "10.255.0.12"),
        lambda: checks.route_present(empty, "10.255.0.12/32"),
        lambda: checks.isis_adjacency(empty, None),
        lambda: checks.isis_neighbor_up(empty, "Gi0/0/0/0"),
        lambda: checks.interface_state(empty, "Gi0/0/0/0"),
    ]

    with mock.patch.object(checks, "load_inventory_file", bomb):
        for call in predicates:
            # Every one must reach a verdict -- `unevaluated` here, since the
            # evidence is empty -- without consulting anything outside it.
            assert call().status == checks.UNEVALUATED


def test_checks_reads_no_environment_and_opens_no_files():
    """A check is a pure function of the evidence handed to it."""

    source = Path(inspect.getfile(checks)).read_text(encoding="utf-8")
    for forbidden in ("os.environ", "getenv", "open(", "Path(", "datetime.now", "time.time"):
        assert forbidden not in source, f"checks.py contains {forbidden!r}"


# --------------------------------------------------------------------------- #
# CheckResult
# --------------------------------------------------------------------------- #


def test_check_result_is_frozen():
    """A verdict is a record of what was observed.

    Mutable, a caller could turn an `unevaluated` into a `healthy` several
    frames from the evidence, and the grounding check would never know.
    """

    result = checks.healthy(subject="x", evidence_keys=("RR1:bgp",))
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = checks.BROKEN  # type: ignore[misc]


def test_only_three_statuses_exist():
    assert checks.STATUSES == frozenset({"healthy", "broken", "unevaluated"})


def test_an_unknown_status_is_rejected_at_construction():
    with pytest.raises(ValueError, match="must be one of"):
        checks.CheckResult("degraded", evidence_keys=("RR1:bgp",))


@pytest.mark.parametrize("status", [checks.HEALTHY, checks.BROKEN])
def test_a_conclusive_verdict_must_cite_evidence(status):
    """D20: every claim cites an evidence key. A verdict citing nothing cannot
    be grounded, so it is refused at construction rather than downstream."""

    with pytest.raises(ValueError, match="must cite at least one evidence key"):
        checks.CheckResult(status, reason="because", subject="x", evidence_keys=())


def test_unevaluated_may_cite_nothing():
    """The one exception, and the reason for it: there may genuinely have been
    nothing to read."""

    result = checks.unevaluated(reason="no bgp section")
    assert result.evidence_keys == ()
    assert result.status == checks.UNEVALUATED


def test_is_conclusive_excludes_unevaluated():
    assert checks.healthy(subject="x", evidence_keys=("k",)).is_conclusive
    assert checks.broken(reason="r", subject="x", evidence_keys=("k",)).is_conclusive
    assert not checks.unevaluated(reason="r").is_conclusive


def test_evidence_key_matches_the_operational_memory_convention():
    """glossary.md keys by object, device first: PE2:bgp:10.255.0.31."""

    assert checks.evidence_key("PE2", "bgp", "10.255.0.31") == "PE2:bgp:10.255.0.31"
    assert checks.evidence_key("PE2", "interfaces") == "PE2:interfaces"


# --------------------------------------------------------------------------- #
# The rule: absence is unevaluated
# --------------------------------------------------------------------------- #


def _section(parse_status, records=None):
    return {"data": {"parse_status": parse_status, "parsed": {"records": records or []}}}


def test_require_parsed_passes_a_parsed_section_through():
    evidence = {"bgp": _section(parsers.PARSE_OK, [{"neighbor": "10.255.0.12"}])}
    section, bail = checks.require_parsed(evidence, "bgp")
    assert bail is None
    assert section is evidence["bgp"]


@pytest.mark.parametrize(
    ("parse_status", "expected_reason"),
    [
        (parsers.PARSE_FAILED, "did not parse"),
        (parsers.PARSE_UNAVAILABLE, "not available on this platform"),
        (None, "no parse status"),
    ],
)
def test_a_section_that_did_not_parse_is_unevaluated(parse_status, expected_reason):
    """Never healthy -- nothing was verified. Never broken -- nothing was
    observed. And each failure keeps its own reason, because collapsing them
    throws away the only clue about what to do next."""

    evidence = {"bgp": _section(parse_status)}
    section, bail = checks.require_parsed(evidence, "bgp", subject="10.255.0.12")

    assert section is None
    assert bail is not None
    assert bail.status == checks.UNEVALUATED
    assert expected_reason in bail.reason
    assert bail.subject == "10.255.0.12"


def test_a_missing_section_is_unevaluated_not_healthy():
    """The OBS-044 shape: the evidence simply is not there."""

    section, bail = checks.require_parsed({}, "interfaces", subject="Gi0/0/0/0")
    assert section is None
    assert bail.status == checks.UNEVALUATED
    assert "no 'interfaces' section" in bail.reason


def test_parsed_records_cannot_distinguish_absent_from_empty():
    """Pinning the hazard that makes require_parsed necessary.

    parsed_records() returns [] for both "parsed fine, no records" and "never
    parsed at all". A check calling it without gating on the parse status
    first is exactly how absence gets read as zero -- OBS-044.
    """

    parsed_fine_but_empty = _section(parsers.PARSE_OK, [])
    never_parsed = _section(parsers.PARSE_FAILED)

    assert checks.parsed_records(parsed_fine_but_empty) == []
    assert checks.parsed_records(never_parsed) == []
    # Indistinguishable by records alone -- which is the whole point.
    assert checks.parsed_records(parsed_fine_but_empty) == checks.parsed_records(never_parsed)
    # require_parsed is what tells them apart.
    assert checks.require_parsed({"x": parsed_fine_but_empty}, "x")[1] is None
    assert checks.require_parsed({"x": never_parsed}, "x")[1].status == checks.UNEVALUATED


def test_unevaluated_always_carries_a_reason():
    """An unexplained 'unevaluated' is only marginally better than a wrong
    answer: the operator still cannot tell whether to re-collect, fix a
    parser, or look elsewhere."""

    with pytest.raises(TypeError):
        checks.unevaluated()  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# The five checks (T-020) -- exercised against the committed fixtures.
#
# ``healthy`` is the clean fabric; ``broken`` is PE2 isolated at the IGP layer
# (0 IS-IS adjacencies, both uplinks admin-down), RR1's session to PE2's
# loopback (10.255.0.12) Idle, and PE2 with no route to RR1's loopback
# (10.255.0.31). See tests/fixtures/cisco_xr/*/{healthy,broken}/.
# --------------------------------------------------------------------------- #


def _fixture_evidence(monkeypatch, device: str, *, label: str = "healthy") -> dict:
    """One device's intent evidence, replayed from a committed fixture.

    ``run_template``/``collect_evidence`` call ``get_device`` even when a
    ``sender`` bypasses the network, so credentials must still resolve --
    ``set_device_environment`` supplies safe test-only values for that.
    """

    set_device_environment(monkeypatch)
    return load_fixture_evidence(device, label=label)


def _with_template(evidence: dict, device: str, template: str, *, label: str, **params: str) -> dict:
    """Add one ``run_template`` result to a copy of ``evidence``.

    Keyed ``"<template>:<parameter-value>"``, exactly the convention
    ``checks.py``'s module docstring documents and T-022's collect step will
    build against.
    """

    value = next(iter(params.values()))
    key = f"{template}:{value}"
    evidence = dict(evidence)
    evidence[key] = network_tools.run_template(
        device, template, sender=fixture_sender(label=label), **params
    )
    return evidence


# --- bgp_session_state: the BGP summary's own St/PfxRcd column --- #


def test_bgp_session_state_established_peer_is_healthy(monkeypatch):
    evidence = _fixture_evidence(monkeypatch, "RR1", label="healthy")
    result = checks.bgp_session_state(evidence, "10.255.0.11")
    assert result.status == checks.HEALTHY
    assert result.evidence_keys == ("RR1:bgp:10.255.0.11",)


def test_bgp_session_state_idle_peer_is_broken(monkeypatch):
    """RR1's session to PE2's loopback (10.255.0.12) sits Idle on ``broken``."""

    evidence = _fixture_evidence(monkeypatch, "RR1", label="broken")
    result = checks.bgp_session_state(evidence, "10.255.0.12")
    assert result.status == checks.BROKEN
    assert "Idle" in result.reason
    assert result.evidence_keys == ("RR1:bgp:10.255.0.12",)


def test_bgp_session_state_failed_parse_is_unevaluated(monkeypatch):
    evidence = _fixture_evidence(monkeypatch, "RR1", label="healthy")
    evidence = dict(evidence)
    evidence["bgp"] = _section(parsers.PARSE_FAILED)
    result = checks.bgp_session_state(evidence, "10.255.0.11")
    assert result.status == checks.UNEVALUATED


def test_bgp_session_state_unknown_peer_is_unevaluated_not_broken(monkeypatch):
    """A peer absent from an otherwise-parsed 'bgp' section is unevaluated --
    it may simply not be configured on this device, which is not the same
    fact as "configured and down"."""

    evidence = _fixture_evidence(monkeypatch, "RR1", label="healthy")
    result = checks.bgp_session_state(evidence, "10.255.0.99")
    assert result.status == checks.UNEVALUATED
    assert result.status != checks.BROKEN


# --- bgp_transport: one peer's own show bgp neighbor <peer> --- #


def test_bgp_transport_established_is_healthy(monkeypatch):
    evidence = _fixture_evidence(monkeypatch, "RR1", label="healthy")
    evidence = _with_template(evidence, "RR1", "bgp_neighbor", label="healthy", address="10.255.0.11")
    result = checks.bgp_transport(evidence, "10.255.0.11")
    assert result.status == checks.HEALTHY
    assert result.evidence_keys == ("RR1:bgp_neighbor:10.255.0.11",)


def test_bgp_transport_idle_peer_is_broken(monkeypatch):
    """PE2's own view of its session to RR1's loopback: Idle on ``broken``."""

    evidence = _fixture_evidence(monkeypatch, "PE2", label="broken")
    evidence = _with_template(evidence, "PE2", "bgp_neighbor", label="broken", address="10.255.0.31")
    result = checks.bgp_transport(evidence, "10.255.0.31")
    assert result.status == checks.BROKEN
    # B-432: the signal is the socket, not the FSM. `Idle` came from
    # `connection_state`, which is what rung 1 already reads.
    assert "the socket is not armed for read" in result.reason
    assert "No route to multi-hop neighbor" in result.reason, "the device's own reason"
    assert result.evidence_keys == ("PE2:bgp_neighbor:10.255.0.31",)


def test_bgp_transport_missing_template_section_is_unevaluated(monkeypatch):
    evidence = _fixture_evidence(monkeypatch, "PE2", label="broken")
    result = checks.bgp_transport(evidence, "10.255.0.31")
    assert result.status == checks.UNEVALUATED


# --- route_present: show route <prefix> --- #


def test_route_present_installed_route_is_healthy(monkeypatch):
    evidence = _fixture_evidence(monkeypatch, "RR1", label="healthy")
    evidence = _with_template(evidence, "RR1", "route", label="healthy", prefix="10.255.0.11/32")
    result = checks.route_present(evidence, "10.255.0.11/32")
    assert result.status == checks.HEALTHY
    assert result.evidence_keys == ("RR1:route:10.255.0.11/32",)


def test_route_present_network_not_in_table_is_broken(monkeypatch):
    """PE2 has no route to RR1's loopback (10.255.0.31) on ``broken`` -- the
    device answers '% Network not in table', a real fact, not an absence of
    evidence."""

    evidence = _fixture_evidence(monkeypatch, "PE2", label="broken")
    evidence = _with_template(evidence, "PE2", "route", label="broken", prefix="10.255.0.31/32")
    result = checks.route_present(evidence, "10.255.0.31/32")
    assert result.status == checks.BROKEN
    assert "Network not in table" in result.reason
    assert result.evidence_keys == ("PE2:route:10.255.0.31/32",)


def test_route_present_missing_template_section_is_unevaluated(monkeypatch):
    evidence = _fixture_evidence(monkeypatch, "PE2", label="broken")
    result = checks.route_present(evidence, "10.255.0.31/32")
    assert result.status == checks.UNEVALUATED


# --- isis_adjacency: show isis neighbors --- #


def test_isis_adjacency_two_up_is_healthy(monkeypatch):
    evidence = _fixture_evidence(monkeypatch, "PE2", label="healthy")
    result = checks.isis_adjacency(evidence)
    assert result.status == checks.HEALTHY
    assert result.evidence_keys == ("PE2:isis",)


def test_isis_adjacency_zero_is_broken(monkeypatch):
    """PE2 on ``broken`` is isolated at the IGP layer: 0 adjacencies."""

    evidence = _fixture_evidence(monkeypatch, "PE2", label="broken")
    result = checks.isis_adjacency(evidence)
    assert result.status == checks.BROKEN
    assert "isolated" in result.reason
    assert result.evidence_keys == ("PE2:isis",)


def test_isis_adjacency_failed_parse_is_unevaluated(monkeypatch):
    evidence = _fixture_evidence(monkeypatch, "PE2", label="healthy")
    evidence = dict(evidence)
    evidence["isis"] = _section(parsers.PARSE_FAILED)
    result = checks.isis_adjacency(evidence)
    assert result.status == checks.UNEVALUATED


def test_isis_adjacency_named_interface_up_is_healthy(monkeypatch):
    evidence = _fixture_evidence(monkeypatch, "PE2", label="healthy")
    result = checks.isis_adjacency(evidence, interface="Gi0/0/0/0")
    assert result.status == checks.HEALTHY
    assert result.subject == "Gi0/0/0/0"


def test_isis_adjacency_unknown_interface_is_unevaluated_not_broken(monkeypatch):
    evidence = _fixture_evidence(monkeypatch, "PE2", label="healthy")
    result = checks.isis_adjacency(evidence, interface="Gi9/9/9/9")
    assert result.status == checks.UNEVALUATED


# --- isis_neighbor_up: the isis_adjacency flow's top rung (B-107) --- #
#
# Deliberately not `isis_adjacency`'s `interface=` branch (see the function's
# own docstring for why): this is a flow's top rung, not an embedded one, so
# absence of an isis record is corroborated against lldp and then against the
# interface's own state before it is allowed to stay `unevaluated`. Every case
# below is measured against a real fixture, not invented -- the two positive
# corroborations were each found by running the flow end to end, not by
# writing the check first and asserting what it should do.


def test_isis_neighbor_up_adjacency_up_is_healthy(monkeypatch):
    evidence = _fixture_evidence(monkeypatch, "PE3", label="healthy")
    result = checks.isis_neighbor_up(evidence, "Gi0/0/0/0")
    assert result.status == checks.HEALTHY
    assert "P2" in result.reason
    assert result.evidence_keys == ("PE3:isis:Gi0/0/0/0",)


def test_isis_neighbor_up_adjacency_present_but_not_up_is_broken():
    """A record for the interface exists and its state is not `Up` -- the one
    case that needs no corroboration at all, since the device answered
    directly. Hand-built: no committed fixture catches an adjacency mid-form."""

    evidence = {
        "device": "PE3",
        "isis": _section(
            parsers.PARSE_OK, [{"interface": "Gi0/0/0/0", "system_id": "P2", "state": "Init"}]
        ),
    }
    result = checks.isis_neighbor_up(evidence, "Gi0/0/0/0")
    assert result.status == checks.BROKEN
    assert "Init" in result.reason
    assert result.evidence_keys == ("PE3:isis:Gi0/0/0/0",)


def test_isis_neighbor_up_lldp_corroborated_absence_is_broken(monkeypatch):
    """The measured case the ``isis-broken`` fixture exists for (B-496): PE3's
    ``Gi0/0/0/0`` has no IS-IS record while LLDP shows P2 cabled there, from
    both ends. Absence corroborated by a distinct subsystem is a fault, not an
    unknown."""

    evidence = _fixture_evidence(monkeypatch, "PE3", label="isis-broken")
    result = checks.isis_neighbor_up(evidence, "Gi0/0/0/0")
    assert result.status == checks.BROKEN
    assert "P2" in result.reason
    assert "LLDP" in result.reason
    assert result.evidence_keys == ("PE3:isis:Gi0/0/0/0", "PE3:lldp:Gi0/0/0/0")


def test_isis_neighbor_up_lldp_corroborated_absence_is_broken_from_the_other_end(monkeypatch):
    """The same fixture, read from P2's side -- the asymmetry B-496 captured
    both ends specifically to make provable from either direction."""

    evidence = _fixture_evidence(monkeypatch, "P2", label="isis-broken")
    result = checks.isis_neighbor_up(evidence, "Gi0/0/0/4")
    assert result.status == checks.BROKEN
    assert "PE3" in result.reason
    assert result.evidence_keys == ("P2:isis:Gi0/0/0/4", "P2:lldp:Gi0/0/0/4")


def test_isis_neighbor_up_shut_interface_explains_the_silence_and_is_broken(monkeypatch):
    """PE2's ``Gi0/0/0/0`` on ``broken``: admin-down, and both `isis` and
    `lldp` are empty tables device-wide -- neither protocol can be heard over
    a link not passing traffic. Found by running the flow against this
    fixture, not anticipated in the first draft of this check (which read
    this case as `unevaluated`, one rung above a fault the next rung would
    have found)."""

    evidence = _fixture_evidence(monkeypatch, "PE2", label="broken")
    result = checks.isis_neighbor_up(evidence, "Gi0/0/0/0")
    assert result.status == checks.BROKEN
    assert "admin-down" in result.reason
    assert result.evidence_keys == ("PE2:isis:Gi0/0/0/0", "PE2:interfaces:Gi0/0/0/0")


def test_isis_neighbor_up_absent_from_all_three_is_unevaluated(monkeypatch):
    """PE2's ``Gi0/0/0/2`` on ``broken`` is up, but LLDP is empty device-wide
    and IS-IS has no record for it either -- genuinely ambiguous: it may
    simply not be an IS-IS-enabled link. This is the case the module's
    absence-is-unevaluated rule protects, and the only one of the three
    silence cases that should stay unevaluated."""

    evidence = _fixture_evidence(monkeypatch, "PE2", label="broken")
    result = checks.isis_neighbor_up(evidence, "Gi0/0/0/2")
    assert result.status == checks.UNEVALUATED
    assert result.status != checks.BROKEN


def test_isis_neighbor_up_failed_isis_parse_is_unevaluated():
    evidence = {"device": "PE3", "isis": _section(parsers.PARSE_FAILED)}
    result = checks.isis_neighbor_up(evidence, "Gi0/0/0/0")
    assert result.status == checks.UNEVALUATED


def test_isis_neighbor_up_failed_lldp_parse_says_so():
    """isis parsed fine and is silent; lldp could not be read at all -- the
    reason must say *that*, not claim a corroboration that never happened."""

    evidence = {
        "device": "PE3",
        "isis": _section(parsers.PARSE_OK, []),
        "lldp": _section(parsers.PARSE_FAILED),
    }
    result = checks.isis_neighbor_up(evidence, "Gi0/0/0/0")
    assert result.status == checks.UNEVALUATED
    assert "LLDP could not be read" in result.reason


def test_isis_neighbor_up_failed_interfaces_parse_says_so_not_up():
    """isis and lldp both parsed fine and are silent; `interfaces` could not
    be read. The reason must not claim the interface "reads up" -- that would
    assert a fact never observed, the exact failure this module exists to
    prevent."""

    evidence = {
        "device": "PE3",
        "isis": _section(parsers.PARSE_OK, []),
        "lldp": _section(parsers.PARSE_OK, []),
        "interfaces": _section(parsers.PARSE_FAILED),
    }
    result = checks.isis_neighbor_up(evidence, "Gi0/0/0/0")
    assert result.status == checks.UNEVALUATED
    assert "could not be read" in result.reason
    assert "reads up" not in result.reason


# --- interface_state: show interfaces <name>, plus the error-counter rate --- #


def test_interface_state_admin_down_line_down_is_broken(monkeypatch):
    """PE2's uplinks are admin-down on ``broken`` -- the fact that isolates it.

    Conclusive on a single observation regardless of the (zero, benign) error
    counters also present in this same capture: not-fully-up outranks the
    counter half entirely."""

    evidence = _fixture_evidence(monkeypatch, "PE2", label="broken")
    evidence = _with_template(evidence, "PE2", "interface", label="broken", interface="Gi0/0/0/0")
    result = checks.interface_state(evidence, "Gi0/0/0/0")
    assert result.status == checks.BROKEN
    assert "admin-down" in result.reason
    assert result.evidence_keys == ("PE2:interface:Gi0/0/0/0",)


def test_interface_state_absent_error_counters_are_never_read_as_zero(monkeypatch):
    """OBS-044: PE1's Gi0/0/0/2.300 is the *only* line-down interface anywhere
    in the healthy fixture set (a VLAN sub-interface with no far end attached);
    IOS-XR omits its error-counter line entirely rather than reporting zero.
    Extraction must come back ``None``, not an empty-but-present dict of
    zeros, or a check would silently read "never measured" as "clean".

    Tested against exactly this interface, never one of the 44 healthy
    Gi/Lo0 captures -- every one of those happens to carry a full (zero)
    counter block and would never exercise this path, which is exactly how
    a check written only against the healthy fixtures would carry OBS-044's
    defect forward undetected.
    """

    evidence = _fixture_evidence(monkeypatch, "PE1", label="healthy")
    evidence = _with_template(
        evidence, "PE1", "interface", label="healthy", interface="Gi0/0/0/2.300"
    )
    section = evidence["interface:Gi0/0/0/2.300"]
    assert checks._extract_error_counters(section) is None


def test_interface_state_counter_half_unevaluated_when_absent_from_one_observation(monkeypatch):
    """Two real interfaces, not an invented shape: Gi0/0/0/0's counters
    (present) as one observation, Gi0/0/0/2.300's (absent) as the other."""

    evidence = _fixture_evidence(monkeypatch, "PE1", label="healthy")
    with_counters = _with_template(evidence, "PE1", "interface", label="healthy", interface="Gi0/0/0/0")
    without_counters = _with_template(
        evidence, "PE1", "interface", label="healthy", interface="Gi0/0/0/2.300"
    )
    current = checks._extract_error_counters(with_counters["interface:Gi0/0/0/0"])
    previous = checks._extract_error_counters(without_counters["interface:Gi0/0/0/2.300"])
    assert current is not None
    assert previous is None

    status, _detail = checks._counter_delta_status(current, previous, has_previous=True)
    assert status == checks.UNEVALUATED


def test_interface_state_single_observation_line_up_is_healthy(monkeypatch):
    """Case 3, the deliberate one: no `previous` must not make the whole
    check unevaluated -- it answers healthy about line state, which it
    actually read, and names the unevaluated counter half in the reason."""

    evidence = _fixture_evidence(monkeypatch, "PE1", label="healthy")
    evidence = _with_template(evidence, "PE1", "interface", label="healthy", interface="Gi0/0/0/0")
    result = checks.interface_state(evidence, "Gi0/0/0/0")
    assert result.status == checks.HEALTHY
    assert result.reason == "line protocol up; error-counter rate not evaluated (single observation)"


def test_interface_state_two_observations_flat_counters_is_healthy(monkeypatch):
    evidence = _fixture_evidence(monkeypatch, "PE1", label="healthy")
    evidence = _with_template(evidence, "PE1", "interface", label="healthy", interface="Gi0/0/0/0")
    previous = copy.deepcopy(evidence)

    result = checks.interface_state(evidence, "Gi0/0/0/0", previous=previous)
    assert result.status == checks.HEALTHY
    assert "flat" in result.reason


def test_interface_state_two_observations_rising_input_errors_is_broken(monkeypatch):
    """Derived from real captured output, not an invented format: a deep copy
    of PE1's Gi0/0/0/0 capture with input_errors incremented."""

    evidence = _fixture_evidence(monkeypatch, "PE1", label="healthy")
    evidence = _with_template(evidence, "PE1", "interface", label="healthy", interface="Gi0/0/0/0")
    previous = copy.deepcopy(evidence)

    records = evidence["interface:Gi0/0/0/0"]["data"]["parsed"]["records"]
    for record in records:
        if record["counter"] == "input_errors":
            record["value"] = str(int(record["value"]) + 5)

    result = checks.interface_state(evidence, "Gi0/0/0/0", previous=previous)
    assert result.status == checks.BROKEN
    assert "input_errors" in result.reason


def test_interface_state_evidence_keys_are_non_empty_for_a_conclusive_verdict(monkeypatch):
    evidence = _fixture_evidence(monkeypatch, "PE2", label="broken")
    evidence = _with_template(evidence, "PE2", "interface", label="broken", interface="Gi0/0/0/0")
    result = checks.interface_state(evidence, "Gi0/0/0/0")
    assert result.status == checks.BROKEN
    assert result.evidence_keys


# --------------------------------------------------------------------------- #
# B-430 -- the device's own account of why, read at last
# --------------------------------------------------------------------------- #


def _neighbor_evidence(device, fixture):
    """One real `show bgp neighbor` capture, in the shape a check reads."""

    import pathlib

    from agent_nettools import template_parsers

    raw = (
        pathlib.Path(__file__).resolve().parent
        / "fixtures" / "cisco_xr" / device / "broken" / fixture
    ).read_text()
    parsed, status = template_parsers.parse_template_output("cisco_xr", "bgp_neighbor", raw)
    assert status is template_parsers.PARSE_OK
    return {
        "device": device,
        "bgp_neighbor:10.255.0.12": {
            "status": "success",
            "data": {"parsed": parsed, "parse_status": template_parsers.PARSE_OK},
        },
    }


def test_a_broken_transport_carries_the_device_s_own_reset_reason():
    """Round 3's wasted answer, now read (OBS-092, shape 7).

    The check reported `transport_blocked` -- true -- while the same parsed
    record carried the far end's stated reason. The diagnostician logged into
    the far device to learn what the local device had already reported.
    """

    evidence = _neighbor_evidence("RR1", "show-bgp-neighbor-10-255-0-12.txt")
    result = checks.bgp_transport(evidence, "10.255.0.12")

    assert result.status == checks.BROKEN
    assert "the socket is not armed for read" in result.reason  # B-432
    assert "hold time expired" in result.reason, "the device's own account of why"
    assert "history, not current state" in result.reason


def test_the_reset_reason_is_dated_so_staleness_is_the_reader_s_to_judge():
    """`last_reset_ago` travels with it. A reason from an hour ago says nothing
    certain about a session that is down now, and the check does not pretend
    otherwise -- it hands over both and lets the reader decide."""

    evidence = _neighbor_evidence("RR1", "show-bgp-neighbor-10-255-0-12.txt")
    result = checks.bgp_transport(evidence, "10.255.0.12")

    assert "ago with reason" in result.reason


def test_the_reset_reason_never_becomes_the_verdict():
    """The care point, asserted.

    `last_reset_reason` is history and appears on **healthy** sessions too --
    measured across this corpus, seven Established sessions carry 'Peer closing
    down the session'. Promoting it to a finding would trade a shape-7
    under-report for a confident wrong answer, which is the worse trade.
    """

    healthy_evidence = {
        "device": "RR1",
        "bgp_neighbor:10.255.0.11": {
            "status": "success",
            "data": {
                "parse_status": "ok",
                "parsed": {"meta": {
                    "found": True,
                    "connection_state": "Established",
                    "last_reset_reason": "Peer closing down the session",
                    "last_reset_ago": "2d20h",
                }},
            },
        },
    }
    result = checks.bgp_transport(healthy_evidence, "10.255.0.11")

    assert result.status == checks.HEALTHY, "a reset reason cannot make a live session broken"
    assert "Peer closing down" not in (result.reason or ""), (
        "and it is not repeated on a healthy session, where it is noise"
    )


def test_a_session_with_no_recorded_reset_reads_normally():
    """The companion. Without it the note could be unconditional and every test
    above would still pass."""

    evidence = {
        "device": "RR1",
        "bgp_neighbor:10.255.0.99": {
            "status": "success",
            "data": {
                "parse_status": "ok",
                "parsed": {"meta": {"found": True, "connection_state": "Active"}},
            },
        },
    }
    result = checks.bgp_transport(evidence, "10.255.0.99")

    assert result.status == checks.BROKEN
    assert "history" not in (result.reason or "")
    # No socket line in this synthetic meta, so the FSM fallback runs -- and
    # says so, rather than implying a socket was observed.
    assert "no socket state reported" in result.reason


# --------------------------------------------------------------------------- #
# B-432 -- rung 2 tests the TCP layer, not the BGP state machine
# --------------------------------------------------------------------------- #


def _neighbor_meta(**overrides):
    meta = {"found": True, "connection_state": "Idle"}
    meta.update(overrides)
    return {
        "device": "RR1",
        "bgp_neighbor:10.255.0.12": {
            "status": "success",
            "data": {"parse_status": "ok", "parsed": {"meta": meta}},
        },
    }


def test_the_transport_rung_reads_the_socket_not_the_session_state():
    """The whole of B-432 in one assertion pair.

    Rung 1 reads the session state from `show bgp summary`. Rung 2 used to read
    `connection_state` from `show bgp neighbor` -- **two commands reporting the
    same finite state machine**, so rung 2 could never disagree with rung 1 and
    `cause_not_localised` was unreachable in practice (OBS-092).

    The socket's arming is a different subsystem: whether the stack is polling a
    socket for this peer at all.
    """

    # Corrected 2026-08-18 by round 8b. This test used `connection_state="Active"`
    # and asserted HEALTHY on the premise "TCP is up". **`Active` is precisely
    # the state where TCP is NOT up** -- RFC 4271 has it retrying to acquire the
    # peer -- so the test encoded the same misconception as the code it guarded,
    # and both passed each other. `OpenSent` is the state the docstring below
    # actually describes: the OPEN has been sent over an ESTABLISHED TCP session,
    # so the socket is up and the session is not.
    #
    # B-432's point is untouched and is what this still pins: rung 2 reads a
    # different subsystem from rung 1 and can therefore disagree with it. Only
    # the spurious half of the disagreement (mid-connect) is gone -- see
    # `test_a_socket_armed_mid_connect_is_not_evidence_of_transport` below.
    tcp_up_bgp_down = _neighbor_meta(connection_state="OpenSent", socket_armed_read=True)
    result = checks.bgp_transport(tcp_up_bgp_down, "10.255.0.12")

    assert result.status == checks.HEALTHY, (
        "TCP is up -- OpenSent means the OPEN went out over an established "
        "session. The BGP session is not up, and that is rung 1's business."
    )
    assert "socket is armed" in result.reason


def test_a_socket_armed_mid_connect_is_not_evidence_of_transport():
    """B-497, measured by round 8b: 127 separations in `Connect`.

    The socket line during connect-retry is byte-identical to a healthy
    session's, but RFC 4271's `Connect` is *waiting for the TCP connection to
    be completed* -- TCP is not established. Read unqualified, this rung said
    "TCP transport is up" while it was down.

    Why it matters: a filtered TCP 179 cycles Idle -> Connect -> Idle. Sampled
    in `Connect`, the old rung cleared transport and the descent blamed the rung
    above -- a transport fault reported as a BGP-layer fault, which is the one
    failure a dependency descent exists to prevent.

    `unevaluated`, not `broken`: the evidence genuinely cannot tell a transport
    fault from a BGP-layer one here, and the house rule is that a rung which
    cannot be judged stops the walk rather than guessing in either direction.
    """

    for state in ("Connect", "Active", "Idle"):
        result = checks.bgp_transport(
            _neighbor_meta(connection_state=state, socket_armed_read=True),
            "10.255.0.12",
        )
        assert result.status == checks.UNEVALUATED, (
            f"a socket armed in {state} does not establish that TCP is up"
        )
        assert "does not imply an established TCP connection" in result.reason


def test_an_unarmed_socket_is_still_broken_in_every_state():
    """B-497 must not turn a real transport failure into 'cannot tell'."""

    for state in ("Connect", "Active", "Idle", "Established"):
        result = checks.bgp_transport(
            _neighbor_meta(connection_state=state, socket_armed_read=False),
            "10.255.0.12",
        )
        assert result.status == checks.BROKEN, f"unarmed in {state} is a transport fault"


def test_that_is_what_makes_cause_not_localised_reachable():
    """The finding that was dead in practice, alive.

    An AS mismatch, a capability mismatch or an MD5 failure after TCP
    establishes all produce exactly this shape: a socket that is up and a
    session that will not come up. Before B-432 the descent could not represent
    it, because rung 2 restated rung 1.
    """

    from agent_nettools import flows
    from agent_nettools.checks import BROKEN, HEALTHY, CheckResult
    from agent_nettools.descent import RungOutcome, _finding_for

    flow = flows.flow_for("bgp_session")
    statuses = [BROKEN, HEALTHY, HEALTHY, HEALTHY, HEALTHY]
    outcomes = [
        RungOutcome(r.name, "RR1", CheckResult(s, reason=r.name, subject="p",
                                               evidence_keys=(f"RR1:{r.name}",)))
        for r, s in zip(flow.descent, statuses, strict=True)
    ]

    assert _finding_for(flow, outcomes, None) == flows.CAUSE_NOT_LOCALISED


def test_no_socket_means_no_transport_regardless_of_what_the_fsm_says():
    """The other direction. A stale `connection_state` cannot make a dead
    transport look alive."""

    result = checks.bgp_transport(
        _neighbor_meta(connection_state="Established", socket_armed_read=False),
        "10.255.0.12",
    )

    assert result.status == checks.BROKEN
    assert "not armed" in result.reason


def test_the_fsm_fallback_says_that_it_is_a_fallback():
    """No socket line -- an older XR release, or output shaped differently.

    A weaker signal is still a signal, so this falls back rather than returning
    `unevaluated`. It says which signal it used, so a reader is not misled into
    thinking a socket was observed.
    """

    result = checks.bgp_transport(_neighbor_meta(connection_state="Established"), "10.255.0.12")

    assert result.status == checks.HEALTHY
    assert "no socket state reported" in result.reason


def test_the_current_state_reason_is_stated_without_a_staleness_caveat():
    """`state_reason` is the device's *current* explanation --
    `BGP state = Idle (No route to multi-hop neighbor)` -- so unlike
    `last_reset_reason` it needs no "history, not current state" hedge."""

    result = checks.bgp_transport(
        _neighbor_meta(socket_armed_read=False, state_reason="No route to multi-hop neighbor"),
        "10.255.0.12",
    )

    assert "reports the session state as 'No route to multi-hop neighbor'" in result.reason
    assert "history" not in result.reason


def test_the_socket_field_discriminates_across_the_whole_corpus():
    """Anti-vacuity for the claim the change rests on.

    Measured: armed on every Established session in the corpus, not armed on
    every Idle one. If that ever stops holding, the field is not the signal this
    check believes it is.
    """

    import glob

    from agent_nettools import template_parsers

    established_armed = down_unarmed = 0
    for path in glob.glob("tests/fixtures/cisco_xr/*/*/show-bgp-neighbor-*.txt"):
        parsed, status = template_parsers.parse_template_output(
            "cisco_xr", "bgp_neighbor", open(path).read()
        )
        if status is not template_parsers.PARSE_OK:
            continue
        meta = parsed["meta"]
        armed, state = meta.get("socket_armed_read"), meta.get("state")
        if armed is None or state is None:
            continue
        if state == "Established":
            assert armed, f"{path}: Established but socket not armed"
            established_armed += 1
        else:
            assert not armed, f"{path}: {state} but socket armed"
            down_unarmed += 1

    assert established_armed >= 14, established_armed
    assert down_unarmed >= 2, down_unarmed
