"""Derived expected-topology counts and the fabric anomaly report.

Uses the committed t0 fixtures (real captured lab output) via
``load_fixture_evidence``, exactly like ``nettools learn-topology
--from-fixtures`` would. The expected values asserted here are independently
derivable by reading the fixture files by eye (see
``tests/fixtures/cisco_xr/<device>/t0/``); this test pins them so a future
change cannot silently alter what the tool reports about a fabric it does not
control the truth of.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from helpers import set_device_environment

from agent_nettools import parsers, topology
from agent_nettools.fixtures import load_fixture_evidence
from agent_nettools.lab import all_devices
from agent_nettools.topology import (
    NeighborhoodStatus,
    build_anomaly_report,
    derive_expected,
    evidence_neighborhood,
    find_lldp_disagreements,
    find_neighbors_not_in_inventory,
    find_zero_adjacency_devices,
    format_anomaly_report,
    update_expected_in_yaml,
)


def _evidence_by_device(monkeypatch, label: str = "t0") -> dict[str, dict]:
    set_device_environment(monkeypatch)
    return {name: load_fixture_evidence(name, label=label) for name in all_devices()}


def test_derive_expected_matches_the_fixtures(monkeypatch):
    """B-590: re-measured against the 2026-08-19 lab refresh.

    The old numbers pinned here (``P1: 2``, ``P2: 4``, ``P3: 1``, ``P4: 3``,
    ``PE1/PE3: {isis: 1/2, bgp: 1}``, ``PE2: {isis: 0, bgp: 1}``, ``PE4: {isis:
    0}``, ``RR1: {isis: 1, bgp: 4}``) came from a ``t0``/``t1`` capture that was
    itself internally inconsistent -- most files dated 2026-07-29 while a few
    in the same label directory had been refreshed live, so the fabric those
    numbers described never existed at any single instant. The 2026-08-19
    refresh recaptured every command in one pass, and the fabric it actually
    describes now is healthier and more connected than the stale one: P1/P3/P4
    each carry 5 live IS-IS adjacencies, P2 carries 4, PE1/PE2/PE4/RR1 carry 2,
    and PE3 carries 1 -- and PE4, which previously answered "% BGP instance
    'default' not active" (see the test below), now runs an active default-AF
    session to RR1 as well as its VPNv4 one.
    """

    evidence = _evidence_by_device(monkeypatch)

    derived = derive_expected(evidence)

    assert derived == {
        "P1": {"isis_adjacencies": 5},
        "P2": {"isis_adjacencies": 4},
        "P3": {"isis_adjacencies": 5},
        "P4": {"isis_adjacencies": 5},
        "PE1": {"isis_adjacencies": 2, "bgp_peers": 1},
        "PE2": {"isis_adjacencies": 2, "bgp_peers": 1},
        "PE3": {"isis_adjacencies": 1, "bgp_peers": 1},
        "PE4": {"isis_adjacencies": 2, "bgp_peers": 1},
        "RR1": {"isis_adjacencies": 2, "bgp_peers": 4},
    }


def test_devices_with_no_bgp_process_have_no_bgp_peers_key(monkeypatch):
    """P1-P4 answer '% BGP instance not active': absent, never zero.

    B-590: PE4 dropped out of this set on 2026-08-19 -- its default AF now
    runs an active session to RR1 (``show bgp summary`` no longer replies "%
    BGP instance 'default' not active"), on top of the VPNv4 session
    ``test_bgp_vpnv4_peers_corrects_the_stale_pe4_no_bgp_claim`` in
    test_netbox.py already covered. The remaining four are still P-routers
    that speak IS-IS only.
    """

    evidence = _evidence_by_device(monkeypatch)

    derived = derive_expected(evidence)

    for name in ("P1", "P2", "P3", "P4"):
        assert "bgp_peers" not in derived[name]
    assert derived["PE4"]["bgp_peers"] == 1


# --------------------------------------------------------------------------- #
# B-435 -- LLDP device IDs resolve to inventory names before comparison.
#
# These four tests asserted the *defect*. At t0 three devices ran configured
# hostnames differing from their labels (P1 = LEAF05_DHCP_SERVER, P3 =
# Lab-leaf01, PE4 = SDWAN-Edge01), and the finders compared LLDP's
# device-reported names against inventory labels -- so both ends saying the same
# thing read as a disagreement, and three of this fabric's own devices read as
# foreign. The tests were written from the same premise as the code and agreed
# with it (§0.13, the tests face).
#
# The positive cases are now synthetic, deliberately. The real fixtures no
# longer contain either anomaly, and a fix that removes the only coverage of
# behaviour that is still correct is its own defect shape -- so a genuine
# disagreement and a genuine unknown are constructed here rather than lost.
# --------------------------------------------------------------------------- #


def test_the_p1_p2_link_was_never_a_disagreement(monkeypatch):
    """OBS-103, now enforced rather than described.

    P1 reports Gi0/0/0/0 facing P2; P2 reports that same port facing
    ``LEAF05_DHCP_SERVER``, which *is* P1. Two spellings, one statement.
    """

    evidence = _evidence_by_device(monkeypatch)

    assert find_lldp_disagreements(evidence) == []


def test_the_three_hostnames_are_this_fabric_s_own_devices(monkeypatch):
    """They were reported as foreign for the life of the project."""

    evidence = _evidence_by_device(monkeypatch)

    assert find_neighbors_not_in_inventory(evidence) == {}


def test_the_hostname_map_now_carries_only_inventory_labels(monkeypatch):
    """B-590: the three renamed hosts this test used to pin are gone.

    As of the 2026-08-19 refresh, P1/P3/PE4 all report their own inventory
    label as their configured hostname (no more ``LEAF05_DHCP_SERVER`` /
    ``Lab-leaf01`` / ``SDWAN-Edge01`` -- see the B-435 section comment above
    for what those were). ``hostname_map`` built from real evidence now has
    nothing to alias: every key is just a casefolded inventory name mapping to
    itself. The behavioral contract this used to pin against real fixtures --
    that a device's *configured* hostname resolves too, not only its label --
    still exists and is still worth testing; it just has no live fixture left
    to exercise it, so ``test_a_fabricated_alias_still_resolves_both_spellings``
    below covers it synthetically, the same move already made for
    ``find_lldp_disagreements``/``find_neighbors_not_in_inventory`` in this
    file.
    """

    evidence = _evidence_by_device(monkeypatch)

    mapping = topology.hostname_map(evidence)

    assert mapping == {name.casefold(): name for name in evidence}
    assert topology.resolve_device("LEAF05_DHCP_SERVER", mapping) is None
    assert topology.resolve_device("Lab-leaf01", mapping) is None
    assert topology.resolve_device("SDWAN-Edge01", mapping) is None
    assert topology.resolve_device("P1", mapping) == "P1"
    assert topology.resolve_device("p1", mapping) == "P1"  # case-insensitive
    assert topology.resolve_device("NotAThing", mapping) is None


def test_a_fabricated_alias_still_resolves_both_spellings():
    """The companion to the test above -- without it, a regression that broke
    hostname aliasing entirely would have zero coverage now that no real
    fixture carries one."""

    evidence = _fabricated(
        {"A": [], "B": []},
        hostnames={"B": "bee"},
    )

    mapping = topology.hostname_map(evidence)

    assert topology.resolve_device("bee", mapping) == "B"
    assert topology.resolve_device("BEE", mapping) == "B"  # case-insensitive
    assert topology.resolve_device("B", mapping) == "B"  # the label still works too
    assert topology.resolve_device("A", mapping) == "A"
    assert topology.resolve_device("NotAThing", mapping) is None


def _fabricated(records_by_device, hostnames=None):
    """A minimal two-device evidence set with hand-written LLDP records."""

    hostnames = hostnames or {}
    out = {}
    for name, records in records_by_device.items():
        out[name] = {
            "facts": {"data": {"parse_status": parsers.PARSE_OK,
                               "parsed": {"meta": {"hostname": hostnames.get(name, name)},
                                          "records": []}}},
            "lldp": {"data": {"parse_status": parsers.PARSE_OK,
                              "parsed": {"meta": {}, "records": records}}},
            "isis": {"data": {"parse_status": parsers.PARSE_OK,
                              "parsed": {"meta": {}, "records": [{"x": 1}]}}},
        }
    return out


def test_a_genuine_disagreement_is_still_detected():
    """The companion. Without it the finder could return [] always and pass.

    A really is cabled to B's Gi0/0/0/1, and B reports that port facing a third
    device. Neither name needs resolving -- this is a wiring disagreement, which
    is what the class was always meant to mean.
    """

    evidence = _fabricated({
        "A": [{"local_interface": "Gi0/0/0/0", "neighbor": "B",
               "neighbor_interface": "Gi0/0/0/1"}],
        "B": [{"local_interface": "Gi0/0/0/1", "neighbor": "C",
               "neighbor_interface": "Gi0/0/0/9"}],
    })

    disagreements = find_lldp_disagreements(evidence)

    assert len(disagreements) == 1
    assert disagreements[0]["device"] == "A"
    assert disagreements[0]["claims_neighbor"] == "B"
    assert disagreements[0]["neighbor_actually_reports"] == [
        {"neighbor": "C", "local_interface": "Gi0/0/0/1"}
    ]


def test_a_disagreement_survives_a_renamed_device():
    """Resolution must not paper over a real disagreement.

    B is configured ``bee`` and A names it correctly, so the *identity*
    resolves -- and B still reports that port facing something else. The link
    is genuinely inconsistent and must still be reported.
    """

    evidence = _fabricated(
        {
            "A": [{"local_interface": "Gi0/0/0/0", "neighbor": "bee",
                   "neighbor_interface": "Gi0/0/0/1"}],
            "B": [{"local_interface": "Gi0/0/0/1", "neighbor": "C",
                   "neighbor_interface": "Gi0/0/0/9"}],
        },
        hostnames={"B": "bee"},
    )

    disagreements = find_lldp_disagreements(evidence)

    assert len(disagreements) == 1
    assert disagreements[0]["claims_neighbor"] == "B"
    assert disagreements[0]["claims_neighbor_as_reported"] == "bee"


def test_a_genuinely_unknown_neighbour_is_still_reported():
    """The companion for the other class."""

    evidence = _fabricated({
        "A": [{"local_interface": "Gi0/0/0/0", "neighbor": "some-switch",
               "neighbor_interface": "Gi1/0/1"}],
    })

    unknown = find_neighbors_not_in_inventory(evidence)

    assert set(unknown) == {"some-switch"}
    assert unknown["some-switch"] == ["A:Gi0/0/0/0"]


def test_a_device_whose_facts_did_not_parse_is_not_assumed_aligned():
    """The absence rule, applied here.

    B's ``facts`` failed, so its configured hostname is unknown. A peer naming
    ``bee`` cannot be resolved and stays *unknown* rather than being assumed to
    be B. Guessing would manufacture exactly the agreement this function exists
    to stop assuming.
    """

    evidence = _fabricated({
        "A": [{"local_interface": "Gi0/0/0/0", "neighbor": "bee",
               "neighbor_interface": "Gi0/0/0/1"}],
        "B": [],
    })
    evidence["B"]["facts"]["data"]["parse_status"] = parsers.PARSE_FAILED

    assert topology.configured_hostname(evidence["B"]) is None
    assert set(find_neighbors_not_in_inventory(evidence)) == {"bee"}


def test_no_device_has_zero_adjacencies_after_the_2026_08_19_refresh(monkeypatch):
    """B-590: PE2 and PE4 were this fabric's headline isolation case for the
    life of the project -- PE2 fully isolated (0 LLDP, 0 IS-IS), PE4 with an
    LLDP neighbor but no IS-IS adjacency. Neither is true any more: PE2 now
    carries 2 live IS-IS adjacencies (to P1 and P3, confirmed by LLDP on both)
    and PE4 carries 2 as well. The live fabric has nobody left at zero, on
    either count -- this is the correct, measured answer for what the lab
    looks like today, not a loosened assertion.

    The *detector's* ability to catch a zero-adjacency device still needs
    coverage now that no real fixture exercises it --
    ``test_the_detector_still_catches_a_synthetic_zero_adjacency_device``
    below is that coverage, built the same way B-435's synthetic guards were.
    """

    evidence = _evidence_by_device(monkeypatch)

    assert find_zero_adjacency_devices(evidence) == []


def test_the_detector_still_catches_a_synthetic_zero_adjacency_device():
    """The companion to the test above. Without it, a regression that broke
    zero-adjacency detection entirely would have no coverage now that the
    real fabric no longer has a case to catch."""

    evidence = _fabricated({
        "A": [],  # 0 LLDP neighbors, and _fabricated gives every device 1 ISIS record.
        "B": [{"local_interface": "Gi0/0/0/0", "neighbor": "A", "neighbor_interface": "Gi0/0/0/0"}],
    })
    evidence["A"]["isis"]["data"]["parsed"]["records"] = []  # A also has 0 IS-IS adjacencies

    zero = find_zero_adjacency_devices(evidence)
    by_device = {entry["device"]: entry for entry in zero}

    assert set(by_device) == {"A"}
    assert by_device["A"] == {"device": "A", "lldp_neighbors": 0, "isis_adjacencies": 0}


def test_report_surfaces_all_four_anomaly_classes(monkeypatch):
    """B-590: as of 2026-08-19 all three classes are empty on this fabric.

    B-435's two were naming artefacts, already resolved when this test was
    last touched. The third -- PE2/PE4's zero-adjacency isolation -- was the
    one anomaly that "always mattered" (the previous comment here's own
    words), and the 2026-08-19 refresh resolved that one too:
    ``test_no_device_has_zero_adjacencies_after_the_2026_08_19_refresh`` above
    measures it directly. This test's job is narrower now -- confirming the
    *report* renders an all-clean fabric correctly rather than omitting a
    section or hiding a stale count -- which is still worth pinning on its
    own, since ``format_anomaly_report`` always runs and is the only signal an
    operator gets (see its own docstring).
    """

    evidence = _evidence_by_device(monkeypatch)

    report = build_anomaly_report(evidence)

    assert report["lldp_disagreements"] == []
    assert report["neighbors_not_in_inventory"] == {}
    assert report["zero_adjacency_devices"] == []

    text = format_anomaly_report(report)
    # The report still renders every heading when its class is empty, rather
    # than omitting it -- an absent section reads as "not checked", not
    # "nothing found".
    assert "LLDP disagreements (0)" in text
    assert "LLDP neighbors not in inventory (0)" in text
    assert "Devices with zero adjacencies (0)" in text
    assert text.count("\n  none") == 3


def test_update_expected_in_yaml_writes_derived_counts(tmp_path, monkeypatch):
    evidence = _evidence_by_device(monkeypatch)
    derived = derive_expected(evidence)

    source = tmp_path / "source.yaml"
    source.write_text(
        (
            "version: 1\n"
            "defaults:\n"
            "  platform: cisco_xr\n"
            "  credential_group: lab\n"
            "  port: 22\n"
            "credential_groups:\n"
            "  lab:\n"
            "    username_env: DEVICE_USERNAME\n"
            "    password_env: DEVICE_PASSWORD\n"
            "devices:\n"
            "  - name: P1\n"
            "    mgmt_ip: 172.20.250.11\n"
            "    role: core\n"
            "    site: lab\n"
            "    expected:\n"
            "      isis_adjacencies: 999\n"  # stale value, must be overwritten
        ),
        encoding="utf-8",
    )
    out = tmp_path / "out.yaml"

    update_expected_in_yaml(source, derived, out_path=out)

    import yaml

    written = yaml.safe_load(out.read_text(encoding="utf-8"))
    # B-590: P1 now measures 5 live IS-IS adjacencies (was 2 on the stale
    # 2026-07-29 capture) -- this asserts on `derived`, computed above from
    # the same fixtures, so it tracks whatever the fabric currently is.
    assert written["devices"][0]["expected"] == {"isis_adjacencies": 5}
    # The source is untouched: reading from source, writing to out.
    original = yaml.safe_load(source.read_text(encoding="utf-8"))
    assert original["devices"][0]["expected"] == {"isis_adjacencies": 999}


def test_an_aliased_neighbour_is_not_reported_as_foreign():
    """B-435's real guard: a neighbour naming a device by its CONFIGURED
    hostname rather than its inventory label must resolve, not be reported as
    outside the inventory.

    This exists because B-435's original guard went **vacuous** on 2026-08-19.
    It pinned `test_the_three_hostnames_are_this_fabric_s_own_devices`, which
    ran against real fixtures where P1/P3/PE4 reported aliases
    (`LEAF05_DHCP_SERVER`, `Lab-leaf01`, `SDWAN-Edge01`). The fixture refresh
    found those devices now report their own labels -- so `resolve_device(n)`
    and `n in evidence_by_device` began returning the same answer for every
    row, and the mutation became undetectable. **The guard stopped protecting
    anything because the fabric changed, not because the code did** (OBS-390).

    A synthetic alias restores the distinction permanently: `bee` is B's
    configured hostname and is not a key in the evidence dict, so a version
    that compares the raw name reports B as foreign and this test fails.
    """

    evidence = _fabricated(
        {"A": [{"neighbor": "bee", "local_interface": "Gi0/0/0/0"}], "B": []},
        hostnames={"B": "bee"},
    )

    assert find_neighbors_not_in_inventory(evidence) == {}


def test_a_genuinely_foreign_neighbour_is_still_reported():
    """Anti-vacuity companion (OBS-181). The test above would also pass if the
    check were removed entirely and everything resolved. A neighbour that is
    genuinely not this fabric's must still be reported."""

    evidence = _fabricated(
        {"A": [{"neighbor": "some-other-router", "local_interface": "Gi0/0/0/0"}], "B": []},
        hostnames={"B": "bee"},
    )

    assert find_neighbors_not_in_inventory(evidence) != {}


# --------------------------------------------------------------------------- #
# B-417 -- evidence_neighborhood: relationship-aware projection over a
# bounded topology neighbourhood.
#
# The real measured case (evidence-reduction.md §5): an investigation of
# RR1's BGP session with 10.255.0.12 (PE2's router_id). Deliberately built
# from the `t0` fixture, not `broken` -- `test_a_broken_incidents_own_
# window_finds_nothing_useful` below is the direct, measured proof of why:
# PE2 reports zero adjacencies *in the window it is trying to explain*,
# because the isolation is the fault. `t0` is this fabric's committed
# stand-in for "topology evidence collected independent of the incident",
# exactly the caller obligation this resolver's own module comment states.
# --------------------------------------------------------------------------- #


def _neighborhood(monkeypatch, device, subject, depth, label="t0"):
    evidence = _evidence_by_device(monkeypatch, label=label)
    return evidence_neighborhood(evidence, device, subject, depth)


def test_depth_0_is_only_the_subjects_own_device(monkeypatch):
    n = _neighborhood(monkeypatch, "RR1", "10.255.0.12", depth=0)

    assert n.status is NeighborhoodStatus.RESOLVED
    assert n.anchor_device == "RR1"
    assert n.subject_device == "PE2"
    assert n.devices == frozenset({"PE2"})
    assert n.interfaces == ()
    assert n.depth == 0


def test_depth_1_adds_no_new_device_but_finds_the_real_session_interfaces(monkeypatch):
    """The 'genuinely empty, not accidentally' half of the report.

    Depth 1 never adds a device by construction -- it only reads the depth-0
    device's own IS-IS adjacencies. That the *device set* is unchanged from
    depth 0 is not a bug to explain away; `interfaces` being real and
    non-empty in the same result is the proof this is a designed boundary,
    not a resolver that stopped working.
    """

    n = _neighborhood(monkeypatch, "RR1", "10.255.0.12", depth=1)

    assert n.status is NeighborhoodStatus.RESOLVED
    assert n.devices == frozenset({"PE2"})  # unchanged from depth 0 -- see above
    assert set(n.interfaces) == {"Gi0/0/0/0", "Gi0/0/0/1"}
    assert {r["system_id"] for r in n.adjacency_records} == {"P1", "P3"}


def test_the_measured_case_lands_at_depth_2(monkeypatch):
    """BACKLOG.md's B-417 row and evidence-reduction.md §5, made executable.

    'The events are on PE2 and name P1/P3' -- P1 and P3 only enter the
    neighbourhood at depth 2, resolved from PE2's own IS-IS adjacency
    records through the inventory's hostname map, never invented.
    """

    n = _neighborhood(monkeypatch, "RR1", "10.255.0.12", depth=2)

    assert n.status is NeighborhoodStatus.RESOLVED
    assert n.devices == frozenset({"PE2", "P1", "P3"})
    assert n.widened is False  # this fabric's t0 capture has no real disagreement (B-590)


def test_a_broken_incidents_own_window_finds_nothing_useful(monkeypatch):
    """Direct, measured proof of this module's own caution.

    Pointed at the `broken` window instead of a topology reference: PE2
    reports zero IS-IS adjacencies *in that window*, because the isolation
    under investigation is exactly what broke them. Depth 2 over the
    incident's own evidence finds nobody -- not P1, not P3 -- which is why
    the resolver's contract requires topology evidence independent of the
    window it is explaining, not a subtler bug in the resolver itself.
    """

    n = _neighborhood(monkeypatch, "RR1", "10.255.0.12", depth=2, label="broken")

    assert n.status is NeighborhoodStatus.RESOLVED  # isis parsed cleanly -- genuinely isolated, not unread
    assert n.devices == frozenset({"PE2"})


def test_subject_matching_no_router_id_is_unresolved_not_misattributed(monkeypatch):
    """A peer-address-shaped subject that owns no device must not fall back to
    the local device -- that would be exactly the wrong-device reading Q-013
    exists to prevent, dressed up as a neighbourhood instead of a rung."""

    n = _neighborhood(monkeypatch, "RR1", "10.255.0.99", depth=2)

    assert n.status is NeighborhoodStatus.SUBJECT_UNRESOLVED
    assert n.subject_device is None
    assert n.devices == frozenset()


def test_local_shaped_subject_defaults_to_the_anchor_device(monkeypatch):
    """A subject that is not IPv4-shaped is already local to `device` --
    `isis_adjacency`/`ldp_session`'s own vocabulary -- so depth 0 is the
    anchor device itself, no inventory lookup involved."""

    n = _neighborhood(monkeypatch, "PE2", "Gi0/0/0/0", depth=0)

    assert n.status is NeighborhoodStatus.RESOLVED
    assert n.subject_device == "PE2"
    assert n.devices == frozenset({"PE2"})


def test_local_shape_depth_1_is_genuinely_empty_for_a_real_isis_gap(monkeypatch):
    """B-496 on the real `isis-broken` fixture: PE3's Gi0/0/0/0 is IS-IS
    enabled but has no IPv4 address, so no adjacency can form. Depth 1 must
    report that as a real, resolved fact -- interfaces=() -- not as
    `TOPOLOGY_UNAVAILABLE`, which would wrongly suggest the evidence itself
    could not be read."""

    n = _neighborhood(monkeypatch, "PE3", "Gi0/0/0/0", depth=1, label="isis-broken")

    assert n.status is NeighborhoodStatus.RESOLVED
    assert n.devices == frozenset({"PE3"})
    assert n.interfaces == ()


def test_an_lldp_only_neighbor_widens_rather_than_being_dropped(monkeypatch):
    """The companion to the test above, same real fixture, one depth further.

    IS-IS names nobody on PE3's Gi0/0/0/0 (the gap above); LLDP names P2.
    Scoping the depth-2 check to IS-IS's own interface list would never look
    at this port and P2 would be silently dropped -- exactly what 'derive
    from declared topology, but widen rather than pick a side' forbids.
    """

    n = _neighborhood(monkeypatch, "PE3", "Gi0/0/0/0", depth=2, label="isis-broken")

    assert n.status is NeighborhoodStatus.RESOLVED
    assert n.devices == frozenset({"PE3", "P2"})
    assert n.widened is True
    assert len(n.disagreements) == 1
    disagreement = n.disagreements[0]
    assert disagreement["kind"] == "isis_lldp_mismatch"
    assert disagreement["isis_neighbor"] is None
    assert disagreement["lldp_neighbor"] == "P2"


def test_unknown_anchor_device_refuses_rather_than_guessing(monkeypatch):
    evidence = _evidence_by_device(monkeypatch)

    with pytest.raises(ValueError, match="no evidence"):
        evidence_neighborhood(evidence, "NOT-A-DEVICE", "10.255.0.12", 1)


def test_negative_depth_is_refused(monkeypatch):
    evidence = _evidence_by_device(monkeypatch)

    with pytest.raises(ValueError, match="non-negative"):
        evidence_neighborhood(evidence, "RR1", "10.255.0.12", -1)


def test_isis_parse_failure_is_topology_unavailable_not_empty():
    """The third absence state (OBS-181's house rule, restated for this
    resolver): a section that failed to parse must not read the same as one
    that parsed cleanly and found nothing. Companion to
    `test_local_shape_depth_1_is_genuinely_empty_for_a_real_isis_gap`, which
    pins the other half of the same distinction."""

    evidence = _fabricated({"A": [], "B": []})
    evidence["A"]["isis"]["data"]["parse_status"] = parsers.PARSE_FAILED

    n = evidence_neighborhood(evidence, "A", "Gi0/0/0/0", depth=1)

    assert n.status is NeighborhoodStatus.TOPOLOGY_UNAVAILABLE
    assert n.devices == frozenset({"A"})  # the device itself still resolved -- only its topology did not
    assert n.interfaces == ()


def test_a_resolver_that_always_returns_empty_fails_this_suite(monkeypatch):
    """The explicit positive control (OBS-181): a stub that always reports
    an empty neighbourhood must disagree with, and so fail, the assertions
    the real measured-case tests above make -- proving those tests would
    actually notice a resolver that stopped finding anything."""

    def always_empty(evidence_by_device, device, subject, depth):
        # Same shape a broken resolver could plausibly produce: it runs, it
        # returns a `Neighborhood`, and it has silently forgotten every
        # device beyond the subject's own.
        real = evidence_neighborhood(evidence_by_device, device, subject, depth)
        return replace(real, devices=frozenset({real.subject_device} if real.subject_device else ()))

    evidence = _evidence_by_device(monkeypatch)

    real = evidence_neighborhood(evidence, "RR1", "10.255.0.12", depth=2)
    stub = always_empty(evidence, "RR1", "10.255.0.12", depth=2)

    assert real.devices == frozenset({"PE2", "P1", "P3"})  # what the tests above pin
    assert stub.devices != real.devices  # the stub would fail that same assertion
    with pytest.raises(AssertionError):
        assert stub.devices == frozenset({"PE2", "P1", "P3"})


def _isis_lldp_mismatch_evidence():
    """Two devices, device-wide (peer-address) shape: IS-IS says A's
    Gi0/0/0/0 faces B; LLDP on the same port says it faces C instead. Neither
    is invented -- both are real records in this fabricated evidence, and the
    resolver must keep both rather than choosing one silently."""

    evidence = _fabricated(
        {
            "A": [{"local_interface": "Gi0/0/0/0", "neighbor": "C", "neighbor_interface": "Gi0/0/0/0"}],
            "B": [],
            "C": [],
        }
    )
    evidence["A"]["isis"]["data"]["parsed"]["records"] = [
        {"system_id": "B", "interface": "Gi0/0/0/0"}
    ]
    return evidence


def test_device_wide_isis_lldp_mismatch_unions_both_candidates(monkeypatch):
    """The device-wide (peer-address) counterpart to the real isis-broken
    proof above, where both sources name *something*, and disagree."""

    from agent_nettools import inventory_model

    evidence = _isis_lldp_mismatch_evidence()

    class _FakeDevice:
        name = "A"
        router_id = "10.9.9.9"

    class _FakeInventory:
        devices = [_FakeDevice()]

    monkeypatch.setattr(inventory_model, "load_inventory_file", lambda *a, **k: _FakeInventory())

    n = evidence_neighborhood(evidence, "A", "10.9.9.9", depth=2)

    assert n.status is NeighborhoodStatus.RESOLVED
    assert n.devices == frozenset({"A", "B", "C"})  # both IS-IS's and LLDP's candidate, neither dropped
    assert n.widened is True
    mismatches = [d for d in n.disagreements if d["kind"] == "isis_lldp_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0]["isis_neighbor"] == "B"
    assert mismatches[0]["lldp_neighbor"] == "C"
